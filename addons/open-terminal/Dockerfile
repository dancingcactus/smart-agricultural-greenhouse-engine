# Open Terminal's slim image: no sudo, no compilers, and a built-in egress firewall. Pin by digest
# once a build works, so the sandbox cannot change under you.
ARG BUILD_FROM=ghcr.io/open-webui/open-terminal:slim
FROM ${BUILD_FROM}

# Forecasting models, installed at build time because the sandbox has no internet afterwards. CPU only.
# Chronos-2 handles continuous series (forecasts and bands); LightGBM handles daily tables such as equipment
# runtime. TabPFN is deliberately not included yet: its weights carry their own license, which must be checked for
# commercial use before it is baked in (docs/forecasting.md). Pin CHRONOS_REVISION to a commit once a build works.
ARG CHRONOS_REVISION=main
ENV CHRONOS2_PATH=/opt/models/chronos-2 CHRONOS_REVISION=${CHRONOS_REVISION}
# This step must not stop the sandbox from building: Home Assistant shows no build output, so a failure here would
# only say "unknown error". If it fails, the reason is saved in the image at /opt/models/INSTALL_FAILED.log, run.sh
# prints it at start, and the forecasting library still works with its baseline (the model wrappers need the models).
COPY install_models.sh /tmp/install_models.sh
RUN sh /tmp/install_models.sh; rm -f /tmp/install_models.sh

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
