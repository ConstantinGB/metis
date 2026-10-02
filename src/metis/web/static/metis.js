(function () {
  const project = document.body.dataset.project;
  const columns = document.getElementById("columns");

  // ------------------------------------------------------------------ jobs polling (all pages)
  const jobs = document.getElementById("jobs");
  if (jobs && jobs.dataset.active === "true") {
    const tick = async () => {
      const r = await fetch(jobs.dataset.poll);
      jobs.innerHTML = await r.text();
      const inner = jobs.querySelector(".jobs");
      if (!inner || inner.dataset.active !== "true") { clearInterval(timer); location.reload(); }
    };
    const timer = setInterval(tick, 1500);
  }
  if (!columns) return;

  // ------------------------------------------------------------------ state: tabs in the URL
  const stateEl = document.getElementById("metis-state");
  const state = stateEl ? JSON.parse(stateEl.textContent) : { tabs: [{ ids: [], title: "New tab" }], active: 0 };
  const tabsEl = document.getElementById("tabs");
  const MAX_TABS = 12;

  function tab() { return state.tabs[state.active]; }
  function serialize() {
    return state.tabs.map((t) => t.ids.map(encodeURIComponent).join(",")).join("|");
  }
  function url() { return `/p/${project}/browse?tabs=${encodeURIComponent(serialize())}&active=${state.active}`; }
  function pushUrl(replace) { (replace ? history.replaceState : history.pushState).call(history, null, "", url()); }

  function renderTabs() {
    tabsEl.innerHTML = "";
    state.tabs.forEach((t, i) => {
      const el = document.createElement("div");
      el.className = "tab" + (i === state.active ? " active" : "");
      el.dataset.index = i;
      el.innerHTML = `<span class="label"></span>${state.tabs.length > 1 ? '<button class="close" title="Close tab">×</button>' : ""}`;
      el.querySelector(".label").textContent = t.title || "New tab";
      tabsEl.appendChild(el);
    });
    if (state.tabs.length < MAX_TABS) {
      const add = document.createElement("button");
      add.className = "newtab"; add.textContent = "+"; add.title = "New tab";
      tabsEl.appendChild(add);
    }
  }

  function updateTitle() {
    const last = columns.querySelector(".card:last-of-type h2");
    tab().title = last ? last.textContent : "New tab";
    renderTabs();
  }

  async function loadRow() {
    const path = tab().ids.map(encodeURIComponent).join(",");
    const r = await fetch(`/p/${project}/row?path=${encodeURIComponent(path)}`);
    columns.innerHTML = await r.text();
    if (!tab().ids.length) columns.innerHTML = '<div class="hint"><p>Click a repository, folder or file on the left, or search.</p></div>';
    afterCards();
    updateTitle();
  }

  function switchTab(i) { state.active = i; pushUrl(false); loadRow(); }
  function newTab(ids) {
    if (state.tabs.length >= MAX_TABS) return;
    state.tabs.push({ ids: ids || [], title: "New tab" });
    switchTab(state.tabs.length - 1);
  }
  function closeTab(i) {
    if (state.tabs.length <= 1) return;
    state.tabs.splice(i, 1);
    if (state.active >= state.tabs.length) state.active = state.tabs.length - 1;
    else if (i < state.active) state.active -= 1;
    pushUrl(false); loadRow();
  }

  tabsEl.addEventListener("click", (e) => {
    if (e.target.closest(".newtab")) { newTab(); return; }
    const t = e.target.closest(".tab"); if (!t) return;
    const i = +t.dataset.index;
    if (e.target.closest(".close")) closeTab(i); else if (i !== state.active) switchTab(i);
  });

  // ------------------------------------------------------------------ cards
  function truncate(col) {
    columns.querySelectorAll(".card").forEach((c) => { if (+c.dataset.col >= col) c.remove(); });
    const hint = columns.querySelector(".hint"); if (hint) hint.remove();
    tab().ids = tab().ids.slice(0, col - 1);
  }

  function insertCard(html) {
    columns.insertAdjacentHTML("beforeend", html);
    const card = columns.lastElementChild;
    initSource(card);
    card.scrollIntoView({ behavior: "smooth", inline: "end", block: "nearest" });
    return card;
  }

  async function openNode(id, col) {
    truncate(col);
    tab().ids.push(id);
    const r = await fetch(`/p/${project}/card?node=${encodeURIComponent(id)}&col=${col}`);
    if (!r.ok) { insertCard(`<section class="card" data-col="${col}"><p class="error">Could not open ${id}</p></section>`); return; }
    insertCard(await r.text());
    pushUrl(false); updateTitle();
  }

  function afterCards() { columns.querySelectorAll(".card").forEach(initSource); }

  columns.addEventListener("auxclick", (e) => { if (e.button === 1) handleLink(e, true); });
  document.addEventListener("click", (e) => handleLink(e, e.ctrlKey || e.metaKey));

  function handleLink(e, inNewTab) {
    const line = e.target.closest('a[href^="line:"]');
    if (line) { e.preventDefault(); showLine(line.closest(".card"), +line.getAttribute("href").slice(5)); return; }
    const a = e.target.closest('a[href^="node:"]');
    if (!a) return;
    e.preventDefault();
    const id = decodeURIComponent(a.getAttribute("href").slice(5));
    const card = a.closest(".card");
    const col = card ? +card.dataset.col + 1 : 1;
    const scope = card || document.querySelector(".tree");
    scope.querySelectorAll("a.selected").forEach((x) => x.classList.remove("selected"));
    a.classList.add("selected");
    const summary = a.closest("summary");
    if (summary && !summary.closest(".card")) summary.parentElement.open = !summary.parentElement.open;
    if (inNewTab) { newTab(tab().ids.slice(0, col - 1).concat([id])); return; }
    openNode(id, col);
  }

  // ------------------------------------------------------------------ source view
  const HL_KEY = "metis.highlight";
  let highlight = true;
  try { highlight = localStorage.getItem(HL_KEY) !== "off"; } catch (_) {}

  function applyHighlight(card) {
    card.querySelectorAll("pre.src").forEach((p) => p.classList.toggle("plain", !highlight));
    card.querySelectorAll("input.hl").forEach((i) => { i.checked = highlight; });
  }

  async function loadSource(details) {
    if (details.dataset.loaded) return;
    details.dataset.loaded = "1";
    const r = await fetch(details.dataset.src);
    details.querySelector(".src-body").innerHTML = r.ok ? await r.text() : '<p class="error">Could not load source.</p>';
    applyHighlight(details);
  }

  function initSource(card) {
    const details = card.querySelector("details.source");
    if (!details || details.dataset.init) return;
    details.dataset.init = "1";
    applyHighlight(card);
    details.addEventListener("toggle", () => { if (details.open) loadSource(details); });
    const label = details.querySelector(".hl-toggle");
    label.addEventListener("click", (e) => e.stopPropagation());
    label.querySelector("input").addEventListener("change", (e) => {
      highlight = e.target.checked;
      try { localStorage.setItem(HL_KEY, highlight ? "on" : "off"); } catch (_) {}
      document.querySelectorAll(".card").forEach(applyHighlight);
    });
    if (details.dataset.open) { details.open = true; loadSource(details); }
    const deeper = card.querySelector("form.deeper");
    if (deeper) deeper.addEventListener("submit", (e) => { e.preventDefault(); searchDeeper(card, deeper.dataset.q); });
    if (card.dataset.stage === "pending") pollAiCard(card);
  }

  async function showLine(card, n) {
    const details = card && card.querySelector("details.source");
    if (!details) return;
    details.open = true;
    await loadSource(details);
    const ln = details.querySelector(`.ln[data-line="${n}"]`);
    if (!ln) return;
    details.querySelectorAll(".ln.flash").forEach((x) => x.classList.remove("flash"));
    ln.classList.add("flash");
    ln.scrollIntoView({ block: "center" });
  }

  // ------------------------------------------------------------------ search
  const search = document.getElementById("search");
  const aiToggle = document.getElementById("ai-toggle");
  let aiMode = false;
  aiToggle.addEventListener("click", () => {
    aiMode = !aiMode;
    aiToggle.dataset.on = aiMode ? "1" : "0";
    search.placeholder = aiMode ? "Ask: where is the code that…  (Enter)" : "Search files and functions";
    search.focus();
  });

  function replaceFirst(html, id) {
    truncate(1);
    tab().ids = [id];
    insertCard(html);
    pushUrl(true); updateTitle();
  }

  let timer;
  search.addEventListener("input", () => {
    if (aiMode) return;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = search.value.trim();
      if (!q) return;
      const r = await fetch(`/p/${project}/search-card?q=${encodeURIComponent(q)}&col=1`);
      replaceFirst(await r.text(), `q:${q}`);
    }, 250);
  });
  search.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || !aiMode) return;
    e.preventDefault();
    const q = search.value.trim(); if (!q) return;
    replaceFirst(`<section class="card" data-col="1" data-node="ai:${q}"><header><h2>AI search: ${q.replace(/</g, "&lt;")}</h2><div class="subtitle">asking Claude…</div></header><div class="body"><p class="muted">Claude is reading the project index. This takes a few seconds.</p></div></section>`, `ai:${q}`);
    const body = new URLSearchParams({ q, col: "1" });
    const r = await fetch(`/p/${project}/ai-search`, { method: "POST", body });
    const html = r.ok ? await r.text() : `<section class="card" data-col="1" data-node="ai:${q}"><p class="error">AI search failed: ${(await r.text()).slice(0, 300)}</p></section>`;
    truncate(1); tab().ids = [`ai:${q}`]; insertCard(html); updateTitle();
  });

  async function searchDeeper(card, q) {
    card.querySelector("form.deeper button").disabled = true;
    await fetch(`/p/${project}/ai-search/deeper`, { method: "POST", body: new URLSearchParams({ q }) });
    card.dataset.stage = "pending";
    pollAiCard(card);
  }

  function pollAiCard(card) {
    const q = card.dataset.node.slice(3);
    const col = card.dataset.col;
    const poll = setInterval(async () => {
      if (!document.body.contains(card)) { clearInterval(poll); return; }
      const r = await fetch(`/p/${project}/ai-search/card?q=${encodeURIComponent(q)}&col=${col}`);
      const html = await r.text();
      const tmp = document.createElement("div"); tmp.innerHTML = html;
      const fresh = tmp.firstElementChild;
      if (fresh.dataset.stage !== "pending") {
        clearInterval(poll);
        card.replaceWith(fresh); initSource(fresh); updateTitle();
      }
    }, 2000);
  }

  // ------------------------------------------------------------------ history
  window.addEventListener("popstate", () => {
    const p = new URLSearchParams(location.search);
    const tabs = p.get("tabs");
    state.tabs = tabs ? tabs.split("|").map((t) => ({ ids: t.split(",").filter(Boolean).map(decodeURIComponent), title: "" })) : [{ ids: [], title: "New tab" }];
    state.active = Math.min(+(p.get("active") || 0), state.tabs.length - 1);
    loadRow();
  });

  renderTabs();
  afterCards();
  pushUrl(true);
})();
