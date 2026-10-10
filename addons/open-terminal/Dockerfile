# Open Terminal's slim image: no sudo, no compilers, and a built-in egress firewall. Pin by digest
# once a build works, so the sandbox cannot change under you.
ARG BUILD_FROM=ghcr.io/open-webui/open-terminal:slim
FROM ${BUILD_FROM}

COPY options_to_env.py /options_to_env.py
COPY run.sh /run.sh
COPY sdk/greenhouse_gateway.py /tmp/greenhouse_gateway.py
# Put the gateway client on the interpreter's path, wherever this image keeps its site-packages.
RUN python3 -c "import shutil, sysconfig; shutil.copy('/tmp/greenhouse_gateway.py', sysconfig.get_paths()['purelib'])" \
    && rm /tmp/greenhouse_gateway.py \
    && chmod +x /run.sh

# tini is PID 1 as in the base image; /run.sh ends by exec'ing the base entrypoint.
ENTRYPOINT ["/usr/bin/tini", "--", "/run.sh"]
CMD ["run"]
