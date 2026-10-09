from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)


def start_scheduler(metric_catalog_cron: str, run_catalog) -> BackgroundScheduler:
    """Run `run_catalog()` on the cron expression and once at startup.

    Failures are logged (and re-raised into the job-error log) rather than killing the scheduler.
    """
    def guarded() -> None:
        try:
            log.info("metric catalog run: %s", run_catalog())
        except Exception:
            log.exception("metric catalog run failed")

    sched = BackgroundScheduler()
    sched.add_job(guarded, CronTrigger.from_crontab(metric_catalog_cron), id="metric_catalog",
                  coalesce=True, max_instances=1)
    sched.add_job(guarded, id="metric_catalog_startup")  # fires immediately
    sched.start()
    return sched
