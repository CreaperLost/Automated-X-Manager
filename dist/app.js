/* X-Automation frontend.
 *
 * Talks to the Rust backend over Tauri's IPC bridge. No framework: the
 * view is small enough for explicit render calls, and the app is local,
 * so there is no router or data layer to justify one.
 *
 * Two rules mirrored from the Python original:
 *   - No action fires without an explicit click.
 *   - Every paid action is previewed with its cost before it runs.
 */

const invoke = window.__TAURI__?.core?.invoke
  || window.__TAURI_INTERNALS__?.invoke
  || (() => { throw new Error("Tauri IPC bridge unavailable"); })();

const state = {
  niche: "crypto",
  view: "sources",
  tweetFilter: "",
  draftFilter: "",
  creators: [],
  projects: [],
  selectedTweet: null,
  generated: null,
  draftId: null,
  mediaPaths: [],
  projectFolders: [],
  busy: false,
  handle: null,
  previewRevision: 0,
  mediaPreviewPath: null,
  disabledButtons: new Map(),
  gallery: [],
  galleryRevision: 0,
  galleryLimit: 24,
};

// ---- dom helpers -----------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function banner(message, kind = "error") {
  const b = $("#banner");
  if (!message) { b.hidden = true; return; }
  b.hidden = false;
  b.textContent = message;
  b.classList.toggle("is-info", kind === "info");
}

function busy(on, label) {
  state.busy = on;
  if (on) {
    state.disabledButtons = new Map($$(".btn, .niche-btn").map((b) => [b, b.disabled]));
    $$(".btn, .niche-btn").forEach((b) => { b.disabled = true; });
  } else {
    state.disabledButtons.forEach((disabled, b) => { b.disabled = disabled; });
    state.disabledButtons.clear();
    refreshPreview();
  }
  const status = $("#operation-status");
  status.hidden = !on;
  status.replaceChildren();
  if (on) status.append(el("span", { class: "spinner", "aria-hidden": "true" }), label || "Saving changes…");
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function emptyState(title, description, action, view) {
  return el("div", { class: "empty-state" }, icon(view === "create" ? "create" : "sources"),
    el("h3", { text: title }), el("p", { text: description }),
    action ? el("button", { class: "btn btn-quiet", text: action, onclick: () => switchView(view) }) : null);
}

function loadingCards(box, count = 2) {
  box.replaceChildren(...Array.from({ length: count }, () => el("div", { class: "skeleton", "aria-label": "Loading" },
    el("span"), el("span"), el("span"), el("span"))));
}

function displayDate(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime())
    ? date.toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
}

function initials(handle) { return (handle || "X").replace(/^@/, "").slice(0, 2).toUpperCase(); }
function metric(value) { return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(Number(value) || 0); }

/* Money is always shown with enough decimals to be legible at these
 * magnitudes: a single post is $0.015, so 2dp would round it to $0.02. */
function money(v) {
  const n = Number(v || 0);
  if (!Number.isFinite(n)) return "0.0000";
  return n >= 1 ? n.toFixed(2) : n.toFixed(4);
}

function errText(e) {
  if (!e) return "Unknown error";
  if (typeof e === "string") return e;
  if (e.message) return e.message;
  return String(e);
}

// ---- bootstrap -------------------------------------------------------------

async function boot() {
  wireEvents();
  await reload();
}

async function reload() {
  try {
    const info = await invoke("bootstrap", { niche: state.niche });
    state.creators = info.creators || [];
    state.projects = info.projects || [];
    $("#storage-path").textContent = info.data_dir;
    $("#workspace-label").textContent = state.niche === "ai" ? "AI" : "Crypto";
    $("#workspace-niche").textContent = `${state.niche === "ai" ? "AI" : "Crypto"} workspace`;
    switchView(state.view);
    renderCreators();
    renderProjects();
    await Promise.all([loadTweets(), loadDrafts(), loadHistory(), loadMeter(), loadMediaFolders(), refreshAccount()]);
    await refreshPreview();
    banner(null);
  } catch (e) {
    banner(`Startup failed: ${errText(e)}`);
  }
}

async function loadMeter() {
  try {
    const m = await invoke("session_meter", { niche: state.niche });
    $("#meter-value").textContent = `$${money(m.total_cost_usd)}`;
    $("#meter").title =
      `reads $${money(m.reads_cost_usd)} · writes $${money(m.writes_cost_usd)} · logged $${money(m.logged_cost_usd)}`;
  } catch { /* the meter is cosmetic; ignore */ }
}

