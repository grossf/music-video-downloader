#!/bin/sh
set -e

# YouTube changes regularly and yt-dlp ships fixes within days. An image that
# pinned yt-dlp at build time would eventually fail every download, so fetch
# the latest release on each start. A failed update (no internet, PyPI down)
# is not fatal: the app starts with the version already installed.
if [ "${YTDLP_AUTO_UPDATE:-true}" = "true" ]; then
    echo "entrypoint: updating yt-dlp"
    pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore --quiet --upgrade yt-dlp \
        || echo "entrypoint: yt-dlp update failed, continuing with the installed version" >&2
fi
echo "entrypoint: yt-dlp $(python -m yt_dlp --version)"

mkdir -p "$DATA_DIR" "$MEDIA_ROOT"

# Without PUID/PGID the app runs as root, as it always has, so existing
# root-owned libraries keep working. With them, files are created as that
# user and can be managed on the host without sudo.
if [ -n "$PUID" ]; then
    PGID="${PGID:-$PUID}"
    # The data dir is small (database and staging), so it is safe to take
    # over recursively. The media library is not touched beyond its root:
    # it may be large, and changing ownership there is the user's call.
    chown -R "$PUID:$PGID" "$DATA_DIR"
    chown "$PUID:$PGID" "$MEDIA_ROOT"
    # yt-dlp keeps a cache under $HOME, which does not exist for this user.
    export HOME="$DATA_DIR/home"
    mkdir -p "$HOME"
    chown "$PUID:$PGID" "$HOME"
    echo "entrypoint: running as $PUID:$PGID"
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

echo "entrypoint: running as root (set PUID and PGID to change this)"
exec "$@"
