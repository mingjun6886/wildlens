/**
 * Wiring. Every other module is testable without a browser; this one is the part
 * that touches the DOM, and it holds no decisions of its own.
 */

import { ApiError, searchByFile, searchBySpecies, searchByTags } from "./api.js";
import { claims, completeSignIn, isSignedIn, signIn, signOut } from "./auth.js";
import { toBase64, uploadFile } from "./upload.js";

const el = (id) => document.getElementById(id);

const STAGE_TEXT = {
  hashing: "hashing locally",
  reserving: "checking whether we have it",
  duplicate: "already in the collection",
  joining: "already being processed",
  sending: "sending to storage",
  pending: "queued",
  processing: "identifying species",
  done: "done",
  failed: "failed",
};

function showSession() {
  const signedIn = isSignedIn();
  el("signed-in").hidden = !signedIn;
  el("signed-out").hidden = signedIn;

  el("session").replaceChildren();
  if (!signedIn) return;

  const who = document.createElement("span");
  who.className = "muted";
  who.textContent = claims()?.email ?? "signed in";

  const out = document.createElement("button");
  out.className = "quiet";
  out.textContent = "Sign out";
  out.addEventListener("click", signOut);

  el("session").append(who, document.createTextNode(" "), out);
}

/** One row in the activity list, updated in place as the upload progresses. */
function activityRow(file) {
  const row = document.createElement("li");
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = file.name;
  const stage = document.createElement("span");
  stage.className = "stage";
  row.append(name, stage);
  el("activity").prepend(row);

  return {
    update: ({ stage: key, detail }) => {
      stage.textContent = [STAGE_TEXT[key] ?? key, detail].filter(Boolean).join(" · ");
    },
    finish: (record) => {
      stage.className = record.status === "FAILED" ? "stage failed" : "stage";
      stage.textContent =
        record.status === "FAILED"
          ? `failed — ${record.errorReason ?? "unknown reason"}`
          : describeTags(record.tags) + (record.wasDuplicate ? " · already had it" : "");
    },
    fail: (message) => {
      stage.className = "stage failed";
      stage.textContent = message;
    },
  };
}

function describeTags(tags) {
  const entries = Object.entries(tags ?? {});
  if (entries.length === 0) return "no animals detected";
  return entries.map(([name, count]) => (count > 1 ? `${count} × ${name}` : name)).join(", ");
}

async function handleFile(file) {
  const row = activityRow(file);
  try {
    const record = await uploadFile(file, row.update);
    row.finish(record);
  } catch (error) {
    row.fail(
      error instanceof ApiError && error.correlationId
        ? `${error.message} (ref ${error.correlationId})`
        : error.message,
    );
  }
}

function renderResults(payload) {
  el("search-note").textContent = payload.note
    ? payload.note
    : `${payload.results.length} match${payload.results.length === 1 ? "" : "es"}`;

  el("results").replaceChildren(
    ...payload.results.map((record) => {
      const card = document.createElement("div");
      card.className = "card";

      if (record.thumbUrl) {
        const link = document.createElement("a");
        link.href = record.fullUrl ?? record.thumbUrl;
        link.target = "_blank";
        link.rel = "noreferrer";
        const image = document.createElement("img");
        // The signed URL loads in an <img> without any CORS configuration,
        // because an image embed is not a fetch. This is why the thumbnail
        // bucket has no CORS rule.
        image.src = record.thumbUrl;
        image.alt = describeTags(record.tags);
        image.loading = "lazy";
        link.append(image);
        card.append(link);
      }

      const tags = document.createElement("div");
      tags.className = "tags";
      tags.textContent = describeTags(record.tags);
      const by = document.createElement("div");
      by.className = "by";
      by.textContent = record.uploadedBy ?? "unattributed";
      card.append(tags, by);

      return card;
    }),
  );
}

/** "cattle 2, magpie 1" -> {cattle: 2, magpie: 1} */
function parseTagQuery(text) {
  const counts = {};
  for (const part of text.split(",")) {
    const match = /^\s*(.+?)\s+(\d+)\s*$/.exec(part);
    if (match) counts[match[1].toLowerCase()] = Number(match[2]);
    else if (part.trim()) counts[part.trim().toLowerCase()] = 1;
  }
  return counts;
}

async function runSearch(which) {
  el("search-note").textContent = "searching…";
  try {
    if (which === "species") {
      const names = el("species").value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
      if (names.length === 0) throw new Error("name at least one species");
      renderResults(await searchBySpecies(names));
    } else {
      const counts = parseTagQuery(el("tags").value);
      if (Object.keys(counts).length === 0) throw new Error("name at least one tag");
      renderResults(await searchByTags(counts));
    }
  } catch (error) {
    el("search-note").textContent = error.message;
    el("results").replaceChildren();
  }
}

async function searchBySample(file) {
  el("search-note").textContent = "identifying the sample…";
  try {
    const payload = await searchByFile({
      base64: await toBase64(file),
      ext: file.name.split(".").pop().toLowerCase(),
    });
    renderResults(payload);
    if (payload.results.length === 0 && Object.keys(payload.query).length > 0) {
      el("search-note").textContent =
        `sample contains ${describeTags(payload.query)} — nothing holds all of those`;
    }
  } catch (error) {
    // A 503 here is the documented cold-start limit, not a fault.
    el("search-note").textContent = error.message;
  }
}

function wire() {
  el("sign-in").addEventListener("click", signIn);

  const drop = el("drop");
  drop.addEventListener("click", () => el("file").click());
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") el("file").click();
  });
  el("file").addEventListener("change", (event) => {
    for (const file of event.target.files) handleFile(file);
    event.target.value = "";
  });

  for (const type of ["dragenter", "dragover"]) {
    drop.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.add("over");
    });
  }
  for (const type of ["dragleave", "drop"]) {
    drop.addEventListener(type, () => drop.classList.remove("over"));
  }
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    for (const file of event.dataTransfer.files) handleFile(file);
  });

  el("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch(event.submitter?.value ?? "species");
  });
  el("sample").addEventListener("change", (event) => {
    if (event.target.files[0]) searchBySample(event.target.files[0]);
    event.target.value = "";
  });
}

async function start() {
  wire();
  try {
    await completeSignIn();
  } catch (error) {
    el("search-note").textContent = error.message;
  }
  showSession();
}

start();
