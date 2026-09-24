const state = { view: "feed", filter: "all", articles: [], reports: [] };

const TYPE_LABELS = {
  country: "страна", organization: "организация", person: "персона",
  group: "группа", unknown: "не определено",
};

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const text = await response.text();
  let data = {};
  if (text) { try { data = JSON.parse(text); } catch (e) { data = {}; } }
  if (!response.ok) throw new Error(data.error || `Ошибка ${response.status}`);
  return data;
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 2800);
}

async function withLoading(label, fn) {
  const box = $("loading");
  box.textContent = label || "Агенты работают…";
  box.classList.remove("hidden");
  try { return await fn(); }
  finally { box.classList.add("hidden"); }
}

function aggClass(level) {
  const index = Math.min(4, Math.floor(Number(level) / 2));
  return `agg-${index}`;
}

function aggBar(level) {
  const value = Math.max(0, Math.min(10, Number(level)));
  return `<span class="agg-bar"><span class="agg-track"><span class="agg-fill ${aggClass(value)}" style="width:${value * 10}%"></span></span> <b>${value}</b>/10</span>`;
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll("header nav button").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.view === view));
  const render = { feed: renderFeed, feeds: renderFeeds, stats: renderStats, reports: renderReports }[view];
  render();
}

async function renderFeed() {
  const view = $("view");
  view.innerHTML = `
    <div class="section-title">
      <h2>Лента материалов</h2>
      <div class="actions">
        <select id="filterSelect" style="width:auto">
          <option value="all" ${state.filter === "all" ? "selected" : ""}>Все материалы</option>
          <option value="unanalyzed" ${state.filter === "unanalyzed" ? "selected" : ""}>Не проанализированные</option>
          <option value="analyzed" ${state.filter === "analyzed" ? "selected" : ""}>Проанализированные</option>
        </select>
        <button id="collectBtn" class="ghost">Собрать новости</button>
        <button id="analyzeBtn">Анализировать новые</button>
      </div>
    </div>
    <div class="panel">
      <h3>Добавить материал вручную</h3>
      <div class="grid2">
        <div><label for="mTitle">Заголовок</label><input id="mTitle"></div>
        <div><label for="mSource">Источник</label><input id="mSource" placeholder="Например, Foreign Policy"></div>
      </div>
      <label for="mText">Текст новости</label>
      <textarea id="mText" rows="4"></textarea>
      <button id="addManualBtn" style="margin-top:10px">Добавить</button>
    </div>
    <div id="articlesList"><p class="spinner">Загрузка…</p></div>
  `;
  $("filterSelect").addEventListener("change", () => { state.filter = $("filterSelect").value; renderFeed(); });
  $("collectBtn").addEventListener("click", async () => {
    await withLoading("Собираю RSS-ленты…", async () => {
      try {
        const data = await api("POST", "/api/collect");
        toast(`Добавлено материалов: ${data.added}${data.errors.length ? `, ошибок: ${data.errors.length}` : ""}`);
        renderFeed();
      } catch (e) { toast(e.message); }
    });
  });
  $("analyzeBtn").addEventListener("click", async () => {
    await withLoading("Сеть агентов анализирует материалы…", async () => {
      try {
        const data = await api("POST", "/api/analyze", {});
        toast(data.processed ? `Проанализировано: ${data.processed}` : "Нет новых материалов для анализа");
        renderFeed();
      } catch (e) { toast(e.message); }
    });
  });
  $("addManualBtn").addEventListener("click", async () => {
    try {
      await api("POST", "/api/articles", {
        title: $("mTitle").value, source: $("mSource").value || "Ручная вставка", text: $("mText").value,
      });
      toast("Материал добавлен");
      renderFeed();
    } catch (e) { toast(e.message); }
  });

  try {
    const data = await api("GET", `/api/articles?only=${state.filter}&limit=200`);
    state.articles = data.articles;
  } catch (e) {
    $("articlesList").innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  const list = $("articlesList");
  if (!state.articles.length) {
    list.innerHTML = `<div class="panel muted">Материалов нет. Нажмите «Собрать новости» или добавьте текст вручную.</div>`;
    return;
  }
  list.innerHTML = state.articles.map(renderArticle).join("");
  list.querySelectorAll("[data-analyze]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await withLoading("Агенты анализируют материал…", async () => {
        try { await api("POST", `/api/articles/${btn.dataset.analyze}/analyze`); toast("Материал проанализирован"); renderFeed(); }
        catch (e) { toast(e.message); }
      });
    }));
  list.querySelectorAll("[data-delete]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm("Удалить материал?")) return;
      try { await api("DELETE", `/api/articles/${btn.dataset.delete}`); renderFeed(); }
      catch (e) { toast(e.message); }
    }));
}

