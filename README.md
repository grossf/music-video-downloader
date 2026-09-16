# Music Video Downloader

Download YouTube music videos into a Jellyfin-readable library, and see from
inside Firefox whether the video you are watching is already archived.

Three pieces:

| Piece | What it does |
|---|---|
| **Server** (`server/`) | FastAPI app — JSON API, web UI, and a download worker built on yt-dlp. Runs as one container. |
| **Addon** (`addon/`) | Firefox extension. Badges the toolbar with the current video's status and queues downloads. |
| **Library** (`MEDIA_ROOT`) | Plain folders + NFO sidecars, pointed at by a Jellyfin **Music Videos** library. |

## How it fits together

```
Firefox addon                  Server (Docker)           MEDIA_ROOT
─────────────                  ───────────────           ──────────
content.js  detects video id   /api/probe                Artist/
background.js  badge + fetch   /api/videos                 Artist - Title [id].mkv
popup.js  prefilled form       worker → yt-dlp             Artist - Title [id].nfo
                               SQLite (queue + index)      Artist - Title [id]-thumb.jpg
web UI  review / edit / delete → Jellyfin refresh
```

Jellyfin's Music Videos library does **no external metadata lookup**, so the
NFO sidecar is the only thing that gives a video an artist, title or tag. That
is why metadata correction is a first-class feature rather than a nicety.

## Quick start (local development)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e "server[dev]"
```

Create `.env` in the repo root:

```
MEDIA_ROOT=D:/repos/music-video-downloader/.local/media
DATA_DIR=D:/repos/music-video-downloader/.local/data
API_TOKEN=dev-token
```

Run it:

```bash
.venv/Scripts/python.exe -m uvicorn --app-dir server app.main:app --port 8080 --reload
```

The library view is at <http://localhost:8080>.

`ffmpeg` must be on PATH — `bv*+ba` downloads arrive as separate video and
audio streams that have to be muxed. The Docker image installs it for you.

## Running on the server

```bash
cp .env.example .env     # set API_TOKEN and MEDIA_HOST_PATH
docker compose up -d
```

`MEDIA_HOST_PATH` must be the same host path your Jellyfin container mounts as
its Music Videos library.

Set `JELLYFIN_URL` and `JELLYFIN_API_KEY` to have the app trigger a library
refresh after each download and each metadata edit. Leave them empty and that
step is skipped silently.

## Installing the addon

During development, load it temporarily — no signing needed:

1. Open `about:debugging#/runtime/this-firefox`
2. **Load Temporary Add-on**, pick `addon/manifest.json`
3. Open the addon's **Settings** and set the server URL and API token
4. **Test connection** verifies both reachability and the token

A temporary add-on is removed when Firefox restarts. For a permanent install,
submit it to AMO as an **unlisted** add-on — that is free and automated, and
gives you a signed `.xpi` you host yourself.

### Badge states

| Badge | Meaning |
|---|---|
| `✓` | In your library |
| `↓` | Downloading |
| `…` | Queued |
| `+` | Not downloaded — click to add |
| `–` | Deleted on purpose (kept as a tombstone so it is not silently re-fetched) |
| `!` | Last download failed |
| `?` | Server unreachable, or the token was rejected |

## Metadata

Extraction prefers yt-dlp's real music metadata, falls back to parsing the
title, and **never falls back to the channel name for the artist** — for K-pop
the channel is the label (HYBE LABELS, SMTOWN, JYP), so that guess would
quietly organise the library by record company. A video with no artist is left
empty and flagged `needs_review`, which the web UI can filter on.

Type and quality profile are remembered per channel, as a side effect of
filling in the form. There is no channel admin screen on purpose: the second
time you queue something from a channel, those fields are already filled.

The uploading channel's name is written to the NFO as `<studio>`
automatically. There is no separate label field: in practice it was always the
channel name retyped by hand, and wrong as a label anyway, since a distribution
channel like 1theK is not the artist's record company. Jellyfin's Music Videos
library does not appear to surface `<studio>` in its UI, so it is kept in the
file only because it costs nothing. The type `<tag>` may not surface either —
that is unverified.

## Testing

```bash
cd server && python -m pytest tests/ -q
```

Drive the download path without any UI:

```bash
python server/scripts/dev_download.py "https://www.youtube.com/watch?v=..." --format "bv*[height<=360]+ba/b"
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MEDIA_ROOT` | `/media/music-videos` | The Jellyfin library root |
| `DATA_DIR` | `/data` | SQLite database and download staging |
| `API_TOKEN` | `change-me` | Shared secret for `/api/*` |
| `DOWNLOAD_CONCURRENCY` | `1` | Parallel downloads |
| `JELLYFIN_URL` | unset | e.g. `http://jellyfin:8096` |
| `JELLYFIN_API_KEY` | unset | Jellyfin API key |
| `LOG_LEVEL` | `INFO` | |

## Quality profiles

A profile is a download template. Manage them at `/profiles`.

Express one either through structured fields — max height, preferred codec,
frame rate, container — or through a raw yt-dlp selector that bypasses all of
them. The structured fields exist so the app can answer "is 2160p an upgrade
on what I have?", which a raw string cannot; the override exists so the
structured model never becomes a cage. Each profile shows the selector it
compiles to.

The codec list is ordered by hardware-decode support rather than efficiency:
AV1 compresses better, but VP9 direct-plays on far more Jellyfin clients, so
a 4K profile preferring VP9 avoids transcoding.

A profile can be chosen per video — in the addon popup, in the web add form,
or in the edit form. A choice that differs from the global default is
remembered for that channel, so the second video from the same channel comes
back pre-selected. Choosing the default itself is not treated as a preference,
or the channel would be pinned to whatever the default happened to be that
day.

Changing a video's profile does not re-download it. Use **Redownload** on a
completed video to fetch it again with the current profile; hand-corrected
metadata is kept and only the file is replaced.

## Not built yet

The upgrade worker that re-checks whether YouTube has since published a higher
resolution (`cutoff_height`, `allow_upgrades` and the `downloaded_*` columns
exist to support it), per-channel admin, progress bars, playlists, pagination
and search.
