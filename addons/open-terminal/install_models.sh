#!/bin/sh
# Install Chronos-2, LightGBM and CPU PyTorch while the build still has internet. Never fails the build:
# on any error the full output is kept in /opt/models/INSTALL_FAILED.log instead. The steps run in a subshell so that
# an `exit 1` ends only them, not this script (a brace group would exit before the log is saved).
LOG=/tmp/install_models.log
MARK=/tmp/pip_bootstrapped
mkdir -p /opt/models
(
    set -x
    id
    python3 --version
    # The base image ships Python without pip. Bootstrap it for the build only (removed again below, so the
    # sandbox stays as locked down as before).
    if ! python3 -m pip --version; then
        touch "$MARK"
        python3 -m ensurepip --default-pip || {
            python3 -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')" \
                && python3 /tmp/get-pip.py --no-cache-dir
        } || exit 1
        python3 -m pip --version || exit 1
    fi
    # LightGBM needs the OpenMP runtime (libgomp). Best effort: the base image may have no apt-get.
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
    fi
    python3 -m pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        torch "chronos-forecasting>=2.3,<3" "lightgbm>=4,<5" numpy pandas huggingface_hub || exit 1
    HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 python3 -c "from huggingface_hub import snapshot_download; snapshot_download('amazon/chronos-2', revision='${CHRONOS_REVISION:-main}', local_dir='/opt/models/chronos-2')" || exit 1
    python3 -c "import torch, lightgbm, pandas, numpy; from chronos import Chronos2Pipeline" || exit 1
) >"$LOG" 2>&1
STATUS=$?
if [ -f "$MARK" ]; then
    python3 -m pip uninstall -y pip >>"$LOG" 2>&1 || true  # restore the base image's lack of pip
    rm -f "$MARK" /tmp/get-pip.py
fi
if [ $STATUS -ne 0 ]; then
    cp "$LOG" /opt/models/INSTALL_FAILED.log
    echo "Model install failed; see /opt/models/INSTALL_FAILED.log in the image" >&2
    tail -n 40 "$LOG" >&2
fi
exit 0