async function refreshAccount() {
  const el = $("#account-state");
  try {
    const handle = await invoke("identity", { niche: state.niche });
    state.handle = handle;
    el.textContent = handle ? "Connected to X" : "Not connected";
    $("#account-name").textContent = handle ? `@${handle}` : "Your X account";
    $("#account-avatar").textContent = initials(handle);
    $("#account-dot").classList.toggle("is-connected", !!handle);
    $("#preview-name").textContent = handle || "Your account";
    $("#preview-handle").textContent = handle ? `@${handle}` : "@yourhandle";
    $("#preview-avatar").textContent = initials(handle);
    $("#preview-reply-name").textContent = handle || "Your account";
    $(".preview-reply-avatar").textContent = initials(handle);
    $("#btn-auth").hidden = !!handle;
  } catch (e) {
    el.textContent = `Could not verify: ${errText(e)}`;
  }
}

// ---- sources ---------------------------------------------------------------

async function loadTweets() {
  const list = $("#tweet-list");
  loadingCards(list);
  try {
    const rows = await invoke("list_tweets", {
      query: { status: state.tweetFilter || null, limit: 200 },
    });
    list.replaceChildren();
    if (!state.tweetFilter) $("#source-count").textContent = rows.length;
    if (!rows.length) {
      list.append(emptyState(state.tweetFilter ? "Nothing in this collection yet." : "Your next idea is out there.",
        state.tweetFilter ? "Try another filter or fetch fresh posts from your creators." : "Add your favorite creators, then fetch recent posts to start your research.",
        state.creators.length ? null : "Add creators", "settings"));
      return;
    }
    for (const r of rows) list.append(tweetCard(r));
  } catch (e) {
    list.replaceChildren(emptyState("Sources couldn't load.", "Try switching views to load your saved posts again."));
    banner(`Could not load sources: ${errText(e)}`);
  }
}

function tweetCard(r) {
  const t = r.tweet || r;
  const m = t.public_metrics || {};
  const selected = state.selectedTweet?.id === t.id;

  const head = el("div", { class: "tweet-head" },
    el("span", { class: "avatar avatar-small source-avatar", text: initials(t.account_handle), "aria-hidden": "true" }),
    el("div", { class: "tweet-author-info" }, el("span", { class: "tweet-author", text: `@${t.account_handle}` }),
      el("span", { class: "tweet-date", text: displayDate(t.created_at) })),
    r.hot ? el("span", { class: "tag tag-hot", text: "Trending" }) : null,
    el("span", { class: `badge badge-${t.status || "new"}`, text: t.status || "new" }),
  );

  const metrics = el("div", { class: "tweet-metrics" },
    el("span", { text: `♡ ${metric(m.like_count)}`, title: `${m.like_count || 0} likes` }),
    el("span", { text: `↻ ${metric(m.retweet_count)}`, title: `${m.retweet_count || 0} reposts` }),
    el("span", { text: `↩ ${metric(m.reply_count)}`, title: `${m.reply_count || 0} replies` }));

  const card = el("div", {
    class: `tweet${selected ? " is-selected" : ""}`,
    role: "button", tabindex: "0", "aria-label": `Use post by ${t.account_handle} as inspiration`,
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectTweet(t); } },
    onclick: () => selectTweet(t),
  }, head, el("div", { class: "tweet-text", text: t.text }),
    el("div", { class: "tweet-footer" }, metrics, el("span", { class: "source-cta", text: selected ? "Selected ✓" : "Use as inspiration →" })));

  return card;
}

function selectTweet(t) {
  state.selectedTweet = t;
  const box = $("#selected-source");
  box.replaceChildren(
    el("div", { class: "src-author", text: `@${t.account_handle}` }),
    el("div", { class: "src-text", text: t.text }),
  );
  $$(".tweet").forEach((n) => n.classList.remove("is-selected"));
  loadTweets();
  switchView("create");
}

async function doFetch() {
  if (!state.creators.length) { banner("Add at least one creator first."); return; }
  busy(true, "Fetching posts from X…");
  banner(null);
  try {
    const results = await invoke("fetch_recent", { niche: state.niche, handles: state.creators });
    const fresh = results.reduce((n, r) => n + r.new_tweets, 0);
    const cost = results.reduce((n, r) => n + r.estimated_cost_usd, 0);
    await loadTweets();
    await loadMeter();
    banner(`Fetched ${results.reduce((n, r) => n + r.fetched, 0)} posts ` +
      `(${fresh} new) for about $${money(cost)}.`, "info");
  } catch (e) {
    banner(`Fetch failed: ${errText(e)}`);
  } finally {
    busy(false);
    $("#fetch-hint").textContent =
      "Costs about $0.010 per handle plus $0.005 per post. Nothing posts automatically.";
  }
}

// ---- create ----------------------------------------------------------------

