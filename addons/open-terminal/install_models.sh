#!/bin/sh
# Install Chronos-2, LightGBM and CPU PyTorch while the build still has internet. Never fails the build:
# on any error the full output is kept in /opt/models/INSTALL_FAILED.log instead. The steps run in a subshell so that
# an `exit 1` ends only them, not this script (a brace group would exit before the log is saved).
LOG=/tmp/install_models.log
mkdir -p /opt/models
(
    set -x
    id
    python3 --version
    python3 -m pip --version || exit 1
    # LightGBM needs the OpenMP runtime (libgomp). Best effort: the base image may have no apt-get.
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
    fi
    python3 -m pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        torch "chronos-forecasting>=2.3,<3" "lightgbm>=4,<5" numpy pandas huggingface_hub || exit 1
    HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 python3 -c "from huggingface_hub import snapshot_download; snapshot_download('amazon/chronos-2', revision='${CHRONOS_REVISION:-main}', local_dir='/opt/models/chronos-2')" || exit 1
    python3 -c "import torch, lightgbm, pandas, numpy; from chronos import Chronos2Pipeline" || exit 1
) >"$LOG" 2>&1
if [ $? -ne 0 ]; then
    cp "$LOG" /opt/models/INSTALL_FAILED.log
    echo "Model install failed; see /opt/models/INSTALL_FAILED.log in the image" >&2
    tail -n 40 "$LOG" >&2
fi
exit 0
