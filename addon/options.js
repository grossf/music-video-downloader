"use strict";

const DEFAULTS = { serverUrl: "http://localhost:8080", apiToken: "" };

const el = (id) => document.getElementById(id);

function say(message, tone) {
  const result = el("result");
  result.textContent = message;
  result.className = tone || "";
}

async function load() {
  const stored = await browser.storage.local.get(DEFAULTS);
  el("serverUrl").value = stored.serverUrl || "";
  el("apiToken").value = stored.apiToken || "";
}

el("save").addEventListener("click", async () => {
  await browser.storage.local.set({
    serverUrl: el("serverUrl").value.trim().replace(/\/+$/, ""),
    apiToken: el("apiToken").value.trim(),
  });
  say("Saved.", "ok");
});

el("test").addEventListener("click", async () => {
  const serverUrl = el("serverUrl").value.trim().replace(/\/+$/, "");
  const apiToken = el("apiToken").value.trim();
  if (!serverUrl) {
    say("Enter a server URL first.", "err");
    return;
  }
  say("Testing…");

  try {
    /* /api/health needs no auth, so hit an authenticated route as well —
     * otherwise a wrong token still looks like success. */
    const health = await fetch(`${serverUrl}/api/health`);
    if (!health.ok) {
      say(`Server responded ${health.status}.`, "err");
      return;
    }
    const auth = await fetch(`${serverUrl}/api/videos?ids=test`, {
      headers: { Authorization: `Bearer ${apiToken}` },
    });
    if (auth.status === 401) {
      say("Reached the server, but the token was rejected.", "err");
      return;
    }
    if (!auth.ok) {
      say(`Reached the server, but got ${auth.status}.`, "err");
      return;
    }
    const info = await health.json();
    say(
      `Connected. Jellyfin ${info.jellyfin_configured ? "configured" : "not configured"}.`,
      "ok"
    );
  } catch (err) {
    say("Could not reach the server. Check the URL and that it is running.", "err");
  }
});

load();
