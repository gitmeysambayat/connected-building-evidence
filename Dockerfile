# Prepared deployment definition. Build/run status is recorded in reports,
# not inferred from this file. The HTTP service is exposed; MQTT stays loopback.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CONNECTED_BUILDING_DATA_DIR=/state TWIN_RUNTIME_DIR=/state/transport \
    TWIN_GEOMETRY_PATH=/state/20261018_v1_geometry.json \
    TWIN_ANALYTICS_PATH=/state/20261018_v1_temperature_forecast.json
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY 20261018_v1_requirements_lock.txt /app/
RUN pip install --no-cache-dir -r 20261018_v1_requirements_lock.txt
COPY . /app/
RUN useradd --create-home --uid 10001 twin && mkdir -p /state && chown twin:twin /state
USER twin
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "twin.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
