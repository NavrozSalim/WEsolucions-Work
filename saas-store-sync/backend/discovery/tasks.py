from celery import shared_task

from .engine import execute_job
from .locks import acquire_discovery_lock, release_discovery_lock


@shared_task(
    name='discovery.tasks.run_discovery_job',
    bind=True,
    ignore_result=True,
    max_retries=None,
)
def run_discovery_job(self, job_id: str):
    owner = str(self.request.id or job_id)
    if not acquire_discovery_lock(job_id, owner):
        # Another worker is still on this job. Come back after the lock can
        # expire so a dead worker is resumed instead of scraped from the start.
        raise self.retry(countdown=300)
    try:
        execute_job(job_id, lock_owner=owner)
    finally:
        release_discovery_lock(job_id, owner)
