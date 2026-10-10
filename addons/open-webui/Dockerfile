# Pinned to a release so the chat front end cannot change under you. Newer versions: bump this tag
# deliberately and re-run your checks.
ARG BUILD_FROM=ghcr.io/open-webui/open-webui:v0.11.4
FROM ${BUILD_FROM}

# Run as root so the add-on can write its storage; the container has no Home Assistant credential.
USER root
COPY options_to_env.py /options_to_env.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

WORKDIR /app/backend
CMD ["bash", "/run.sh"]
