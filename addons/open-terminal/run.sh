#!/bin/sh
# Start Open Terminal as a Home Assistant add-on: options -> environment, persistent home, then the
# image's own hardened entrypoint (which builds the egress firewall from OPEN_TERMINAL_ALLOWED_DOMAINS).
set -e

# If the options are missing or unsafe this prints the reason and stops the add-on.
eval "$(python3 /options_to_env.py /data/options.json)" || exit 1

# The agent's home holds its tools and proposals repositories; keep it in add-on storage so Home
# Assistant backups include it and it survives updates. Owned by the app user (uid 1000) up front,
# because the entrypoint's ownership fix does not follow the symlink.
if [ ! -d /data/home ]; then
    mkdir -p /data/home
    cp -a /home/user/. /data/home/ 2>/dev/null || true
fi
mkdir -p /data/home/.local/bin
[ -f /data/home/.bashrc ] || cp /etc/skel/.bashrc /data/home/.bashrc 2>/dev/null || true
[ -f /data/home/.profile ] || cp /etc/skel/.profile /data/home/.profile 2>/dev/null || true
chown -R 1000:1000 /data/home
rm -rf /home/user
ln -s /data/home /home/user

if [ -f /opt/models/INSTALL_FAILED.log ]; then
    echo "Greenhouse sandbox: the forecasting models did not install when this image was built. Last lines:" >&2
    tail -n 25 /opt/models/INSTALL_FAILED.log >&2
fi
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

echo "Greenhouse sandbox: outbound limited to ${OPEN_TERMINAL_ALLOWED_DOMAINS}"
exec /app/entrypoint-slim.sh "$@"