function currentBody() { return $("#body-input").value; }
function currentLink() { return $("#link-input").value; }
function currentImages() {
  return [...state.mediaPaths];
}

function draftFromInputs() {
  return {
    id: state.draftId,
    source_tweet_id: state.selectedTweet?.id ?? null,
    body: currentBody(),
    link_url: currentLink().trim() || null,
    quote_tweet_id: null,
    writing_mode: $("#writing-mode").value,
    image_paths: currentImages(),
    tone: "",
    status: state.draftId ? "final" : "draft",
    created_at: null,
    finalized_at: null,
    posted_at: null,
    x_tweet_id: null,
    x_reply_id: null,
    cost_usd: null,
    error: null,
  };
}

async function generate(twoOptions) {
  const t = state.selectedTweet;
  busy(true, twoOptions ? "Generating two options…" : "Generating draft…");
  banner(null);
  try {
    const res = await invoke("generate_draft", {
      req: {
        niche: state.niche,
        source_text: t?.text || "",
        source_author: t?.account_handle || "",
        source_tweet_id: t?.id ?? null,
        writing_mode: $("#writing-mode").value,
        extra_instructions: $("#extra-instructions").value || null,
        image_paths: currentImages(),
        two_options: !!twoOptions,
      },
    });
    state.generated = res;
    renderOptions(res, twoOptions);

    // Prefill: option A (or the single draft) plus the CTA reply.
    const body = twoOptions ? (res.option_a || "") : (res.option_a || "");
    $("#body-input").value = body;
    $("#link-input").value = res.cta_text || "";
    if (res.recommended_media_path && !state.mediaPaths.length) {
      state.mediaPaths = [res.recommended_media_path];
      renderAttachments();
      banner(`Media suggested: ${res.recommended_media_name}`, "info");
    }
    await refreshPreview();
  } catch (e) {
    banner(`Generation failed: ${errText(e)}`);
  } finally {
    busy(false);
  }
}

