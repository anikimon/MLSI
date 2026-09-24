const state = { view: "materials", materials: [], current: null, report: null, rubric: null };

const STATUS_CLASS = {
  "соответствует": "ok",
  "частично соответствует": "warn",
  "не соответствует": "bad",
  "не применимо": "na",
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
  toast.timer = setTimeout(() => el.classList.add("hidden"), 3200);
}

async function withLoading(label, fn) {
  const box = $("loading");
  box.textContent = label || "Агенты работают…";
  box.classList.remove("hidden");
  try { return await fn(); } finally { box.classList.add("hidden"); }
}

function switchView(view) {
  state.view = view;
  state.current = null;
  document.querySelectorAll("header nav button").forEach((btn) =>
    btn.classList.toggle("active", btn.dataset.view === view));
  if (view === "rubric") renderRubric(); else renderMaterials();
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

async function renderMaterials() {
  const view = $("view");
  view.innerHTML = `
    <div class="section-title"><h2>Материалы</h2></div>
    <div class="panel">
      <h3>Новая проверка</h3>
      <label for="mTitle">Название</label>
      <input id="mTitle" placeholder="Название материала">
      <div class="grid2">
        <div>
          <label for="mFile">Файл (txt, docx, html, mp3, wav, mp4, mov…)</label>
          <input id="mFile" type="file">
          <div class="file-hint">Аудио и видео требуют установленных ffmpeg и faster-whisper на сервере.</div>
        </div>
        <div>
          <label for="mText">…или вставьте текст</label>
          <textarea id="mText" rows="4" placeholder="Текст статьи, книги, сценария…"></textarea>
        </div>
      </div>
      <button id="uploadBtn" style="margin-top:12px">Загрузить</button>
    </div>
    <div id="materialsList"><p class="spinner">Загрузка…</p></div>
  `;
  $("uploadBtn").addEventListener("click", async () => {
    const file = $("mFile").files[0];
    await withLoading(file ? "Извлекаю текст / распознаю речь…" : "Сохраняю материал…", async () => {
      try {
        const payload = { title: $("mTitle").value };
        if (file) {
          payload.filename = file.name;
          payload.content_base64 = await fileToBase64(file);
        } else {
          payload.text = $("mText").value;
        }
        const data = await api("POST", "/api/materials", payload);
        toast("Материал загружен");
        openMaterial(data.id);
      } catch (e) { toast(e.message); }
    });
  });
  try {
    state.materials = (await api("GET", "/api/materials")).materials;
  } catch (e) { $("materialsList").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
  const list = $("materialsList");
  if (!state.materials.length) {
    list.innerHTML = `<div class="panel muted">Материалов пока нет.</div>`;
    return;
  }
  list.innerHTML = state.materials.map((m) => `
    <div class="material">
      <div>
        <h4>${esc(m.title)}</h4>
        <div class="meta">
          <span class="badge">${esc(m.kind)}</span>
          ${m.analyzed ? '<span class="badge ok">отчёт готов</span>' : '<span class="badge na">не проверен</span>'}
          <span>${esc(new Date(m.created_at).toLocaleString("ru-RU"))}</span>
        </div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="small" data-open="${m.id}">Открыть</button>
        <button class="small ghost danger" data-del="${m.id}">Удалить</button>
      </div>
    </div>`).join("");
  list.querySelectorAll("[data-open]").forEach((btn) =>
    btn.addEventListener("click", () => openMaterial(Number(btn.dataset.open))));
  list.querySelectorAll("[data-del]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm("Удалить материал и отчёт?")) return;
      try { await api("DELETE", `/api/materials/${btn.dataset.del}`); renderMaterials(); }
      catch (e) { toast(e.message); }
    }));
}

async function openMaterial(id) {
  let material;
  try { material = (await api("GET", `/api/materials/${id}`)).material; }
  catch (e) { toast(e.message); return; }
  let report = null;
  try { report = (await api("GET", `/api/materials/${id}/report`)).report; } catch (e) { report = null; }
  state.current = material;
  state.report = report;
  renderMaterialView();
}

function renderMaterialView() {
  const m = state.current;
  const view = $("view");
  view.innerHTML = `
    <div class="section-title">
      <div>
        <button class="ghost small" id="backBtn">← Назад</button>
        <h2 style="margin-top:10px">${esc(m.title)}</h2>
        <div class="meta"><span class="badge">${esc(m.kind)}</span><span>${m.length} символов</span></div>
      </div>
      <button id="analyzeBtn">${state.report ? "Переанализировать" : "Проверить материал"}</button>
    </div>
    <div id="reportBox"></div>
    <div class="panel"><h3>Извлечённый текст</h3><div class="analysis muted">${esc(m.text.slice(0, 6000))}${m.text.length > 6000 ? "…" : ""}</div></div>
  `;
  $("backBtn").addEventListener("click", () => switchView("materials"));
  $("analyzeBtn").addEventListener("click", async () => {
    await withLoading("Сеть агентов проверяет материал…", async () => {
      try {
        await api("POST", `/api/materials/${m.id}/analyze`);
        toast("Отчёт сформирован");
        openMaterial(m.id);
      } catch (e) { toast(e.message); }
    });
  });
  const box = $("reportBox");
  if (!state.report) {
    box.innerHTML = `<div class="panel muted">Отчёт ещё не сформирован. Нажмите «Проверить материал».</div>`;
    return;
  }
  box.innerHTML = renderReport(state.report);
}

