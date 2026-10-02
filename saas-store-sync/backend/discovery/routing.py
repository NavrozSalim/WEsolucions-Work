"""Route discovery jobs onto the US or AU scrape servers.

Local Docker runs one worker per queue. Production should do the same on the
US VPS (``-Q discover-us``) and the AU VPS (``-Q discover-au``), separate from
catalog price workers on ``heavy-us`` / ``heavy-au``.
"""

from __future__ import annotations

import logging
from typing import Any

from .columns import region_for

logger = logging.getLogger(__name__)

QUEUE_DISCOVER_US = 'discover-us'
QUEUE_DISCOVER_AU = 'discover-au'
TASK_NAME = 'discovery.tasks.run_discovery_job'


def queue_for_marketplace(marketplace: str) -> str:
    if region_for(marketplace) == 'AU':
        return QUEUE_DISCOVER_AU
    return QUEUE_DISCOVER_US


class DiscoveryTaskRouter:
    """Send ``run_discovery_job`` to discover-us or discover-au from the job row."""

    def route_for_task(
        self,
        name: str,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        *,
        task=None,
        **kw: Any,
    ) -> dict[str, str] | None:
        if name != TASK_NAME:
            return None
        args = args or ()
        kwargs = kwargs or {}
        job_id = args[0] if args else kwargs.get('job_id')
        if not job_id:
            logger.warning('discovery route: missing job id; using %s', QUEUE_DISCOVER_US)
            return {'queue': QUEUE_DISCOVER_US}
        try:
            from discovery.models import DiscoveryJob

            marketplace = DiscoveryJob.objects.values_list('marketplace', flat=True).get(id=job_id)
        except Exception as exc:
            logger.exception(
                'discovery route: could not resolve %s; using %s: %s',
                job_id,
                QUEUE_DISCOVER_US,
                exc,
            )
            return {'queue': QUEUE_DISCOVER_US}
        return {'queue': queue_for_marketplace(marketplace)}
