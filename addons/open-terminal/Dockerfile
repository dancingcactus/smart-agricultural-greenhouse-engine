# Open Terminal's slim image: no sudo, no compilers, and a built-in egress firewall. Pin by digest
# once a build works, so the sandbox cannot change under you.
ARG BUILD_FROM=ghcr.io/open-webui/open-terminal:slim
FROM ${BUILD_FROM}

# Forecasting models, installed at build time because the sandbox has no internet afterwards. CPU only.
# Chronos-2 handles continuous series (forecasts and bands); LightGBM handles daily tables such as equipment
# runtime. TabPFN is deliberately not included yet: its weights carry their own license, which must be checked for
# commercial use before it is baked in (docs/forecasting.md). Pin CHRONOS_REVISION to a commit once a build works.
ARG CHRONOS_REVISION=main
ENV CHRONOS2_PATH=/opt/models/chronos-2 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# LightGBM needs the OpenMP runtime; a base image without apt-get is caught by the import check below.
RUN (command -v apt-get >/dev/null && apt-get update && apt-get install -y --no-install-recommends libgomp1 \
        && rm -rf /var/lib/apt/lists/*) || echo "libgomp1 not installed" >&2
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        torch "chronos-forecasting>=2.3,<3" "lightgbm>=4,<5" numpy pandas huggingface_hub \
    && HF_HUB_OFFLINE=0 python3 -c "from huggingface_hub import snapshot_download; snapshot_download('amazon/chronos-2', revision='$CHRONOS_REVISION', local_dir='/opt/models/chronos-2')" \
    && python3 -c "import torch, lightgbm, pandas, numpy; from chronos import Chronos2Pipeline"

COPY options_to_env.py /options_to_env.py
COPY run.sh /run.sh
COPY sdk/greenhouse_gateway.py sdk/greenhouse_forecast.py /tmp/sdk/
# Put the gateway client and the forecasting helpers on the interpreter's path, wherever this image keeps its site-packages.
RUN python3 -c "import glob, shutil, sysconfig; [shutil.copy(f, sysconfig.get_paths()['purelib']) for f in glob.glob('/tmp/sdk/*.py')]" \
    && rm -r /tmp/sdk \
    && chmod +x /run.sh

# tini is PID 1 as in the base image; /run.sh ends by exec'ing the base entrypoint.
ENTRYPOINT ["/usr/bin/tini", "--", "/run.sh"]
CMD ["run"]
