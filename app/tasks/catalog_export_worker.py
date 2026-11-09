"""Independent durable PDF worker. Run with python -m app.tasks.catalog_export_worker."""
import argparse
import asyncio
import logging
import shutil
import signal
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, text

from app.models.catalog_export import CatalogExportJob

from pypdf import PdfWriter

from app.config import settings
from app.database import async_session
from app.services import catalog_export_service as jobs
from app.services import catalog_pdf_service as pdf

logger = logging.getLogger(__name__)

# Separate from the admission transaction mutex. SQLite is only a test/local
# fallback; production occupancy is shared by all workers via PostgreSQL.
RENDERER_LOCK_KEY = 73420713
_local_renderer_lock = asyncio.Lock()


@asynccontextmanager
async def renderer_occupancy():
    async with async_session() as session:
        bind = session.bind
    if bind.dialect.name != 'postgresql':
        if _local_renderer_lock.locked():
            yield False
            return
        async with _local_renderer_lock:
            yield True
        return
    async with bind.connect() as connection:
        acquired = False
        try:
            acquired = bool(await connection.scalar(text(
                'SELECT pg_try_advisory_lock(:key)'), {'key': RENDERER_LOCK_KEY}))
            await connection.commit()
            yield acquired
        except BaseException:
            # Acquisition itself can fail after PostgreSQL took the lock.
            # Invalidate rather than returning an uncertain session to pooling.
            if not acquired:
                await connection.invalidate()
            raise
        finally:
            if acquired:
                try:
                    await connection.execute(text('SELECT pg_advisory_unlock(:key)'),
                                             {'key': RENDERER_LOCK_KEY})
                    await connection.commit()
                except BaseException:
                    # Never put a possibly still-locked session back in the pool.
                    await connection.invalidate()
                    raise
    # Limitation: a database disconnect can release this server-side lock while
    # a Python thread is still draining. Leases fence publication, but cannot
    # guarantee cross-process renderer occupancy during a DB/session failure.


