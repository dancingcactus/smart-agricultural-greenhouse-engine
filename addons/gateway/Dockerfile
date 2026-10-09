ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM ${BUILD_FROM}
RUN apk add --no-cache python3 py3-pip git
WORKDIR /app
COPY pyproject.toml ./
COPY gateway ./gateway
RUN pip install --no-cache-dir --break-system-packages .
CMD ["python3", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8099"]
