FROM python:3.11-slim

# ffmpeg is required: bv*+ba downloads arrive as separate streams and must be
# muxed into the mkv container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app

ENV MEDIA_ROOT=/media/music-videos \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
