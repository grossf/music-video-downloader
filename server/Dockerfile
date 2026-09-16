FROM python:3.11-slim

# ffmpeg is required: bv*+ba downloads arrive as separate streams and must be
# muxed into the mkv container.
# setpriv (util-linux, already in the base image) drops to PUID/PGID.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Set by the release workflow from the git tag, e.g. v0.2.0.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Strip CRLF in case the script was checked out on Windows; /bin/sh would
# otherwise fail with a confusing "not found".
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

ENV MEDIA_ROOT=/media/music-videos \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

EXPOSE 8080
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