function renderArticle(article) {
  const analyzed = article.analyzed;
  const date = new Date(article.published_at || article.created_at).toLocaleString("ru-RU");
  const translated = analyzed && article.translated_title && article.translated_title !== article.title
    ? `<div class="text"><b>${esc(article.translated_title)}</b></div>` : "";
  const body = article.text && article.text !== article.title
    ? `<div class="text muted">${esc(article.text.slice(0, 400))}${article.text.length > 400 ? "…" : ""}</div>` : "";
  const link = article.link ? `<a href="${esc(article.link)}" target="_blank" rel="noopener">Источник ↗</a>` : "";
  return `
    <div class="article">
      <h3>${esc(article.title)}</h3>
      <div class="meta">
        <span class="badge">${esc(article.source)}</span>
        <span>${esc(date)}</span>
        <span>${esc(TYPE_LABELS[article.target_type] || article.target_type || "")}</span>
        <span style="margin-left:auto">${link}</span>
      </div>
      ${translated}
      ${analyzed ? `
        <div class="meta">
          <span class="badge ${article.tonality}">Тональность: ${article.tonality} (${article.tonality_score})</span>
          <span class="badge target">Объект: ${esc(article.target)}</span>
          <span>Агрессия: ${aggBar(article.aggression_level)}</span>
        </div>
        <div class="reason">Тональность: ${esc(article.tonality_reason)}</div>
        <div class="reason">Агрессия: ${esc(article.aggression_reason)}</div>
        <div class="reason">Объект: ${esc(article.target_reason)} · движки: ${esc((article.engines || []).join(", "))} · ${esc(article.model || "")}</div>
      ` : `<div class="reason">Материал ещё не проанализирован.</div>`}
      ${body}
      <div class="actions" style="margin-top:10px">
        <button class="small" data-analyze="${article.id}">${analyzed ? "Переанализировать" : "Анализировать"}</button>
        <button class="small ghost danger" data-delete="${article.id}">Удалить</button>
      </div>
    </div>`;
}

async function renderFeeds() {
  const view = $("view");
  view.innerHTML = `
    <div class="section-title"><h2>RSS-источники</h2></div>
    <div class="panel">
      <h3>Добавить источник</h3>
      <div class="grid2">
        <div><label for="fName">Название</label><input id="fName"></div>
        <div><label for="fUrl">RSS URL</label><input id="fUrl" placeholder="https://..."></div>
      </div>
      <button id="addFeedBtn" style="margin-top:10px">Добавить</button>
    </div>
    <div class="panel"><h3>Список</h3><div id="feedsList" class="muted">Загрузка…</div></div>
  `;
  $("addFeedBtn").addEventListener("click", async () => {
    try {
      await api("POST", "/api/feeds", { name: $("fName").value, url: $("fUrl").value });
      toast("Источник добавлен");
      renderFeeds();
    } catch (e) { toast(e.message); }
  });
  try {
    const data = await api("GET", "/api/feeds");
    $("feedsList").innerHTML = data.feeds.map((feed) => `
      <div class="list-row">
        <div>${esc(feed.name)} <span class="muted">· ${esc(feed.url)} · ${esc(feed.language)}</span></div>
        <div style="display:flex;gap:8px">
          <button class="small ghost" data-collect="${feed.id}">Собрать</button>
          <button class="small ghost danger" data-remove="${feed.id}">Удалить</button>
        </div>
      </div>`).join("");
    document.querySelectorAll("[data-collect]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        await withLoading(`Собираю «${btn.parentElement.parentElement.textContent.split("·")[0].trim()}»…`, async () => {
          try {
            const data = await api("POST", "/api/collect", { feed_id: Number(btn.dataset.collect) });
            const result = data.results[0];
            toast(result.error ? `Ошибка: ${result.error}` : `Добавлено: ${result.added}`);
            renderFeeds();
          } catch (e) { toast(e.message); }
        });
      }));
    document.querySelectorAll("[data-remove]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        try { await api("DELETE", `/api/feeds/${btn.dataset.remove}`); renderFeeds(); }
        catch (e) { toast(e.message); }
      }));
  } catch (e) {
    $("feedsList").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}

