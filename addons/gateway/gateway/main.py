from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway import addon, config, middleware
from gateway.jobs import helper_snapshot
from gateway.jobs.scheduler import start_scheduler
from gateway.metric_catalog.runner import run_from_env
from gateway.routes import calls, ha, metric_catalog, vm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    addon.apply_options()
    cron = os.environ.get("METRIC_CATALOG_CRON")
    snapshot = config.snapshot_enabled() and bool(config.snapshot_patterns())
    sched = start_scheduler(
        cron, run_from_env,
        snapshot_cron=config.snapshot_cron() if snapshot else None,
        run_snapshot=helper_snapshot.run_from_env if snapshot else None,
    ) if cron else None
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="Greenhouse gateway (read-only)", lifespan=lifespan)
middleware.install(app)
app.include_router(metric_catalog.router)
app.include_router(ha.router)
app.include_router(vm.router)
app.include_router(calls.router)


@app.get("/health")
def health():
    return {"status": "ok"}