function renderReport(report) {
  const index = report.index;
  const counts = index.counts;
  const groups = {};
  report.results.forEach((item) => {
    (groups[item.section] = groups[item.section] || { title: item.section_title, items: [] }).items.push(item);
  });
  const groupHtml = Object.values(groups).map((group) => `
    <div class="provision-group">
      <h4>${esc(group.title)}</h4>
      <table><thead><tr><th>Пункт</th><th>Оценка</th><th>Уверенность</th><th>Комментарий и цитаты</th></tr></thead><tbody>
      ${group.items.map((item) => `
        <tr>
          <td>${esc(item.code)}<br><b>${esc(item.title)}</b></td>
          <td><span class="badge ${STATUS_CLASS[item.status] || "na"}">${esc(item.status)}</span></td>
          <td>${Math.round(item.confidence * 100)}%</td>
          <td>${esc(item.comment)}
            ${(item.evidence || []).map((q) => `<div class="quote">«${esc(q)}»</div>`).join("")}
          </td>
        </tr>`).join("")}
      </tbody></table>
    </div>`).join("");
  const visual = report.visual && report.visual.available
    ? `<div class="panel"><h3>Визуальный анализ (${esc(report.visual.model)})</h3>
        ${report.visual.findings.map((f) => `<div class="quote">Кадр ${f.frame}: ${esc(f.note)}</div>`).join("")}</div>`
    : (report.visual ? `<div class="panel muted"><h3>Визуальный анализ</h3>${esc(report.visual.note)}</div>` : "");
  const gauge = index.compliance_index == null ? 0 : index.compliance_index;
  return `
    <div class="panel">
      <h3>Индекс соответствия</h3>
      <div style="display:flex;align-items:center;gap:14px">
        <div style="font-size:2rem;font-weight:700">${index.compliance_index == null ? "—" : index.compliance_index + "%"}</div>
        <div style="flex:1"><div class="gauge"><div class="gauge-fill" style="width:${gauge}%"></div></div>
        <div class="muted" style="font-size:0.82rem;margin-top:4px">Применимых пунктов: ${index.applicable}</div></div>
      </div>
    </div>
    <div class="grid4">
      <div class="stat-card"><div class="value ok">${counts["соответствует"]}</div><div class="label">Соответствует</div></div>
      <div class="stat-card"><div class="value warn">${counts["частично соответствует"]}</div><div class="label">Частично</div></div>
      <div class="stat-card"><div class="value error">${counts["не соответствует"]}</div><div class="label">Не соответствует</div></div>
      <div class="stat-card"><div class="value muted">${counts["не применимо"]}</div><div class="label">Не применимо</div></div>
    </div>
    <div class="panel" style="margin-top:16px"><h3>Выводы</h3><div class="analysis">${esc(report.summary)}</div>
      <p class="muted" style="margin-top:10px;font-size:0.8rem">Модель: ${esc(report.model)} · движки: ${esc((report.engines || []).join(", "))}</p>
    </div>
    ${visual}
    <div class="panel"><h3>Покомпонентная оценка</h3>${groupHtml}</div>`;
}

async function renderRubric() {
  const view = $("view");
  view.innerHTML = `<div class="section-title"><h2>Рубрика по указу</h2></div><div id="rubricBox"><p class="spinner">Загрузка…</p></div>`;
  let data;
  try { data = state.rubric || (state.rubric = await api("GET", "/api/rubric")); }
  catch (e) { $("rubricBox").innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
  const link = `http://www.kremlin.ru/acts/bank/48502`;
  $("rubricBox").innerHTML = `
    <div class="panel">
      <h3>${esc(data.decree.title)}</h3>
      <p class="muted">${esc(data.decree.number)} от ${esc(data.decree.date)} · ${esc(data.decree.edition)}</p>
      <p class="muted" style="margin-top:8px">${esc(data.decree.note)}</p>
      <p style="margin-top:8px"><a href="${link}" target="_blank" rel="noopener">Официальный текст на kremlin.ru ↗</a></p>
    </div>
    ${data.sections.map((section) => `
      <div class="panel">
        <h3>${esc(section.title)} (${esc(section.code)})</h3>
        <p class="muted" style="margin-bottom:8px">${esc(section.instruction)}</p>
        <table><thead><tr><th>ID</th><th>Пункт</th><th>Что оценивается</th></tr></thead><tbody>
          ${section.provisions.map((p) => `<tr><td>${esc(p.id)}</td><td>${esc(p.title)}</td><td>${esc(p.description)}</td></tr>`).join("")}
        </tbody></table>
      </div>`).join("")}`;
}

document.querySelectorAll("header nav button").forEach((btn) =>
  btn.addEventListener("click", () => switchView(btn.dataset.view)));

async function init() {
  try {
    const health = await api("GET", "/api/health");
    const parts = [health.ai_enabled ? "DeepSeek" : "локально"];
    if (health.ffmpeg) parts.push("ffmpeg");
    if (health.vision) parts.push("vision");
    $("statusBadges").textContent = parts.join(" · ");
    $("statusBadges").className = `badge ${health.ai_enabled ? "ok" : "na"}`;
  } catch (e) { $("statusBadges").textContent = "офлайн"; }
  switchView("materials");
}

init();
