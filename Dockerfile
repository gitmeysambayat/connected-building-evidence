# Linux CI executes this image under the proposed free-host resource limits.
FROM python:3.12-slim-bookworm AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY 20261018_v1_requirements_lock.txt /app/
RUN pip install --no-cache-dir -r 20261018_v1_requirements_lock.txt
FROM dependencies AS prepare
ARG BUILD_REVISION=unrecorded
ARG RENDER_GIT_COMMIT
COPY . /app/
# Real SQL/RDF import, IFC tessellation and model fitting happen during build.
RUN python scripts/20261018_v1_Cloud_Prepare.py --output /prepared --revision "${RENDER_GIT_COMMIT:-$BUILD_REVISION}"
# Geometry uses an owner-only temporary file. The non-root runtime must be able
# to read every immutable baseline file before copying it into its own state.
RUN chmod -R a+rX /prepared

FROM dependencies AS runtime
ARG BUILD_REVISION=unrecorded
LABEL org.opencontainers.image.title="Connected Building" \
      org.opencontainers.image.source="https://github.com/gitmeysambayat/connected-building-evidence" \
      org.opencontainers.image.revision="$BUILD_REVISION"
ENV PORT=8080 CONNECTED_BUILDING_DATA_DIR=/state TWIN_RUNTIME_DIR=/state/transport \
    TWIN_GEOMETRY_PATH=/state/20261018_v1_geometry.json \
    TWIN_ANALYTICS_PATH=/state/20261018_v1_temperature_forecast.json \
    TWIN_PREPARED_DIR=/opt/twin-prepared TWIN_STATE_PERSISTENCE=ephemeral \
    TWIN_CLOUD_BOOT_RECORD=/state/20261018_v1_Cloud_Boot.json \
    TWIN_BUILD_REVISION=$BUILD_REVISION
COPY --from=prepare /app /app
COPY --from=prepare /prepared /opt/twin-prepared
RUN useradd --create-home --uid 10001 twin && mkdir -p /state && chown twin:twin /state
USER twin
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=8)"
CMD ["python", "scripts/20261018_v1_Cloud_Start.py"]