async def disk_work(function, *args, **kwargs):
    """On cancellation join the current bounded thread before removing its files."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class LeaseLost(Exception):
    pass


class CancellationRequested(Exception):
    pass


class CatalogExportWorker:
    def __init__(self):
        self.worker_id = uuid.uuid4().hex
        self.stopping = False

    async def update(self, job, **values):
        async with async_session() as session:
            if not await jobs.update_job(session, job.id, self.worker_id, **values):
                raise LeaseLost()

    async def heartbeat(self, job):
        while True:
            await asyncio.sleep(30)
            await self.update(job, lease_until=jobs.now() + timedelta(seconds=120))

    async def cancellation_requested(self, job):
        async def read_flag():
            async with async_session() as session:
                return bool(await session.scalar(select(CatalogExportJob.cancel_requested).where(
                    CatalogExportJob.id == job.id)))
        # Cancellation of a watcher must not abandon an in-flight database read
        # or its connection cleanup (notably SQLite's threaded driver in tests).
        reading = asyncio.create_task(read_flag())
        try:
            return await asyncio.shield(reading)
        except asyncio.CancelledError:
            await asyncio.gather(reading, return_exceptions=True)
            raise

    async def check_cancellation(self, job):
        if await self.cancellation_requested(job):
            raise CancellationRequested()

    async def watch_cancellation(self, job):
        while True:
            await self.check_cancellation(job)
            await asyncio.sleep(0.25)

    async def work(self, job, function, *args, **kwargs):
        await self.check_cancellation(job)
        return await disk_work(function, *args, **kwargs)

    def check_disk(self):
        root = Path(settings.catalog_export_dir)
        size = sum(path.stat().st_size for path in root.rglob('*') if path.is_file())
        if size > settings.catalog_export_max_bytes or shutil.disk_usage(root).free < 100_000_000:
            raise ValueError("Export storage is full. Try again after old exports expire.")

    @staticmethod
    def merge(paths, destination):
        writer = PdfWriter()
        try:
            for path in paths:
                # append imports and remaps each family's outline destination to
                # its actual page in this volume, including long continuations.
                # Source PDFs deliberately reuse local bookmark keys; do not
                # rebuild destinations from those keys or from family counts.
                writer.append(str(path), import_outline=True)
            writer.page_mode = '/UseOutlines'
            with destination.open('wb') as stream:
                writer.write(stream)
        finally:
            writer.close()

    async def process(self, job, directory):
        options = job.options
        snapshot = jobs.now()
        async with async_session() as session:
            # A coherent catalog snapshot; materialize all selected rows before slow image work.
            if session.bind.dialect.name == 'postgresql':
                from sqlalchemy import text
                await session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            cards, omitted = await pdf.get_export_cards(session, options['family_codes'],
                stock_mode=options['stock_mode'], min_stock=options['min_stock'], max_families=250, max_rows=25000)
        await self.update(job, snapshot_at=snapshot, total=len(cards))
        warnings, parts = [], []
        for index, card in enumerate(cards):
            await self.update(job, progress=index)
            await self.work(job,self.check_disk)
            images = await pdf.fetch_catalog_images([card])
            valid_photo = await self.work(job,pdf._normalized_photo, images.get(card['primary_image_url']))
            if valid_photo is None:
                warnings.append(f"{card['family_code']}: photo unavailable.")
                images = {}
            else:
                images = {card['primary_image_url']: valid_photo.getvalue()}
                valid_photo.close()
            # Decode/normalize and render one family at a time; original photos never accumulate.
            data = await self.work(job,pdf.render_catalog_pdf, [card], images=images,
                show_stock=options['show_stock'], show_prices=options.get('show_prices', True),
                stock_mode=options['stock_mode'],
                min_stock=options['min_stock'], snapshot_at=snapshot, omitted=omitted)
            path = directory / f'part-{index:03}.pdf'
            await self.work(job,path.write_bytes, data)
            parts.append(path)
            del data, images
            await self.update(job, progress=index + 1)
        outputs = []
        step = 50 if options['output_mode'] == 'split' else len(parts)
        for index, offset in enumerate(range(0, len(parts), step)):
            await self.update(job, progress=len(cards))
            name = f'catalog-{index+1:02}.pdf' if options['output_mode'] == 'split' else 'catalog.pdf'
            destination = directory / name
            await self.work(job,self.merge, parts[offset:offset+step], destination)
            outputs.append(destination)
            await self.work(job,self.check_disk)
        if options['output_mode'] == 'split':
            def archive():
                destination = directory / 'catalog-volumes.zip'
                with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED) as bundle:
                    for path in outputs:
                        bundle.write(path, path.name)
                return destination
            outputs.append(await self.work(job,archive))
        for path in parts:
            path.unlink(missing_ok=True)
        await self.work(job,self.check_disk)
        result = {'directory': str(directory.relative_to(Path(settings.catalog_export_dir))),
                  'files': [{'name': path.name, 'size': path.stat().st_size} for path in outputs], 'omitted': omitted}
        await self.update(job, status='ready', result=result, warnings=warnings,
                          expires_at=jobs.now() + timedelta(hours=settings.catalog_export_retention_hours))

    async def run_once(self):
        async with renderer_occupancy() as acquired:
            if not acquired:
                return False
            async with async_session() as session:
                await jobs.cleanup_jobs(session)
                job = await jobs.claim_job(session, self.worker_id)
            if not job:
                return False
            await self.run_claimed(job)
            return True

    async def run_claimed(self, job):
        directory = jobs.safe_directory(f'{job.id}/{uuid.uuid4().hex}')
        heartbeat = asyncio.create_task(self.heartbeat(job))
        cancellation = asyncio.create_task(self.watch_cancellation(job))
        processing = None
        success = False
        failure = None
        cancelled = False
        try:
            directory.mkdir(parents=True)
            processing = asyncio.create_task(self.process(job, directory))
            done, _ = await asyncio.wait([processing, heartbeat, cancellation],
                timeout=settings.catalog_export_job_seconds, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise TimeoutError()
            if heartbeat in done:
                heartbeat.result()
                raise LeaseLost()
            if cancellation in done:
                cancellation.result()
            processing.result()
            success = True
        except CancellationRequested:
            cancelled = True
        except LeaseLost:
            # A progress/publication update can lose its fence because a
            # cancellation just arrived; resolve that after the thread drains.
            logger.info('Catalog job cancelled or reclaimed: %s', job.id)
        except Exception as exc:
            logger.exception('Catalog export failed: %s', job.id)
            failure = str(exc) if isinstance(exc, ValueError) else 'Export could not be completed. Please retry with fewer families.'
        finally:
            cancellation.cancel()
            if processing and not processing.done():
                processing.cancel()
            # Keep heartbeat alive until *all* threads, including deletion,
            # finish. Never publish terminal status before this drain.
            try:
                await asyncio.gather(cancellation, *([processing] if processing else []),
                                     return_exceptions=True)
                if not success:
                    await disk_work(shutil.rmtree, directory, True)
                    cancelled = cancelled or await self.cancellation_requested(job)
                    # Renew after cleanup as well as checking the existing lease:
                    # a failed heartbeat must not resurrect a lost lease.
                    if not heartbeat.done():
                        await self.update(job, lease_until=jobs.now() + timedelta(seconds=120))
                        if cancelled:
                            await self.update(job, status='cancelled')
                        elif failure:
                            await self.update(job, status='failed', error=failure)
            except LeaseLost:
                pass
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def run(self, once=False):
        while not self.stopping:
            try:
                processed = await self.run_once()
            except Exception:
                logger.exception('Catalog worker iteration failed')
                processed = False
            if once:
                break
            await asyncio.sleep(1 if processed else 5)


async def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    worker = CatalogExportWorker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: setattr(worker, 'stopping', True))
    await worker.run(args.once)


if __name__ == '__main__':
    asyncio.run(main())
