/*
 * All server traffic lives here.
 *
 * This is not an arbitrary split: content scripts inherit YouTube's HTTPS page
 * context, so a fetch to an http:// server on the LAN is blocked as mixed
 * content. The background script, holding host permissions, is not subject to
 * that, so every request goes through this file.
 */

"use strict";

const DEFAULT_SETTINGS = {
  serverUrl: "http://localhost:8080",
  apiToken: "",
};

/* How the badge renders each state. "absent" and "deleted" are deliberately
 * distinct: a deleted video was removed on purpose, and showing it as merely
 * "not downloaded" invites re-downloading something you threw away. */
const BADGE = {
  done: { text: "✓", color: "#4ba36b", title: "In your library" },
  downloading: { text: "↓", color: "#d0a215", title: "Downloading…" },
  queued: { text: "…", color: "#d0a215", title: "Queued" },
  failed: { text: "!", color: "#cf5c5c", title: "Download failed" },
  deleted: { text: "–", color: "#6b7280", title: "Deleted from your library" },
  absent: { text: "+", color: "#6ea8fe", title: "Not downloaded — click to add" },
  unauthorized: { text: "?", color: "#cf5c5c", title: "Bad API token — check options" },
  offline: { text: "?", color: "#6b7280", title: "Server unreachable" },
  unconfigured: { text: "?", color: "#d0a215", title: "Set the server URL in options" },
};

const POLL_MS = 3000;

/* tabId -> { videoId, hints, state, video } */
const tabs = new Map();
let pollTimer = null;

async function getSettings() {
  const stored = await browser.storage.local.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

async function apiFetch(path, { method = "GET", body } = {}) {
  const { serverUrl, apiToken } = await getSettings();
  if (!serverUrl) {
    const err = new Error("unconfigured");
    err.state = "unconfigured";
    throw err;
  }
  const headers = { Authorization: `Bearer ${apiToken}` };
  if (body) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(serverUrl.replace(/\/+$/, "") + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    const err = new Error("offline");
    err.state = "offline";
    throw err;
  }
  if (response.status === 401) {
    const err = new Error("unauthorized");
    err.state = "unauthorized";
    throw err;
  }
  return response;
}

function setBadge(tabId, state) {
  const badge = BADGE[state] || BADGE.offline;
  browser.browserAction.setBadgeText({ tabId, text: badge.text });
  browser.browserAction.setBadgeBackgroundColor({ tabId, color: badge.color });
  browser.browserAction.setTitle({
    tabId,
    title: `Music Video Downloader — ${badge.title}`,
  });
}

function clearBadge(tabId) {
  browser.browserAction.setBadgeText({ tabId, text: "" });
  browser.browserAction.setTitle({ tabId, title: "Music Video Downloader" });
}

async function refreshTab(tabId) {
  const entry = tabs.get(tabId);
  if (!entry || !entry.videoId) {
    clearBadge(tabId);
    return;
  }

  try {
    const response = await apiFetch(`/api/videos/${entry.videoId}`);
    if (response.status === 404) {
      entry.state = "absent";
      entry.video = null;
    } else if (response.ok) {
      const video = await response.json();
      entry.video = video;
      entry.state = video.status;
    } else {
      entry.state = "offline";
    }
  } catch (err) {
    entry.state = err.state || "offline";
    entry.video = null;
  }

  setBadge(tabId, entry.state);
  schedulePolling();
}

/* Poll only while something is actually in flight, so an idle browser makes no
 * requests at all. */
function schedulePolling() {
  const busy = [...tabs.values()].some(
    (entry) => entry.state === "queued" || entry.state === "downloading"
  );
  if (busy && pollTimer === null) {
    pollTimer = setInterval(() => {
      for (const [tabId, entry] of tabs) {
        if (entry.state === "queued" || entry.state === "downloading") {
          refreshTab(tabId);
        }
      }
    }, POLL_MS);
  } else if (!busy && pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

browser.runtime.onMessage.addListener((message, sender) => {
  switch (message.type) {
    case "video-changed": {
      const tabId = sender.tab && sender.tab.id;
      if (tabId === undefined) return;
      tabs.set(tabId, {
        videoId: message.videoId,
        hints: {
          pageTitle: message.pageTitle || "",
          channelName: message.channelName || "",
        },
        state: null,
        video: null,
      });
      return refreshTab(tabId);
    }

    case "get-popup-state": {
      return (async () => {
        const [tab] = await browser.tabs.query({
          active: true,
          currentWindow: true,
        });
        const entry = (tab && tabs.get(tab.id)) || null;
        const settings = await getSettings();
        return {
          tabId: tab ? tab.id : null,
          videoId: entry ? entry.videoId : null,
          hints: entry ? entry.hints : null,
          state: entry ? entry.state : null,
          video: entry ? entry.video : null,
          serverUrl: settings.serverUrl,
          configured: Boolean(settings.serverUrl && settings.apiToken),
        };
      })();
    }

    case "probe": {
      return (async () => {
        try {
          const response = await apiFetch(
            `/api/probe?url=${encodeURIComponent(message.url)}`
          );
          if (!response.ok) {
            return { ok: false, error: `Lookup failed (${response.status})` };
          }
          return { ok: true, data: await response.json() };
        } catch (err) {
          return { ok: false, error: err.state || String(err) };
        }
      })();
    }

    case "enqueue": {
      return (async () => {
        try {
          const response = await apiFetch("/api/videos", {
            method: "POST",
            body: message.payload,
          });
          if (!response.ok) {
            return { ok: false, error: `Queue failed (${response.status})` };
          }
          const data = await response.json();
          if (message.tabId != null) await refreshTab(message.tabId);
          return { ok: true, data };
        } catch (err) {
          return { ok: false, error: err.state || String(err) };
        }
      })();
    }

    case "refresh": {
      return refreshTab(message.tabId);
    }

    default:
      return undefined;
  }
});

browser.tabs.onRemoved.addListener((tabId) => {
  tabs.delete(tabId);
  schedulePolling();
});

browser.browserAction.setBadgeTextColor?.({ color: "#0b1220" });