async function renderStats() {
  const view = $("view");
  view.innerHTML = `<div class="section-title"><h2>Сводка по агрессии и тональности</h2></div><div id="statsBody"><p class="spinner">Загрузка…</p></div>`;
  let data;
  try { data = await api("GET", "/api/stats"); }
  catch (e) { $("statsBody").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
  const stats = data.stats;
  const targets = (stats.targets || []).map(([name, count]) =>
    `<tr><td>${esc(name)}</td><td>${count}</td></tr>`).join("");
  const sources = (stats.sources || []).map(([name, count]) =>
    `<tr><td>${esc(name)}</td><td>${count}</td></tr>`).join("");
  const aggressive = (stats.most_aggressive || []).map((item) =>
    `<tr><td>${esc(item.title)}</td><td>${esc(item.source)}</td><td>${aggBar(item.level)}</td></tr>`).join("");
  $("statsBody").innerHTML = `
    <div class="grid4">
      <div class="stat-card"><div class="value">${stats.analyzed_articles}</div><div class="label">Проанализировано (из ${stats.total_articles})</div></div>
      <div class="stat-card"><div class="value">${stats.avg_aggression}</div><div class="label">Средняя агрессия /10</div></div>
      <div class="stat-card"><div class="value">${stats.avg_tonality}</div><div class="label">Средняя тональность</div></div>
      <div class="stat-card"><div class="value">${stats.high_aggression_share}%</div><div class="label">Доля с агрессией 7+</div></div>
    </div>
    <div class="grid2" style="margin-top:16px">
      <div class="panel"><h3>Объекты агрессии</h3>${targets ? `<table><tr><th>Объект</th><th>Упоминаний</th></tr>${targets}</table>` : `<p class="muted">Нет данных.</p>`}</div>
      <div class="panel"><h3>Источники</h3>${sources ? `<table><tr><th>Источник</th><th>Материалов</th></tr>${sources}</table>` : `<p class="muted">Нет данных.</p>`}</div>
    </div>
    <div class="panel"><h3>Наиболее агрессивные материалы</h3>${aggressive ? `<table><tr><th>Заголовок</th><th>Источник</th><th>Агрессия</th></tr>${aggressive}</table>` : `<p class="muted">Нет данных.</p>`}</div>
    <div class="panel"><h3>Доли тональности</h3>
      <p>Негативные: ${stats.tonality_counts.negative} · Нейтральные: ${stats.tonality_counts.neutral} · Позитивные: ${stats.tonality_counts.positive}</p>
    </div>`;
}

async function renderReports() {
  const view = $("view");
  view.innerHTML = `
    <div class="section-title">
      <h2>Аналитические отчёты</h2>
      <button id="genReportBtn">Сформировать отчёт</button>
    </div>
    <div id="reportBody"></div>`;
  $("genReportBtn").addEventListener("click", async () => {
    await withLoading("Агент-аналитик формирует выводы…", async () => {
      try {
        const data = await api("POST", "/api/reports", {});
        toast("Отчёт сформирован");
        renderReports();
        showReport(data.report);
      } catch (e) { toast(e.message); }
    });
  });
  try {
    const data = await api("GET", "/api/reports");
    state.reports = data.reports;
  } catch (e) { $("reportBody").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
  if (!state.reports.length) {
    $("reportBody").innerHTML = `<div class="panel muted">Отчётов пока нет. Сначала проанализируйте материалы, затем сформируйте отчёт.</div>`;
    return;
  }
  $("reportBody").innerHTML = `<div class="panel"><h3>История</h3>${state.reports.map((r) => `
    <div class="list-row"><div>${esc(r.title)} <span class="muted">· ${esc(r.model)}</span></div>
    <button class="small ghost" data-report="${r.id}">Открыть</button></div>`).join("")}</div>
    <div id="reportView"></div>`;
  document.querySelectorAll("[data-report]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      try {
        const data = await api("GET", `/api/reports/${btn.dataset.report}`);
        showReport(data.report);
      } catch (e) { toast(e.message); }
    }));
}

function showReport(report) {
  $("reportView").innerHTML = `
    <div class="panel">
      <h3>${esc(report.title)}</h3>
      <div class="analysis">${esc(report.content)}</div>
      <p class="muted" style="margin-top:10px">Модель: ${esc(report.model)}</p>
    </div>`;
}

document.querySelectorAll("header nav button").forEach((btn) =>
  btn.addEventListener("click", () => switchView(btn.dataset.view)));

async function init() {
  try {
    const health = await api("GET", "/api/health");
    $("aiStatus").textContent = health.ai_enabled ? "DeepSeek" : "локальный режим";
    $("aiStatus").className = `badge ${health.ai_enabled ? "positive" : "muted"}`;
  } catch (e) { $("aiStatus").textContent = "офлайн"; }
  switchView("feed");
}

init();
