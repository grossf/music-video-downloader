"use strict";

/* Mirrors the server's type enum (app/db.py TYPE_TAGS). */
const TYPE_OPTIONS = [
  ["mv", "MV"],
  ["performance", "Performance"],
  ["dance_practice", "Dance Practice"],
  ["live_stage", "Live Stage"],
  ["fancam", "Fancam"],
  ["relay_dance", "Relay Dance"],
  ["behind", "Behind the Scenes"],
  ["other", "Other"],
];

const el = (id) => document.getElementById(id);

const STATE_TEXT = {
  done: ["ok", "In your library"],
  downloading: ["warn", "Downloading…"],
  queued: ["warn", "Queued"],
  failed: ["err", "Last download failed"],
  deleted: ["info", "Deleted from your library"],
  absent: ["info", "Not downloaded"],
  unauthorized: ["err", "Bad API token"],
  offline: ["err", "Server unreachable"],
  unconfigured: ["warn", "Server not configured"],
};

/* States where downloading is the useful next action. "deleted" is included
 * on purpose: the tombstone tells you it was removed deliberately, but you may
 * still want it back — it just should not happen silently. */
const ACTIONABLE = new Set(["absent", "deleted", "failed"]);

let context = null;

function setStatus(state) {
  const [tone, text] = STATE_TEXT[state] || ["err", "Unknown state"];
  el("dot").className = `dot ${tone}`;
  el("state-text").textContent = text;
}

function fillTypeOptions(selected) {
  const select = el("type");
  select.innerHTML = "";
  for (const [value, text] of TYPE_OPTIONS) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    if (value === selected) option.selected = true;
    select.append(option);
  }
}

function showDetail(parts) {
  el("detail").textContent = parts.filter(Boolean).join(" · ");
}

let profiles = [];

/* Pre-selects the server's default profile. */
async function fillProfiles() {
  const select = el("profile");
  const result = await browser.runtime.sendMessage({ type: "get-profiles" });

  if (!result.ok) {
    /* The picker is a convenience, not a gate — a failed lookup must not stop
     * you queueing the video with the server's default. */
    select.innerHTML = "";
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Server default";
    select.append(option);
    el("profile-summary").textContent = "Could not load profiles.";
    return;
  }

  profiles = result.data.profiles;
  const chosen = result.data.default_profile_id;

  select.innerHTML = "";
  for (const profile of profiles) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.name + (profile.is_default ? " (default)" : "");
    if (profile.id === chosen) option.selected = true;
    select.append(option);
  }
  showProfileSummary();
}

function showProfileSummary() {
  const selected = profiles.find((p) => String(p.id) === el("profile").value);
  el("profile-summary").textContent = selected ? selected.summary : "";
}

el("profile").addEventListener("change", showProfileSummary);

async function render() {
  context = await browser.runtime.sendMessage({ type: "get-popup-state" });

  el("library-link").href = context.serverUrl || "#";

  if (!context.videoId) {
    setStatus("absent");
    el("state-text").textContent = "Not a YouTube video page";
    showDetail([]);
    return;
  }

  setStatus(context.state || "offline");

  if (context.state === "done" && context.video) {
    const v = context.video;
    showDetail([
      v.artist || "no artist",
      v.title,
      v.downloaded_height ? `${v.downloaded_height}p` : null,
      v.profile_name,
    ]);
    if (v.needs_review) {
      el("review-note").textContent = "This one is still flagged for review.";
      el("review-note").classList.remove("hidden");
    }
    return;
  }

  if (context.state === "queued" || context.state === "downloading") {
    showDetail([context.video && context.video.title]);
    /* Keep the popup honest while it is open. */
    setTimeout(render, 2000);
    return;
  }

  if (!ACTIONABLE.has(context.state)) {
    showDetail([
      context.state === "unconfigured" || context.state === "unauthorized"
        ? "Open Settings to fix this."
        : "Could not reach the server.",
    ]);
    return;
  }

  if (context.state === "failed" && context.video && context.video.error) {
    showDetail([context.video.error.slice(0, 120)]);
  }

  await prefillForm();
}

async function prefillForm() {
  /* Render immediately from what the content script saw, so the form is never
   * blank while the lookup runs, then refine with the server's answer. */
  const hints = context.hints || {};
  el("title").value = hints.pageTitle || "";
  fillTypeOptions("mv");
  el("form").classList.remove("hidden");

  /* Both in flight at once, so the profile picker is ready while the
   * metadata lookup is still running. */
  const [, result] = await Promise.all([
    fillProfiles(),
    browser.runtime.sendMessage({
      type: "probe",
      url: `https://www.youtube.com/watch?v=${context.videoId}`,
    }),
  ]);

  if (!result.ok) {
    el("error").textContent = `Lookup failed: ${result.error}`;
    el("error").classList.remove("hidden");
    return;
  }

  const p = result.data;
  context.probe = p;
  el("artist").value = p.artist || "";
  el("title").value = p.title || hints.pageTitle || "";
  fillTypeOptions(p.type || "mv");

  showDetail([p.channel_name, p.available_heights?.length ? `up to ${Math.max(...p.available_heights)}p` : null]);

  if (!p.artist) {
    el("review-note").classList.remove("hidden");
    el("artist").focus();
  }
}

el("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  el("submit").disabled = true;
  el("submit").textContent = "Queueing…";
  el("error").classList.add("hidden");

  const p = context.probe || {};
  const result = await browser.runtime.sendMessage({
    type: "enqueue",
    tabId: context.tabId,
    payload: {
      video_id: context.videoId,
      artist: el("artist").value.trim() || null,
      title: el("title").value.trim() || null,
      type: el("type").value,
      year: p.year ?? null,
      duration: p.duration ?? null,
      channel_id: p.channel_id ?? null,
      channel_name: p.channel_name ?? null,
      profile_id: el("profile").value ? Number(el("profile").value) : null,
    },
  });

  if (!result.ok) {
    el("submit").disabled = false;
    el("submit").textContent = "Download";
    el("error").textContent = result.error;
    el("error").classList.remove("hidden");
    return;
  }

  el("form").classList.add("hidden");
  setStatus("queued");
  showDetail(["Queued for download"]);
});

el("options-link").addEventListener("click", (event) => {
  event.preventDefault();
  browser.runtime.openOptionsPage();
});

render();
