from fastapi import FastAPI

from gateway.routes import metric_catalog

app = FastAPI(title="Greenhouse gateway (read-only)")
app.include_router(metric_catalog.router)
