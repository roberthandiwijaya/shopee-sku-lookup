"""Independent durable PDF worker. Run with python -m app.tasks.catalog_export_worker."""
import argparse
import asyncio
import logging
import shutil
import signal
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from pypdf import PdfWriter

from app.config import settings
from app.database import async_session
from app.services import catalog_export_service as jobs
from app.services import catalog_pdf_service as pdf

logger = logging.getLogger(__name__)


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
            await disk_work(self.check_disk)
            images = await pdf.fetch_catalog_images([card])
            valid_photo = await disk_work(pdf._normalized_photo, images.get(card['primary_image_url']))
            if valid_photo is None:
                warnings.append(f"{card['family_code']}: photo unavailable.")
                images = {}
            else:
                images = {card['primary_image_url']: valid_photo.getvalue()}
                valid_photo.close()
            # Decode/normalize and render one family at a time; original photos never accumulate.
            data = await disk_work(pdf.render_catalog_pdf, [card], images=images,
                show_stock=options['show_stock'], show_prices=options.get('show_prices', True),
                stock_mode=options['stock_mode'],
                min_stock=options['min_stock'], snapshot_at=snapshot, omitted=omitted)
            path = directory / f'part-{index:03}.pdf'
            await disk_work(path.write_bytes, data)
            parts.append(path)
            del data, images
            await self.update(job, progress=index + 1)
        outputs = []
        step = 50 if options['output_mode'] == 'split' else len(parts)
        for index, offset in enumerate(range(0, len(parts), step)):
            await self.update(job, progress=len(cards))
            name = f'catalog-{index+1:02}.pdf' if options['output_mode'] == 'split' else 'catalog.pdf'
            destination = directory / name
            await disk_work(self.merge, parts[offset:offset+step], destination)
            outputs.append(destination)
            await disk_work(self.check_disk)
        if options['output_mode'] == 'split':
            def archive():
                destination = directory / 'catalog-volumes.zip'
                with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_STORED) as bundle:
                    for path in outputs:
                        bundle.write(path, path.name)
                return destination
            outputs.append(await disk_work(archive))
        for path in parts:
            path.unlink(missing_ok=True)
        await disk_work(self.check_disk)
        result = {'directory': str(directory.relative_to(Path(settings.catalog_export_dir))),
                  'files': [{'name': path.name, 'size': path.stat().st_size} for path in outputs], 'omitted': omitted}
        await self.update(job, status='ready', result=result, warnings=warnings,
                          expires_at=jobs.now() + timedelta(hours=settings.catalog_export_retention_hours))

    async def run_once(self):
        async with async_session() as session:
            await jobs.cleanup_jobs(session)
            job = await jobs.claim_job(session, self.worker_id)
        if not job:
            return False
        directory = jobs.safe_directory(f'{job.id}/{uuid.uuid4().hex}')
        directory.mkdir(parents=True)
        heartbeat = asyncio.create_task(self.heartbeat(job))
        processing = asyncio.create_task(self.process(job, directory))
        success = False
        try:
            done, _ = await asyncio.wait([processing, heartbeat], timeout=settings.catalog_export_job_seconds,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise TimeoutError()
            if heartbeat in done:
                heartbeat.result()
                raise LeaseLost()
            processing.result()
            success = True
        except LeaseLost:
            logger.info('Catalog job cancelled or reclaimed: %s', job.id)
        except Exception as exc:
            logger.exception('Catalog export failed: %s', job.id)
            message = str(exc) if isinstance(exc, ValueError) else 'Export could not be completed. Please retry with fewer families.'
            try:
                await self.update(job, status='failed', error=message)
            except LeaseLost:
                pass
        finally:
            # to_thread work cannot be killed: wait for current bounded family/merge before deleting its files.
            heartbeat.cancel()
            if not processing.done():
                processing.cancel()
            await asyncio.gather(heartbeat, processing, return_exceptions=True)
            if not success:
                await disk_work(shutil.rmtree, directory, True)
        return True

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
