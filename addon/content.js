/*
 * Detects which video the page is showing and tells the background script.
 *
 * YouTube is a single-page app: navigating from one video to the next never
 * reloads the page, so reading the URL once at injection time would leave the
 * addon permanently describing whatever video you happened to land on first.
 *
 * This script deliberately makes NO network calls. It runs in YouTube's HTTPS
 * page context, so a request to an http:// server on the LAN would be blocked
 * as mixed content. All server traffic belongs in background.js.
 */

(() => {
  "use strict";

  const POLL_MS = 1000;
  let lastVideoId = null;

  function currentVideoId() {
    try {
      const url = new URL(location.href);
      if (url.pathname === "/watch") {
        return url.searchParams.get("v");
      }
      if (url.pathname.startsWith("/shorts/")) {
        return url.pathname.split("/")[2] || null;
      }
      return null;
    } catch {
      return null;
    }
  }

  /* Best-effort only. These selectors are YouTube's internal DOM and will
   * eventually change; the background script re-probes through the server
   * anyway, so a miss here costs nothing but a slightly emptier first render. */
  function pageHints() {
    const channelLink = document.querySelector(
      "ytd-channel-name a, #owner #channel-name a, #upload-info a"
    );
    const titleEl = document.querySelector(
      "h1.ytd-watch-metadata yt-formatted-string, h1.title yt-formatted-string"
    );
    return {
      pageTitle: (titleEl && titleEl.textContent.trim()) || document.title || "",
      channelName: (channelLink && channelLink.textContent.trim()) || "",
    };
  }

  function notify(force = false) {
    const videoId = currentVideoId();
    if (!force && videoId === lastVideoId) return;
    lastVideoId = videoId;

    browser.runtime
      .sendMessage({
        type: "video-changed",
        videoId,
        ...pageHints(),
      })
      .catch(() => {
        /* Background not ready yet; the poll below will retry. */
      });
  }

  /* YouTube's own navigation event — the fast path. */
  document.addEventListener("yt-navigate-finish", () => notify());
  document.addEventListener("yt-page-data-updated", () => notify());
  window.addEventListener("popstate", () => notify());

  /* Safety net: the events above are undocumented internals and have been
   * renamed before. A 1s href check is cheap and keeps the badge honest even
   * if YouTube drops them entirely. */
  setInterval(() => notify(), POLL_MS);

  notify(true);
})();
