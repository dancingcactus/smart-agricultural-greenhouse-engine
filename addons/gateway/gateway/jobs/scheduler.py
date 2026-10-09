from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)


def start_scheduler(metric_catalog_cron: str, run_catalog, snapshot_cron: str | None = None,
                    run_snapshot=None, extra_jobs: dict | None = None) -> BackgroundScheduler:
    """Run `run_catalog()` on the cron expression and once at startup.

    Failures are logged (and re-raised into the job-error log) rather than killing the scheduler.
    """
    def guarded() -> None:
        try:
            log.info("metric catalog run: %s", run_catalog())
        except Exception as exc:
            log.error("metric catalog run failed: %s", exc, exc_info=not isinstance(exc, RuntimeError))

    sched = BackgroundScheduler()
    sched.add_job(guarded, CronTrigger.from_crontab(metric_catalog_cron), id="metric_catalog",
                  coalesce=True, max_instances=1)
    sched.add_job(guarded, id="metric_catalog_startup")  # fires immediately
    if snapshot_cron and run_snapshot:
        def guarded_snapshot() -> None:
            try:
                log.info("helper snapshot: %s", run_snapshot())
            except Exception as exc:  # noqa: BLE001 - a failed run must not stop the scheduler
                log.error("helper snapshot failed: %s", exc)

        sched.add_job(guarded_snapshot, CronTrigger.from_crontab(snapshot_cron), id="helper_snapshot",
                      coalesce=True, max_instances=1)
        sched.add_job(guarded_snapshot, id="helper_snapshot_startup")
    for job_id, (cron, fn) in (extra_jobs or {}).items():
        def guarded_extra(job_id=job_id, fn=fn) -> None:
            try:
                log.info("%s: %s", job_id, fn())
            except Exception as exc:  # noqa: BLE001 - a failed run must not stop the scheduler
                log.error("%s failed: %s", job_id, exc)

        sched.add_job(guarded_extra, CronTrigger.from_crontab(cron), id=job_id, coalesce=True, max_instances=1)
        sched.add_job(guarded_extra, id=f"{job_id}_startup")
    sched.start()
    return sched
