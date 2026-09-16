"""Cross-site request refusal for the web UI.

The web UI has no login, so a form post is authorised by nothing but reaching
the server. Any page open in the browser could therefore post to
/videos/<id>/delete in the background. Browsers always send an Origin header
on a cross-site POST, so comparing it with the Host the request was sent to is
enough to refuse those without a token or a session.

/api/* is exempt: it is authorised by the bearer token, and the addon calls it
from a moz-extension:// origin that would never match.
"""

from urllib.parse import urlsplit

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_same_origin(headers) -> bool:
    source = headers.get("origin") or headers.get("referer")
    if not source:
        # Not a browser (curl, scripts). A browser making a cross-site POST
        # always includes Origin, so this does not open a hole.
        return True
    if source == "null":
        # Sandboxed iframes and some redirects; never our own pages.
        return False
    source_host = urlsplit(source).netloc.lower()
    # Behind a reverse proxy the Host header may be rewritten; the original
    # host the browser used is then in X-Forwarded-Host.
    accepted = {
        headers.get("host", "").lower(),
        headers.get("x-forwarded-host", "").split(",")[0].strip().lower(),
    }
    return bool(source_host) and source_host in accepted


def refuses(method: str, path: str, headers) -> bool:
    if method.upper() in SAFE_METHODS or path.startswith("/api/"):
        return False
    return not is_same_origin(headers)
