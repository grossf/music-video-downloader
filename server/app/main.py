import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import init_db
from app.routes_api import router as api_router
from app.routes_web import router as web_router
from app.services import jellyfin
from app.worker import requeue_interrupted, worker_loop

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings.ensure_dirs()
    profile_id = init_db()
    log.info("media_root=%s data_dir=%s", settings.media_root, settings.data_dir)
    log.info("default profile id=%s", profile_id)
    log.info("jellyfin configured: %s", jellyfin.is_configured())

    # A row still marked 'downloading' means the process died mid-job.
    recovered = requeue_interrupted()
    if recovered:
        log.info("requeued %s interrupted download(s)", recovered)

    stop = asyncio.Event()
    workers = [
        asyncio.create_task(worker_loop(f"w{i}", stop))
        for i in range(max(1, settings.download_concurrency))
    ]
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*workers, return_exceptions=True)


app = FastAPI(title="Music Video Downloader", lifespan=lifespan)

# The addon calls from a moz-extension:// origin. The bearer token is the
# security boundary here, not the origin, so this stays permissive —
# allow_credentials is False because the token travels in a header.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Serves thumbnails to the library view. check_dir=False because the mount is
# evaluated at import time, before the lifespan has created the directory.
app.mount(
    "/media",
    StaticFiles(directory=str(settings.media_root), check_dir=False),
    name="media",
)
app.include_router(api_router)
app.include_router(web_router)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "media_root": str(settings.media_root),
        "jellyfin_configured": jellyfin.is_configured(),
    }
