from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from gateway import addon, config, middleware, ui
from gateway.jobs import helper_snapshot, weather_archive
from gateway.jobs import snapshot as snapshot_job
from gateway.jobs.scheduler import start_scheduler
from gateway.metric_catalog.runner import run_from_env
from gateway.routes import calls, glossary, ha, metric_catalog, snapshot, vm, weather

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
        extra_jobs={
            **({"weather_archive": (config.weather_cron(), weather_archive.run_from_env)}
               if config.weather_entities() else {}),
            **({"config_snapshot": (config.snapshot_config_cron(), snapshot_job.run_from_env)}
               if config.snapshot_enabled_config() else {}),
        } or None,
    ) if cron else None
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="Greenhouse gateway (read-only)", lifespan=lifespan)
middleware.install(app)
app.include_router(metric_catalog.router)
app.include_router(ha.router)
app.include_router(vm.router)
app.include_router(glossary.router)
app.include_router(snapshot.router)
app.include_router(weather.router)
app.include_router(calls.router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def panel():
    return ui.PAGE


@app.get("/health")
def health():
    return {"status": "ok"}
