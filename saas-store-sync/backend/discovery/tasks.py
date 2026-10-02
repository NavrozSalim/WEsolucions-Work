from celery import shared_task

from .engine import execute_job


@shared_task(name='discovery.tasks.run_discovery_job', bind=True, ignore_result=True)
def run_discovery_job(self, job_id: str):
    execute_job(job_id)
