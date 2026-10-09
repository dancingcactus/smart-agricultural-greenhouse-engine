ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM ${BUILD_FROM}
RUN apk add --no-cache python3 py3-pip git
WORKDIR /app
COPY pyproject.toml ./
COPY gateway ./gateway
RUN pip install --no-cache-dir --break-system-packages .
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]
