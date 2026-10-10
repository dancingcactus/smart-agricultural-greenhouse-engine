#!/bin/bash
# Start Open WebUI as a Home Assistant add-on: options -> environment, then the image's own start script.
set -e

mkdir -p /data/webui
# If the options are missing or incomplete this prints the reason and stops the add-on.
eval "$(python3 /options_to_env.py /data/options.json)" || exit 1

cd /app/backend
exec bash start.sh
