"""Renderer occupancy covers cancellation/timeout drains, not just job status."""
import asyncio
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import update

from app.config import settings
from app.models.catalog_export import CatalogExportJob
from app.services import catalog_export_service as jobs
from app.tasks import catalog_export_worker as worker
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'catalog_export_dir', str(tmp_path))
    monkeypatch.setattr(worker, 'async_session', TestSession)
    monkeypatch.setattr(worker, '_local_renderer_lock', asyncio.Lock())


async def new_job():
    async with TestSession() as session:
        job, _ = await jobs.create_job(session, {'family_codes': ['ZZ-9001']}, 'occupancy-owner')
        return job.id


async def state(job_id):
    async with TestSession() as session:
        job = await session.get(CatalogExportJob, job_id)
        return job.status, job.cancel_requested


async def wait_for(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 3)


@pytest.mark.asyncio
async def test_pending_cancel_is_immediate_and_running_cancel_renews(client, monkeypatch):
    monkeypatch.setattr(settings, 'catalog_export_cooldown_seconds', 0)
    job_id = await new_job()
    async with TestSession() as session:
        job = await session.get(CatalogExportJob, job_id)
        returned = await jobs.cancel_job(session, job)
        assert returned.status == 'cancelled'
        assert returned.cancel_requested
    assert not await worker.CatalogExportWorker().run_once()

    job_id = await new_job()
    async with TestSession() as session:
        job = await jobs.claim_job(session, 'owner')
        assert job.id == job_id
        await jobs.cancel_job(session, job)
        assert job.status == 'running'
        assert job.cancel_requested
        assert await jobs.update_job(session, job_id, 'owner', lease_until=jobs.now() + timedelta(seconds=120))
        assert not await jobs.update_job(session, job_id, 'owner', progress=1)
        assert not await jobs.update_job(session, job_id, 'owner', status='ready')
        assert not await jobs.update_job(session, job_id, 'other', status='cancelled')


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [True, False], ids=['cancel', 'timeout'])
async def test_thread_and_cleanup_drain_keep_occupancy_and_heartbeat(client, monkeypatch, cancel):
    job_id = await new_job()
    started, release = threading.Event(), threading.Event()
    cleaning, finish_cleanup = threading.Event(), threading.Event()
    steps, renewals = [], []
    original_rmtree = worker.shutil.rmtree

    def render():
        started.set()
        assert release.wait(5), 'test failed to release renderer'

    def cleanup(*args):
        cleaning.set()
        assert finish_cleanup.wait(5), 'test failed to release cleanup'
        return original_rmtree(*args)

    async def process(self, job, directory):
        await self.work(job, render)
        steps.append('second step')
        await self.work(job, lambda: steps.append('second render'))

    async def heartbeat(self, job):
        while True:
            await self.update(job, lease_until=jobs.now() + timedelta(seconds=120))
            renewals.append(True)
            await asyncio.sleep(0.02)

    monkeypatch.setattr(worker.CatalogExportWorker, 'process', process)
    monkeypatch.setattr(worker.CatalogExportWorker, 'heartbeat', heartbeat)
    monkeypatch.setattr(worker.shutil, 'rmtree', cleanup)
    monkeypatch.setattr(settings, 'catalog_export_job_seconds', 2 if cancel else 0.1)
    task = asyncio.create_task(worker.CatalogExportWorker().run_once())
    try:
        await wait_for(started.is_set)
        if cancel:
            async with TestSession() as session:
                await jobs.cancel_job(session, await session.get(CatalogExportJob, job_id))
        await asyncio.sleep(0.35)
        assert (await state(job_id))[0] == 'running'
        assert not await worker.CatalogExportWorker().run_once()
        count = len(renewals)
        await wait_for(lambda: len(renewals) > count)
        release.set()
        await wait_for(cleaning.is_set)
        assert (await state(job_id))[0] == 'running'
        assert not await worker.CatalogExportWorker().run_once()
        count = len(renewals)
        await wait_for(lambda: len(renewals) > count)
        finish_cleanup.set()
        assert await asyncio.wait_for(task, 3)
        assert (await state(job_id))[0] == ('cancelled' if cancel else 'failed')
        assert not steps
        async with worker.renderer_occupancy() as acquired:
            assert acquired
    finally:
        release.set()
        finish_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_lost_lease_cannot_publish_terminal_after_drain(client):
    job_id = await new_job()
    async with TestSession() as session:
        job = await jobs.claim_job(session, 'old')
        await session.execute(update(CatalogExportJob).where(CatalogExportJob.id == job_id).values(
            lease_until=jobs.now() - timedelta(seconds=1)))
        await session.commit()
        assert not await jobs.update_job(session, job.id, 'old', lease_until=jobs.now() + timedelta(seconds=120))
        assert not await jobs.update_job(session, job.id, 'old', status='failed')
        assert not await jobs.update_job(session, job.id, 'old', status='cancelled')


@pytest.mark.asyncio
@pytest.mark.parametrize('acquired,unlock_fails', [(True, False), (False, False), (True, True)])
async def test_postgres_occupancy_uses_dedicated_session_lock(monkeypatch, acquired, unlock_fails):
    connection = AsyncMock()
    connection.scalar.return_value = acquired
    if unlock_fails:
        connection.execute.side_effect = OSError('connection lost')
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=False)
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),
                             connect=MagicMock(return_value=connection_context))
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=SimpleNamespace(bind=engine))
    session_context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(worker, 'async_session', MagicMock(return_value=session_context))

    async def attempt():
        async with worker.renderer_occupancy() as held:
            assert held is acquired
            connection.execute.assert_not_awaited()

    if unlock_fails:
        with pytest.raises(OSError):
            await attempt()
        connection.invalidate.assert_awaited_once()
    else:
        await attempt()
        connection.invalidate.assert_not_awaited()
    sql, parameters = connection.scalar.await_args.args
    assert 'pg_try_advisory_lock' in str(sql)
    assert parameters == {'key': 73420713}
    if acquired:
        assert 'pg_advisory_unlock' in str(connection.execute.await_args.args[0])
    else:
        connection.execute.assert_not_awaited()
