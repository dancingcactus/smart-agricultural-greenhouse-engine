#!/usr/bin/with-contenv bashio
bashio::log.info "Starting Greenhouse gateway (read-only)"
exec python3 -m uvicorn gateway.main:app --host 0.0.0.0 --port 8099