function renderOptions(res, twoOptions) {
  const box = $("#options");
  const panel = $("#options-panel");
  box.replaceChildren();

  const cards = [];
  if (twoOptions) {
    if (res.option_a) cards.push(["Option A · Rephrase", res.option_a, res.option_a_reasoning]);
    if (res.option_b) cards.push(["Option B · Original take", res.option_b, res.option_b_reasoning]);
  } else if (res.option_a) {
    cards.push(["Draft", res.option_a, res.option_a_reasoning]);
  }
  if (!cards.length) { panel.hidden = true; return; }
  panel.hidden = false;

  for (const [label, text, why] of cards) {
    const pick = (event) => {
      if (event?.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
      event?.preventDefault();
      $$(".option").forEach((n) => n.classList.toggle("is-picked", n === event?.currentTarget));
      $("#body-input").value = text;
      refreshPreview();
    };
    box.append(el("div", {
      class: "option",
      role: "button", tabindex: "0", "aria-label": `Use ${label}`, onclick: pick, onkeydown: pick,
    },
      el("h3", { text: label }),
      el("p", { text }),
      why ? el("div", { class: "why", text: why }) : null,
    ));
  }

  if (res.project_name) {
    box.append(el("p", { class: "hint", text: `Matched project: ${res.project_name} (${res.project_url})` }));
  }
}

async function refreshPreview() {
  const revision = ++state.previewRevision;
  const body = currentBody();
  const link = currentLink();
  $("#preview-body").textContent = body.trim() ? body : "Your next post starts here.";
  $("#preview-body").classList.toggle("is-placeholder", !body.trim());
  $("#reply-preview").hidden = !link.trim();
  $("#preview-reply-body").textContent = link;
  $("#editing-label").textContent = state.draftId ? `DRAFT #${state.draftId}` : "NEW DRAFT";
  $("#publish-note").textContent = state.draftId ? "Publish when you're happy with your draft." : "Save your draft before publishing.";
  refreshMediaPreview();
  // Cheap local counters for immediate feedback while typing.
  $("#main-count").textContent = `${body.length} / 280`;
  $("#reply-count").textContent = `${link.length} / 280`;
  $("#main-count").classList.toggle("is-over", body.length > 280);
  $("#reply-count").classList.toggle("is-over", link.length > 280);
  $("#btn-post").disabled = true;

  const box = $("#validation");
  const costBox = $("#cost-preview");
  if (!body.trim()) {
    box.replaceChildren();
    costBox.replaceChildren(el("span", { class: "hint", text: "Add your post to see the estimated cost." }));
    $("#btn-post").disabled = true;
    return;
  }

  try {
    const p = await invoke("preview_publish", { draft: draftFromInputs() });
    if (revision !== state.previewRevision) return;
    box.replaceChildren();
    if (p.can_post) {
      box.append(el("div", { class: "v-item v-ok", text: "Ready to post. No blocking issues." }));
    } else {
      for (const e of p.errors) {
        box.append(el("div", { class: "v-item" },
          el("span", { text: e.message }),
          el("span", { class: "hint-line", text: e.hint }),
        ));
      }
    }
    // `replaceChildren` stringifies null, so filter the optional rows out
    // rather than passing them through.
    costBox.replaceChildren(
      ...[
        el("span", { class: "big", text: `$${money(p.total)}` }),
        el("span", { text: p.reason }),
        p.saved > 0 ? el("span", { class: "saved", text: `saves $${money(p.saved)}` }) : null,
        p.inline_alternative
          ? el("span", { class: "mono", text: `inline would be $${money(p.inline_alternative)}` })
          : null,
      ].filter(Boolean),
    );
    $("#btn-post").disabled = !p.can_post || !state.draftId || state.busy;
  } catch (e) {
    if (revision !== state.previewRevision) return;
    box.replaceChildren(el("div", { class: "v-item", text: errText(e) }));
    $("#btn-post").disabled = true;
  }
}

async function refreshMediaPreview() {
  const paths = currentImages();
  const signature = JSON.stringify([state.niche, paths]);
  if (state.mediaPreviewPath === signature) return;
  state.mediaPreviewPath = signature;
  const container = $("#preview-media-grid");
  container.querySelectorAll("video").forEach(video => video.pause());
  container.replaceChildren();
  container.hidden = !paths.length;
  container.classList.toggle("multiple", paths.length > 1);
  $(".preview-caption").textContent = "A preview of your post and follow-up reply.";
  await Promise.all(paths.map(async path => {
    const node = mediaElement(path, true);
    container.append(node);
    try {
      const url = await mediaUrl(path);
      if (state.mediaPreviewPath === signature) node.src = url;
    } catch {
      if (state.mediaPreviewPath === signature) $(".preview-caption").textContent = "Attachment selected, but preview unavailable. Check that the file is still in your library.";
    }
  }));
}

async function saveDraft() {
  busy(true, "Saving draft…");
  try {
    const draft = draftFromInputs();
    const id = state.draftId || await invoke("save_draft", { draft });
    if (state.draftId) await invoke("update_draft", { draft });
    state.draftId = id;
    draft.id = id;
    banner(`Draft saved (#${id}).`, "info");
    await Promise.all([loadDrafts(), loadHistory()]);
  } catch (e) {
    banner(`Could not save: ${errText(e)}`);
  } finally {
    busy(false);
  }
}

async function postNow() {
  if (!state.draftId) {
    banner("Save the draft before posting.");
    return;
  }
  busy(true, "Posting to X…");
  banner(null);
  try {
    // Persist the edited text before spending anything.
    await invoke("update_draft", { draft: draftFromInputs() });
    const res = await invoke("post_draft", { niche: state.niche, draftId: state.draftId });
    banner(
      `Posted ${res.x_tweet_id}` +
      (res.x_reply_id ? ` with reply ${res.x_reply_id}` : "") +
      ` for $${money(res.cost_usd)}.`,
      "info",
    );
    await Promise.all([loadDrafts(), loadHistory(), loadMeter()]);
  } catch (e) {
    banner(`Post failed: ${errText(e)}`);
    await Promise.all([loadDrafts(), loadHistory()]);
  } finally {
    busy(false);
  }
}

// ---- queue -----------------------------------------------------------------

async function loadDrafts() {
  const box = $("#draft-list");
  loadingCards(box);
  try {
    const rows = await invoke("list_drafts", {
      query: { status: state.draftFilter || null, limit: 50 },
    });
    box.replaceChildren();
    if (!state.draftFilter) $("#draft-count").textContent = rows.length;
    if (!rows.length) {
      box.append(emptyState(state.draftFilter ? "No drafts with this status." : "Make room for your next post.",
        "Save a draft from the editor and you'll find it here, ready for another look.", "Create a draft", "create"));
      return;
    }
    for (const d of rows) box.append(draftCard(d));
  } catch (e) {
    box.replaceChildren(emptyState("Drafts couldn't load.", "Try switching views to load your saved drafts again."));
    banner(`Could not load drafts: ${errText(e)}`);
  }
}

function draftCard(d) {
  const badge = el("span", { class: `badge badge-${d.status}`, text: d.status });
  const actions = [];

  if (d.status === "draft" || d.status === "final" || d.status === "failed") {
    actions.push(el("button", {
      class: "btn btn-sm",
      text: "Edit",
      onclick: () => editDraft(d),
    }));
    actions.push(el("button", {
      class: "btn btn-sm btn-primary",
      text: "Publish",
      onclick: () => postExisting(d),
    }));
  }
  actions.push(el("button", {
    class: "btn btn-sm btn-danger",
    text: "Delete",
    onclick: async () => {
      try {
        await invoke("delete_draft", { draftId: d.id });
        await loadDrafts();
      } catch (e) { banner(`Delete failed: ${errText(e)}`); }
    },
  }));

  return el("div", { class: "draft" },
    el("div", { class: "draft-head" },
      el("span", { class: "badge", text: `#${d.id}` }),
      badge,
      d.writing_mode ? el("span", { class: "draft-mode", text: d.writing_mode === "original_take" ? "Original take" : "Rephrase" }) : null,
    ),
    el("div", { class: "draft-body", text: d.body }),
    d.link_url ? el("div", { class: "draft-link", text: `reply: ${d.link_url}` }) : null,
    d.cost_usd != null ? el("div", { class: "draft-link", text: `cost: $${money(d.cost_usd)}` }) : null,
    d.x_tweet_id ? el("div", { class: "draft-link", text: `Published on X · ${d.x_tweet_id}` }) : null,
    el("div", { class: "draft-foot" }, el("span", { class: "draft-date", text: displayDate(d.created_at) }), actions),
  );
}

function editDraft(d) {
  state.draftId = d.id;
  $("#body-input").value = d.body || "";
  $("#link-input").value = d.link_url || "";
  $("#writing-mode").value = d.writing_mode || "rephrase";
  state.selectedTweet = d.source_tweet_id ? { id: d.source_tweet_id } : null;
  $("#selected-source").replaceChildren(el("p", { class: "empty", text: d.source_tweet_id ? "This draft was created from a saved source." : "This draft starts with your own idea." }));
  state.mediaPaths = [...(d.image_paths || [])];
  renderAttachments();
  $("#options-panel").hidden = true;
  switchView("create");
  refreshPreview();
}

async function postExisting(d) {
  const p = await invoke("preview_publish", { draft: d });
  const detail = p.errors.map((e) => e.message).join("; ") || p.reason;
  if (!p.can_post) {
    banner(`Cannot post yet: ${detail}`);
    return;
  }
  if (!confirm(`Post this draft for about $${money(p.total)}?\n\n${d.body.slice(0, 140)}`)) return;

  busy(true, "Posting to X…");
  try {
    const res = await invoke("post_draft", { niche: state.niche, draftId: d.id });
    banner(`Posted ${res.x_tweet_id} for $${money(res.cost_usd)}.`, "info");
    await Promise.all([loadDrafts(), loadHistory(), loadMeter()]);
  } catch (e) {
    banner(`Post failed: ${errText(e)}`);
    await loadDrafts();
  } finally {
    busy(false);
  }
}

async function loadHistory() {
  const box = $("#history");
  try {
    const rows = await invoke("recent_history", { limit: 20 });
    box.replaceChildren();
    if (!rows.length) {
      box.append(el("p", { class: "empty", text: "Your published posts will appear here. Take your time with the first one." }));
      return;
    }
    for (const h of rows) {
      box.append(el("div", { class: "history-row" },
        el("span", { class: "mono", text: (h.created_at || "").slice(0, 19).replace("T", " ") }),
        el("span", { class: "history-detail", text: `${h.action} · ${h.result || "-"} ${h.detail || ""}` }),
        el("span", { class: "cost", text: h.cost_usd != null ? `$${money(h.cost_usd)}` : "—" }),
      ));
    }
  } catch (e) {
    banner(`Could not load history: ${errText(e)}`);
  }
}

// ---- creators / projects ---------------------------------------------------

function renderCreators() {
  const list = $("#creator-list");
  list.replaceChildren();
  if (!state.creators.length) {
    list.append(el("p", { class: "empty", text: "No creators configured." }));
  }
  for (const h of state.creators) {
    list.append(el("span", { class: "tag" }, `@${h}`,
      el("button", {
        text: "×",
        title: `Remove ${h}`,
        onclick: () => {
          state.creators = state.creators.filter((x) => x !== h);
          renderCreators();
        },
      })));
  }
  const chips = $("#creator-chips");
  chips.replaceChildren(...state.creators.slice(0, 6).map((h) => el("span", { class: "tag", text: `@${h}` })));
  if (state.creators.length > 6) chips.append(el("span", { class: "tag", text: `+${state.creators.length - 6} more` }));
  if (!state.creators.length) chips.append(el("span", { class: "hint", text: "Add creators to build your research feed." }));
}

function renderProjects() {
  const box = $("#project-list");
  box.replaceChildren();
  if (!state.projects.length) {
    box.append(el("p", { class: "empty", text: "No activations configured." }));
    return;
  }
  state.projects.forEach((p, i) => {
    box.append(el("div", { class: "project-row" },
      el("input", {
        class: "input", value: p.name, "aria-label": "Project name",
        oninput: (e) => { state.projects[i].name = e.target.value; },
      }),
      el("input", {
        class: "input", value: p.url, "aria-label": "Project URL",
        oninput: (e) => { state.projects[i].url = e.target.value; },
      }),
      el("button", {
        class: "btn btn-sm btn-danger", text: "×", "aria-label": `Remove ${p.name}`,
        onclick: () => { state.projects.splice(i, 1); renderProjects(); },
      }),
    ));
  });
}

async function saveCreators() {
  busy(true);
  try {
    const res = await invoke("save_creators", { niche: state.niche, handles: state.creators });
    state.creators = res.handles;
    renderCreators();
    banner(`Watchlist saved. Following ${res.handles.length} creator(s).`, "info");
  } catch (e) {
    banner(`Could not save creators: ${errText(e)}`);
  } finally {
    busy(false);
  }
}

async function saveProjects() {
  busy(true);
  try {
    await invoke("save_projects", { niche: state.niche, projects: state.projects });
    await reload();
    banner("Projects saved.", "info");
  } catch (e) {
    banner(`Could not save projects: ${errText(e)}`);
  } finally {
    busy(false);
  }
}

// ---- media -----------------------------------------------------------------

async function loadMediaFolders() {
  const niche = state.niche;
  try {
    const folders = await invoke("list_project_folders", { niche });
    const groups = await Promise.all(folders.map(async project => {
      const files = await invoke("list_project_media", { niche, projectName: project });
      return files.map(file => ({ ...file, project }));
    }));
    if (niche !== state.niche) return;
    state.projectFolders = folders;
    state.gallery = groups.flat();
    const select = $("#gallery-project");
    const previous = select.value;
    select.replaceChildren(el("option", { value: "", text: "All projects" }),
      ...[...new Set([...folders, ...state.projects.map(p => p.name)])].map(project => el("option", { value: project, text: project })));
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
    renderGallery();
    renderAttachments();
  } catch (error) {
    banner(`Could not load the gallery: ${errText(error)}`);
  }
}

function isVideo(path) { return /\.(mp4|mov|webm)$/i.test(path); }
function mediaElement(path, controls = false) {
  return isVideo(path)
    ? el("video", { class: "media-visual", controls: controls ? "" : null, preload: "metadata", playsinline: "", muted: controls ? null : "", "aria-label": path.split(/[\\/]/).pop() })
    : el("img", { class: "media-visual", alt: path.split(/[\\/]/).pop(), loading: "lazy" });
}
async function mediaUrl(path) {
  const allowed = await invoke("prepare_media_preview", { niche: state.niche, path });
  const convert = window.__TAURI__?.core?.convertFileSrc || window.__TAURI_INTERNALS__?.convertFileSrc;
  if (!convert) throw new Error("Media preview bridge unavailable");
  return convert(allowed);
}
function toggleAttachment(path) {
  if (state.busy) return;
  if (state.mediaPaths.includes(path)) {
    state.mediaPaths = state.mediaPaths.filter(item => item !== path);
  } else {
    if (state.mediaPaths.length && (isVideo(path) || state.mediaPaths.some(isVideo))) {
      banner("Remove the current attachments before choosing a video. A video must be the only attachment.");
      return;
    }
    if (state.mediaPaths.length >= 4) { banner("You can attach up to four images."); return; }
    state.mediaPaths.push(path);
  }
  renderAttachments();
  $$(".gallery-attach").forEach(button => {
    const selected = state.mediaPaths.includes(button.dataset.path);
    button.textContent = selected ? "Remove attachment" : "Attach to draft";
    button.setAttribute("aria-pressed", String(selected));
  });
  refreshPreview();
}
function renderAttachments() {
  const list = $("#attachment-list");
  list.replaceChildren(...state.mediaPaths.map(path => el("span", { class: "attachment-chip" },
    el("span", { text: `${isVideo(path) ? "Video" : "Image"} · ${path.split(/[\\/]/).pop()}` }),
    el("button", { type: "button", text: "×", "aria-label": "Remove attachment", onclick: () => toggleAttachment(path) }))));
  $("#attachment-empty").hidden = !!state.mediaPaths.length;
  $("#gallery-selection").textContent = `${state.mediaPaths.length} attached to current draft`;
  $$(".gallery-attach").forEach(button => {
    const selected = state.mediaPaths.includes(button.dataset.path);
    button.textContent = selected ? "Remove attachment" : "Attach to draft";
    button.setAttribute("aria-pressed", String(selected));
  });
}
let galleryObserver;
function renderGallery() {
  galleryObserver?.disconnect();
  const revision = ++state.galleryRevision;
  const query = $("#gallery-search").value.trim().toLowerCase();
  const project = $("#gallery-project").value;
  const type = $("#gallery-type").value;
  const files = state.gallery.filter(item => (!project || item.project === project)
    && (!type || isVideo(item.path) === (type === "video"))
    && `${item.filename} ${item.description} ${item.project}`.toLowerCase().includes(query));
  $("#gallery-count").textContent = `${files.length} assets`;
  const grid = $("#gallery-grid");
  grid.querySelectorAll("video").forEach(video => video.pause());
  grid.replaceChildren();
  if (!files.length) grid.append(emptyState("Your media library starts here.", "Choose a project and import images or videos, or adjust your filters."));
  galleryObserver = new IntersectionObserver(entries => entries.forEach(async entry => {
    if (!entry.isIntersecting) return;
    galleryObserver.unobserve(entry.target);
    const path = entry.target.dataset.path;
    try {
      const url = await mediaUrl(path);
      if (revision === state.galleryRevision) entry.target.src = url;
    } catch { entry.target.closest(".gallery-card")?.classList.add("preview-unavailable"); }
  }), { rootMargin: "100px" });
  for (const item of files.slice(0, state.galleryLimit)) {
    const visual = mediaElement(item.path, isVideo(item.path));
    visual.dataset.path = item.path;
    const description = el("textarea", { class: "input", rows: "2", "aria-label": `Description for ${item.filename}`, placeholder: "Describe this asset for AI matching" });
    description.value = item.description || "";
    const save = el("button", { class: "btn btn-quiet", text: "Save description", onclick: async () => {
      save.disabled = true;
      try {
        await invoke("register_media_description", { niche: state.niche, projectName: item.project, filename: item.filename, description: description.value });
        item.description = description.value.trim();
        banner("Media description saved.", "info");
      } catch (error) { banner(errText(error)); }
      finally { save.disabled = false; }
    } });
    const selected = state.mediaPaths.includes(item.path);
    grid.append(el("article", { class: "gallery-card" },
      el("div", { class: "gallery-visual" }, visual, el("span", { class: "media-kind", text: isVideo(item.path) ? "VIDEO" : "IMAGE" })),
      el("div", { class: "gallery-details" }, el("span", { class: "small-label", text: item.project }),
        el("h3", { text: item.filename }),
        el("button", { class: "btn gallery-attach", "data-path": item.path, "aria-pressed": String(selected), text: selected ? "Remove attachment" : "Attach to draft", onclick: () => toggleAttachment(item.path) }),
        el("details", {}, el("summary", { text: "Asset description" }), description, save))));
    galleryObserver.observe(visual);
  }
  $("#gallery-more").hidden = files.length <= state.galleryLimit;
}
async function importMedia() {
  const project = $("#gallery-project").value;
  if (!project) { banner("Choose a project in the gallery before importing media."); return; }
  const niche = state.niche;
  busy(true, "Importing media…");
  let imported = 0;
  const failed = [];
  try {
    const paths = await invoke("plugin:dialog|open", { options: { multiple: true, title: "Import images and videos", filters: [{ name: "Media", extensions: ["png", "jpg", "jpeg", "gif", "webp", "avif", "mp4", "mov", "webm"] }] } });
    for (const path of paths ? (Array.isArray(paths) ? paths : [paths]) : []) {
      try {
        await invoke("import_media", { niche, projectName: project, sourcePath: path });
        imported++;
      } catch (error) { failed.push(errText(error)); }
    }
    await loadMediaFolders();
    if (failed.length) banner(`${imported} imported; ${failed.length} failed. ${failed[0]}`);
    else if (imported) banner(`${imported} media file${imported === 1 ? "" : "s"} imported. Choose attachments below.`, "info");
  } catch (error) { banner(`Import failed: ${errText(error)}`); }
  finally { busy(false); }
}

// ---- navigation ------------------------------------------------------------

function switchView(view) {
  if (state.view !== view) $$("video").forEach(video => video.pause());
  state.view = view;
  $$(".view-btn").forEach((b) => {
    const on = b.dataset.view === view;
    b.classList.toggle("is-active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${view}`));
  const pages = {
    sources: ["YOUR RESEARCH DESK", "Find your next idea.", "Collect the posts worth turning into something of your own."],
    create: ["YOUR WRITING STUDIO", "A little inspiration. Your own voice.", "Shape an idea, make it yours, and see exactly how it will look."],
    queue: ["YOUR PUBLISHING DESK", "Good ideas, ready to go.", "Give your drafts another look, then publish when the moment feels right."],
    settings: ["YOUR WORKSPACE", "Set the stage.", "Manage your creator watchlist and the projects behind your posts."],
    gallery: ["YOUR MEDIA LIBRARY", "Give your ideas a visual.", "Browse project images and videos, then attach them to your draft."],
  };
  const [eyebrow, title, subtitle] = pages[view] || pages.sources;
  $("#page-eyebrow").textContent = eyebrow;
  $("#page-title").textContent = title;
  $("#page-sub").textContent = subtitle;
  $("#breadcrumb-view").textContent = view[0].toUpperCase() + view.slice(1);
}

// ---- wiring ----------------------------------------------------------------

function wireEvents() {
  $$(".view-btn").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
  $$("[data-open-settings]").forEach((b) => b.addEventListener("click", () => switchView("settings")));

  $$(".niche-btn").forEach((b) => b.addEventListener("click", async () => {
    if (state.niche === b.dataset.niche) return;
    state.niche = b.dataset.niche;
    // Keep editor text, but never carry a saved draft ID into another database.
    state.draftId = null;
    state.selectedTweet = null;
    state.mediaPaths = [];
    state.gallery = [];
    state.galleryRevision++;
    galleryObserver?.disconnect();
    $("#gallery-project").value = "";
    state.mediaPreviewPath = null;
    $("#options-panel").hidden = true;
    $("#selected-source").replaceChildren(el("p", { class: "empty", text: "Choose a source from your research desk, or start with your own instructions." }));
    $$(".niche-btn").forEach((x) => {
      const on = x.dataset.niche === state.niche;
      x.classList.toggle("is-active", on);
      x.setAttribute("aria-selected", on ? "true" : "false");
    });
    await reload();
  }));

  $$(".filter-group .chip[data-status]").forEach((c) => c.addEventListener("click", () => {
    state.tweetFilter = c.dataset.status;
    $$(".filter-group .chip[data-status]").forEach((x) => x.classList.toggle("is-active", x === c));
    loadTweets();
  }));

  $$(".filter-group .chip[data-dstatus]").forEach((c) => c.addEventListener("click", () => {
    state.draftFilter = c.dataset.dstatus;
    $$(".filter-group .chip[data-dstatus]").forEach((x) => x.classList.toggle("is-active", x === c));
    loadDrafts();
  }));

  $("#btn-fetch").addEventListener("click", doFetch);
  $("#btn-generate").addEventListener("click", () => generate(false));
  $("#btn-two-options").addEventListener("click", () => generate(true));
  $("#btn-save-draft").addEventListener("click", saveDraft);
  $("#btn-post").addEventListener("click", postNow);
  $("#btn-save-creators").addEventListener("click", saveCreators);
  $("#btn-save-projects").addEventListener("click", saveProjects);

  $("#btn-add-creator").addEventListener("click", () => {
    const input = $("#creator-input");
    const v = input.value.trim().replace(/^@/, "");
    if (v && !state.creators.includes(v)) state.creators.push(v);
    input.value = "";
    renderCreators();
  });
  $("#creator-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#btn-add-creator").click();
  });

  $("#btn-add-project").addEventListener("click", () => {
    const name = $("#project-name").value.trim();
    const url = $("#project-url").value.trim();
    if (!name || !url) return;
    state.projects.push({ name, url, description: "", tags: [] });
    $("#project-name").value = "";
    $("#project-url").value = "";
    renderProjects();
  });

  $("#body-input").addEventListener("input", refreshPreview);
  $("#link-input").addEventListener("input", refreshPreview);
  $$("[data-open-gallery]").forEach(button => button.addEventListener("click", () => switchView("gallery")));
  $("#btn-import-media").addEventListener("click", importMedia);
  $("#btn-refresh-gallery").addEventListener("click", loadMediaFolders);
  $("#gallery-search").addEventListener("input", () => { state.galleryLimit = 24; renderGallery(); });
  ["#gallery-project", "#gallery-type"].forEach(selector => $(selector).addEventListener("change", () => { state.galleryLimit = 24; renderGallery(); }));
  $("#gallery-more").addEventListener("click", () => { state.galleryLimit += 24; renderGallery(); });
  $("#gallery-edit-draft").addEventListener("click", () => { switchView("create"); refreshPreview(); });

  $("#btn-auth").addEventListener("click", async () => {
    const button = $("#btn-auth");
    button.disabled = true;
    try {
      await invoke("begin_auth", { niche: state.niche });
      banner("Approve access in your browser. Waiting for X authorization…", "info");
      await invoke("finish_auth");
      await refreshAccount();
      banner("X authorization saved. You can now post.", "info");
    } catch (e) {
      banner(`Could not authorize X: ${errText(e)}`);
    } finally {
      button.disabled = false;
    }
  });
}

boot();
