const state = {
  user: null,
  pendingChallenge: null,
  surveys: [],
  current: null,
  draftQuestions: [],
  view: "surveys",
  tab: "questions",
};

const TYPE_LABELS = {
  open: "Открытый вопрос",
  single: "Один вариант",
  multiple: "Несколько вариантов",
  scale: "Числовая шкала",
};
const STATUS_LABELS = { draft: "Черновик", field: "Полевой этап", processing: "Обработка", done: "Завершено" };
const ROLE_LABELS = { admin: "Администратор", researcher: "Исследователь", interviewer: "Интервьюер" };

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value == null ? "" : value).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const text = await response.text();
  let data = {};
  if (text) {
    try { data = JSON.parse(text); } catch (e) { data = {}; }
  }
  if (!response.ok) {
    throw new Error(data.error || `Ошибка ${response.status}`);
  }
  return data;
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 2600);
}

function show(section) {
  $("auth").classList.toggle("hidden", section !== "auth");
  $("app").classList.toggle("hidden", section !== "app");
}

function setAuthMessage(error, info) {
  $("authError").textContent = error || "";
  $("authInfo").textContent = info || "";
}

function showRegister() {
  $("authTitle").textContent = "Создание администратора";
  $("authSub").textContent = "Первый пользователь получает роль администратора";
  $("registerForm").classList.remove("hidden");
  $("loginForm").classList.add("hidden");
  $("codeForm").classList.add("hidden");
  setAuthMessage();
}

function showLogin() {
  $("authTitle").textContent = "Вход";
  $("authSub").textContent = "Введите почту и пароль — мы отправим код на почту";
  $("registerForm").classList.add("hidden");
  $("loginForm").classList.remove("hidden");
  $("codeForm").classList.add("hidden");
  setAuthMessage();
}

function showCode(challenge, delivered, devCode) {
  state.pendingChallenge = challenge;
  $("authTitle").textContent = "Подтверждение входа";
  $("authSub").textContent = "Введите шестизначный код из письма";
  $("registerForm").classList.add("hidden");
  $("loginForm").classList.add("hidden");
  $("codeForm").classList.remove("hidden");
  $("codeInput").value = devCode || "";
  setAuthMessage(
    "",
    delivered
      ? "Письмо отправлено. Проверьте почту."
      : `SMTP не настроен — код выведен в консоль сервера.${devCode ? ` Тестовый код: ${devCode}` : ""}`
  );
  $("codeInput").focus();
}

async function initAuth() {
  show("auth");
  try {
    const setup = await api("GET", "/api/setup");
    if (setup.needs_admin) { showRegister(); return; }
  } catch (e) { /* игнорируем, покажем вход */ }
  try {
    const session = await api("GET", "/api/session");
    if (session.authenticated) { enterApp(session.user); return; }
  } catch (e) { /* покажем вход */ }
  showLogin();
}

function enterApp(user) {
  state.user = user;
  show("app");
  $("userLabel").textContent = `${user.name} · ${ROLE_LABELS[user.role] || user.role}`;
  $("navTeam").classList.toggle("hidden", user.role !== "admin");
  state.view = "surveys";
  state.current = null;
  renderSurveys();
}

function switchView(view) {
  state.view = view;
  state.current = null;
  $("navSurveys").classList.toggle("active", view === "surveys");
  $("navTeam").classList.toggle("active", view === "team");
  if (view === "team") renderTeam();
  else renderSurveys();
}

async function renderSurveys() {
  const canCreate = state.user.role === "admin" || state.user.role === "researcher";
  const view = $("view");
  view.innerHTML = `
    <div class="section-title">
      <h2>Исследования</h2>
      ${canCreate ? '<button id="newSurveyBtn">Новое исследование</button>' : ""}
    </div>
    <div id="surveysList" class="cards"><p class="muted">Загрузка…</p></div>
  `;
  if (canCreate) $("newSurveyBtn").addEventListener("click", renderNewSurveyForm);
  try {
    const data = await api("GET", "/api/surveys");
    state.surveys = data.surveys;
  } catch (e) {
    $("surveysList").innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  const list = $("surveysList");
  if (!state.surveys.length) {
    list.innerHTML = `<div class="card muted">Пока нет исследований. Создайте первое.</div>`;
    return;
  }
  list.innerHTML = state.surveys.map((survey) => `
    <div class="card clickable" data-id="${survey.id}">
      <h3>${esc(survey.title)}</h3>
      <div class="muted">${esc(survey.description || "Без описания")}</div>
      <div class="meta">
        <span class="badge ${survey.status}">${STATUS_LABELS[survey.status] || survey.status}</span>
        <span>Вопросов: ${survey.question_count}</span>
        <span>Ответов: ${survey.response_count}</span>
      </div>
    </div>
  `).join("");
  list.querySelectorAll(".card[data-id]").forEach((card) => {
    card.addEventListener("click", () => openSurvey(Number(card.dataset.id)));
  });
}

function renderNewSurveyForm() {
  const view = $("view");
  view.innerHTML = `
    <div class="section-title"><h2>Новое исследование</h2><button class="ghost" id="backBtn">Назад</button></div>
    <div class="panel">
      <label for="nsTitle">Название</label>
      <input id="nsTitle" maxlength="200">
      <label for="nsDescription">Описание</label>
      <textarea id="nsDescription" maxlength="4000"></textarea>
      <label for="nsGoal">Цель исследования</label>
      <textarea id="nsGoal" maxlength="2000"></textarea>
      <label for="nsTasks">Задачи (по одной на строке)</label>
      <textarea id="nsTasks" maxlength="4000"></textarea>
      <button id="nsSave" style="margin-top:16px">Создать</button>
    </div>
  `;
  $("backBtn").addEventListener("click", () => switchView("surveys"));
  $("nsSave").addEventListener("click", async () => {
    try {
      const result = await api("POST", "/api/surveys", {
        title: $("nsTitle").value,
        description: $("nsDescription").value,
        goal: $("nsGoal").value,
        tasks: $("nsTasks").value,
      });
      toast("Исследование создано");
      openSurvey(result.id);
    } catch (e) {
      toast(e.message);
    }
  });
}

async function openSurvey(id) {
  try {
    const data = await api("GET", `/api/surveys/${id}`);
    state.current = data.survey;
    state.draftQuestions = data.survey.questions.map((q) => ({
      label: q.label,
      type: q.type,
      required: q.required,
      options: q.options.slice(),
      scale_min: q.scale_min,
      scale_max: q.scale_max,
      scale_step: q.scale_step,
    }));
    state.tab = "questions";
    renderSurvey();
  } catch (e) {
    toast(e.message);
  }
}

function renderSurvey() {
  const survey = state.current;
  const canEdit = state.user.role === "admin" || state.user.role === "researcher";
  const canViewResponses = state.user.role !== "interviewer";
  const tabs = [
    ["questions", "Анкета"],
    ["analytics", "Аналитика"],
    ["responses", "Ответы"],
    ["members", "Команда"],
    ["settings", "Настройки"],
  ].filter(([key]) => (key === "responses" ? canViewResponses : true));

  $("view").innerHTML = `
    <div class="section-title">
      <div>
        <button class="ghost small" id="backBtn">← Назад</button>
        <h2 style="margin-top:10px">${esc(survey.title)}</h2>
      </div>
      <span class="badge ${survey.status}">${STATUS_LABELS[survey.status] || survey.status}</span>
    </div>
    <div class="tabs">
      ${tabs.map(([key, label]) => `<button data-tab="${key}" class="${state.tab === key ? "active" : ""}">${label}</button>`).join("")}
    </div>
    <div id="tabContent"></div>
  `;
  $("backBtn").addEventListener("click", () => switchView("surveys"));
  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tab = btn.dataset.tab;
      renderSurvey();
    });
  });
  const content = $("tabContent");
  if (state.tab === "questions") renderQuestionsTab(content, canEdit);
  else if (state.tab === "analytics") renderAnalyticsTab(content, canEdit);
  else if (state.tab === "responses") renderResponsesTab(content);
  else if (state.tab === "members") renderMembersTab(content, canEdit);
  else renderSettingsTab(content, canEdit);
}

function renderQuestionsTab(container, canEdit) {
  const survey = state.current;
  const locked = survey.response_count > 0;
  container.innerHTML = `
    ${locked ? `<div class="panel"><span class="muted">Анкета заблокирована: уже собрано ответов (${survey.response_count}). Формулировки менять нельзя, чтобы не нарушить данные.</span></div>` : ""}
    <div class="panel">
      <div id="questionsList"></div>
      ${canEdit && !locked ? `
        <div class="inline" style="margin-top:14px">
          <div>
            <label for="newQType">Тип нового вопроса</label>
            <select id="newQType">
              <option value="open">Открытый</option>
              <option value="single">Один вариант</option>
              <option value="multiple">Несколько вариантов</option>
              <option value="scale">Числовая шкала</option>
            </select>
          </div>
          <button id="addQBtn">Добавить вопрос</button>
        </div>
        <button id="saveQuestionsBtn" style="margin-top:16px">Сохранить анкету</button>
      ` : ""}
    </div>
  `;
  renderQuestionEditor($("questionsList"), canEdit && !locked);
  if ($("addQBtn")) {
    $("addQBtn").addEventListener("click", () => {
      syncEditor();
      state.draftQuestions.push({
        label: "", type: $("newQType").value, required: false, options: [], scale_min: 1, scale_max: 5, scale_step: 1,
      });
      renderQuestionEditor($("questionsList"), true);
    });
  }
  if ($("saveQuestionsBtn")) {
    $("saveQuestionsBtn").addEventListener("click", async () => {
      syncEditor();
      for (const [i, q] of state.draftQuestions.entries()) {
        if (!q.label.trim()) { toast(`Заполните формулировку вопроса ${i + 1}`); return; }
        if ((q.type === "single" || q.type === "multiple") && q.options.filter((o) => o.trim()).length < 2) {
          toast(`Вопрос ${i + 1}: нужно минимум два варианта`); return;
        }
      }
      const questions = state.draftQuestions.map((q) => ({
        label: q.label,
        type: q.type,
        required: q.required,
        options: q.options.filter((o) => o.trim()),
        scale_min: q.scale_min,
        scale_max: q.scale_max,
        scale_step: q.scale_step,
      }));
      try {
        await api("PUT", `/api/surveys/${state.current.id}/questions`, { questions });
        toast("Анкета сохранена");
        openSurvey(state.current.id);
      } catch (e) {
        toast(e.message);
      }
    });
  }
}

function renderQuestionEditor(container, editable) {
  if (!state.draftQuestions.length) {
    container.innerHTML = `<p class="muted">Вопросов пока нет.</p>`;
    return;
  }
  container.innerHTML = state.draftQuestions.map((q, index) => {
    const options = (q.type === "single" || q.type === "multiple") ? `
      <div style="margin-top:8px">
        ${q.options.map((option, oi) => `
          <div class="option-row">
            <input data-action="option" data-index="${index}" data-option="${oi}" value="${esc(option)}" placeholder="Вариант ${oi + 1}" ${editable ? "" : "disabled"}>
            ${editable ? `<button class="ghost small" data-action="removeOption" data-index="${index}" data-option="${oi}">✕</button>` : ""}
          </div>
        `).join("")}
        ${editable ? `<button class="ghost small" data-action="addOption" data-index="${index}" style="margin-top:8px">+ вариант</button>` : ""}
      </div>` : "";
    const scale = q.type === "scale" ? `
      <div class="grid2" style="margin-top:8px">
        <div><label>Минимум</label><input type="number" step="any" data-action="scale_min" data-index="${index}" value="${q.scale_min ?? 1}" ${editable ? "" : "disabled"}></div>
        <div><label>Максимум</label><input type="number" step="any" data-action="scale_max" data-index="${index}" value="${q.scale_max ?? 5}" ${editable ? "" : "disabled"}></div>
        <div><label>Шаг</label><input type="number" step="any" data-action="scale_step" data-index="${index}" value="${q.scale_step ?? 1}" ${editable ? "" : "disabled"}></div>
      </div>` : "";
    return `
      <div class="question-item" data-qindex="${index}">
        <div class="qhead">
          <label style="margin:0">Вопрос ${index + 1}</label>
          <div>
            <select data-action="type" data-index="${index}" ${editable ? "" : "disabled"} style="width:auto;display:inline-block">
              ${Object.entries(TYPE_LABELS).map(([value, label]) => `<option value="${value}" ${q.type === value ? "selected" : ""}>${label}</option>`).join("")}
            </select>
            ${editable ? `<button class="ghost small" data-action="removeQuestion" data-index="${index}">Удалить</button>` : ""}
          </div>
        </div>
        <div class="qbody">
          <input data-action="label" data-index="${index}" value="${esc(q.label)}" placeholder="Формулировка вопроса" ${editable ? "" : "disabled"}>
          ${options}
          ${scale}
          <label style="display:flex;align-items:center;gap:8px;margin-top:10px">
            <input type="checkbox" data-action="required" data-index="${index}" ${q.required ? "checked" : ""} ${editable ? "" : "disabled"} style="width:auto">
            Обязательный
          </label>
        </div>
      </div>`;
  }).join("");

  if (!editable) return;
  container.querySelectorAll("[data-action]").forEach((el) => {
    const action = el.dataset.action;
    if (action === "label" || action === "required" || action === "scale_min" || action === "scale_max" || action === "scale_step" || action === "option") return;
    if (action === "type") {
      el.addEventListener("change", () => {
        syncEditor();
        const index = Number(el.dataset.index);
        state.draftQuestions[index].type = el.value;
        if ((el.value === "single" || el.value === "multiple") && state.draftQuestions[index].options.length < 2) {
          state.draftQuestions[index].options = ["", ""];
        }
        if (el.value === "scale" && state.draftQuestions[index].scale_min == null) {
          state.draftQuestions[index].scale_min = 1;
          state.draftQuestions[index].scale_max = 5;
          state.draftQuestions[index].scale_step = 1;
        }
        renderQuestionEditor(container, true);
      });
    } else if (action === "removeQuestion") {
      el.addEventListener("click", () => {
        syncEditor();
        state.draftQuestions.splice(Number(el.dataset.index), 1);
        renderQuestionEditor(container, true);
      });
    } else if (action === "addOption") {
      el.addEventListener("click", () => {
        syncEditor();
        state.draftQuestions[Number(el.dataset.index)].options.push("");
        renderQuestionEditor(container, true);
      });
    } else if (action === "removeOption") {
      el.addEventListener("click", () => {
        syncEditor();
        state.draftQuestions[Number(el.dataset.index)].options.splice(Number(el.dataset.option), 1);
        renderQuestionEditor(container, true);
      });
    }
  });
}

function syncEditor() {
  document.querySelectorAll(".question-item[data-qindex]").forEach((item) => {
    const index = Number(item.dataset.qindex);
    const question = state.draftQuestions[index];
    if (!question) return;
    const label = item.querySelector('[data-action="label"]');
    if (label) question.label = label.value;
    const required = item.querySelector('[data-action="required"]');
    if (required) question.required = required.checked;
    const scaleMin = item.querySelector('[data-action="scale_min"]');
    if (scaleMin) question.scale_min = scaleMin.value === "" ? null : Number(scaleMin.value);
    const scaleMax = item.querySelector('[data-action="scale_max"]');
    if (scaleMax) question.scale_max = scaleMax.value === "" ? null : Number(scaleMax.value);
    const scaleStep = item.querySelector('[data-action="scale_step"]');
    if (scaleStep) question.scale_step = scaleStep.value === "" ? null : Number(scaleStep.value);
    item.querySelectorAll('[data-action="option"]').forEach((input) => {
      question.options[Number(input.dataset.option)] = input.value;
    });
  });
}

async function renderAnalyticsTab(container, canEdit) {
  container.innerHTML = `<div class="panel muted">Загрузка аналитики…</div>`;
  let data;
  try {
    data = await api("GET", `/api/surveys/${state.current.id}/analytics`);
  } catch (e) {
    container.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  const stats = data.stats;
  const analysis = data.analysis;
  container.innerHTML = `
    <div class="panel">
      <div class="section-title">
        <h3>Результаты (${stats.total_responses} анкет)</h3>
        ${canEdit ? `<button id="runAiBtn">Сгенерировать ИИ-анализ</button>` : ""}
      </div>
      <div id="statsBlock"></div>
    </div>
    <div class="panel">
      <h3>Аналитическая записка</h3>
      ${analysis
        ? `<p class="muted" style="margin:8px 0">Источник: ${analysis.source === "deepseek" ? "DeepSeek" : "локальный расчёт"} · ${new Date(analysis.created_at).toLocaleString("ru-RU")}</p>
           <div class="analysis">${esc(analysis.content)}</div>`
        : `<p class="muted" style="margin-top:8px">Записка ещё не сформирована.${data.ai_enabled ? "" : " ИИ недоступен: задайте DEEPSEEK_API_KEY — будет выполнен локальный расчёт."}</p>`}
    </div>
  `;
  renderStats($("statsBlock"), stats);
  if ($("runAiBtn")) {
    $("runAiBtn").addEventListener("click", async () => {
      const btn = $("runAiBtn");
      btn.disabled = true;
      btn.textContent = "Генерация…";
      try {
        await api("POST", `/api/surveys/${state.current.id}/analytics`);
        toast("Анализ готов");
        renderAnalyticsTab(container, canEdit);
      } catch (e) {
        toast(e.message);
        btn.disabled = false;
        btn.textContent = "Сгенерировать ИИ-анализ";
      }
    });
  }
}

function renderStats(container, stats) {
  if (!stats.questions.length) {
    container.innerHTML = `<p class="muted">Вопросов нет.</p>`;
    return;
  }
  container.innerHTML = stats.questions.map((item) => {
    let body = "";
    if (item.type === "single" || item.type === "multiple") {
      const rows = Object.entries(item.counts).map(([option, count]) => `
        <tr>
          <td>${esc(option)}</td>
          <td>${count}</td>
          <td>${item.percent[option]}%</td>
          <td><span class="bar-track"><span class="bar-fill" style="width:${Math.min(item.percent[option], 100)}%"></span></span></td>
        </tr>`).join("");
      body = `<table><thead><tr><th>Вариант</th><th>Ответов</th><th>Доля</th><th></th></tr></thead><tbody>${rows}</tbody></table>
        <p class="muted" style="margin-top:6px">База: ${item.base}${item.type === "multiple" ? " · сумма долей может превышать 100%" : ""}</p>`;
    } else if (item.type === "scale") {
      const rows = Object.entries(item.distribution).map(([value, count]) => `
        <tr><td>${esc(value)}</td><td>${count}</td></tr>`).join("");
      body = `<p>Среднее: <b>${item.mean ?? "—"}</b> · база: ${item.base} · диапазон: ${item.min}…${item.max}</p>
        ${rows ? `<table><thead><tr><th>Значение</th><th>Ответов</th></tr></thead><tbody>${rows}</tbody></table>` : ""}`;
    } else {
      body = item.answers.length
        ? `<ul style="margin-left:18px">${item.answers.map((answer) => `<li>${esc(answer)}</li>`).join("")}</ul>
           <p class="muted" style="margin-top:6px">Показаны первые ${item.answers.length} из ${item.base}.</p>`
        : `<p class="muted">Ответов пока нет.</p>`;
    }
    return `<div class="question-item"><h4>${esc(item.label)}</h4><div class="qbody">${body}</div></div>`;
  }).join("");
}

async function renderResponsesTab(container) {
  const survey = state.current;
  container.innerHTML = `
    <div class="panel">
      <h3>Заполнить анкету</h3>
      <div id="fillForm"></div>
    </div>
    <div class="panel">
      <h3>Полученные ответы</h3>
      <div id="responsesList" class="muted">Загрузка…</div>
    </div>
  `;
  renderFillForm($("fillForm"));
  try {
    const data = await api("GET", `/api/surveys/${survey.id}/responses`);
    if (!data.responses.length) {
      $("responsesList").innerHTML = `<p class="muted">Ответов пока нет.</p>`;
    } else {
      $("responsesList").innerHTML = `
        <table><thead><tr><th>#</th><th>Автор</th><th>Источник</th><th>Дата</th></tr></thead><tbody>
        ${data.responses.map((r) => `<tr><td>${r.id}</td><td>${esc(r.author || "—")}</td><td>${esc(r.source)}</td><td>${new Date(r.created_at).toLocaleString("ru-RU")}</td></tr>`).join("")}
        </tbody></table>`;
    }
  } catch (e) {
    $("responsesList").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}

function renderFillForm(container) {
  const questions = state.current.questions;
  if (!questions.length) {
    container.innerHTML = `<p class="muted">Сначала добавьте вопросы в анкете.</p>`;
    return;
  }
  container.innerHTML = questions.map((q) => {
    let field = "";
    if (q.type === "open") {
      field = `<textarea data-qid="${q.id}"></textarea>`;
    } else if (q.type === "single") {
      field = q.options.map((o, i) => `<label style="display:flex;gap:8px;align-items:center;color:var(--text);margin:4px 0"><input type="radio" name="q${q.id}" value="${esc(o)}" style="width:auto"> ${esc(o)}</label>`).join("");
    } else if (q.type === "multiple") {
      field = q.options.map((o) => `<label style="display:flex;gap:8px;align-items:center;color:var(--text);margin:4px 0"><input type="checkbox" data-qid="${q.id}" value="${esc(o)}" style="width:auto"> ${esc(o)}</label>`).join("");
    } else {
      const step = q.scale_step || 1;
      field = `<input type="number" data-qid="${q.id}" min="${q.scale_min}" max="${q.scale_max}" step="${step}" placeholder="${q.scale_min}…${q.scale_max}">`;
    }
    return `<div class="question-item">
      <div class="qhead"><label style="margin:0">${esc(q.label)}${q.required ? ' <span class="error">*</span>' : ""}</label></div>
      <div class="qbody">${field}</div>
    </div>`;
  }).join("") + `<button id="submitResponseBtn">Отправить ответ</button>`;

  $("submitResponseBtn").addEventListener("click", async () => {
    const answers = {};
    for (const q of questions) {
      if (q.type === "single") {
        const checked = container.querySelector(`input[name="q${q.id}"]:checked`);
        answers[q.id] = checked ? checked.value : "";
      } else if (q.type === "multiple") {
        answers[q.id] = Array.from(container.querySelectorAll(`input[type="checkbox"][data-qid="${q.id}"]:checked`)).map((el) => el.value);
      } else {
        const input = container.querySelector(`[data-qid="${q.id}"]`);
        answers[q.id] = input ? input.value : "";
      }
    }
    try {
      await api("POST", `/api/surveys/${state.current.id}/responses`, { answers });
      toast("Ответ сохранён");
      openSurvey(state.current.id).then(() => { state.tab = "responses"; renderSurvey(); });
    } catch (e) {
      toast(e.message);
    }
  });
}

async function renderMembersTab(container, canEdit) {
  container.innerHTML = `<div class="panel muted">Загрузка…</div>`;
  let members = [];
  try {
    members = (await api("GET", `/api/surveys/${state.current.id}/members`)).members;
  } catch (e) {
    container.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  let users = [];
  if (canEdit) {
    try { users = (await api("GET", "/api/users")).users; } catch (e) { users = []; }
  }
  const memberIds = new Set(members.map((m) => m.id));
  const available = users.filter((u) => !memberIds.has(u.id) && u.is_active);
  container.innerHTML = `
    <div class="panel">
      <h3>Сотрудники исследования</h3>
      <div id="membersList">
        ${members.length ? members.map((m) => `
          <div class="member-row">
            <div>${esc(m.name)} <span class="muted">· ${esc(m.email)} · ${ROLE_LABELS[m.role] || m.role}</span></div>
            ${canEdit ? `<button class="ghost small danger" data-uid="${m.id}">Убрать</button>` : ""}
          </div>`).join("") : `<p class="muted">Сотрудники не назначены.</p>`}
      </div>
    </div>
    ${canEdit ? `
    <div class="panel">
      <h3>Добавить сотрудника</h3>
      ${available.length ? `
        <div class="inline">
          <div>
            <label for="memberSelect">Сотрудник</label>
            <select id="memberSelect">
              ${available.map((u) => `<option value="${u.id}">${esc(u.name)} — ${ROLE_LABELS[u.role] || u.role}</option>`).join("")}
            </select>
          </div>
          <button id="addMemberBtn">Назначить</button>
        </div>` : `<p class="muted">Все сотрудники уже назначены. Создайте новых в разделе «Команда».</p>`}
    </div>` : ""}
  `;
  container.querySelectorAll("[data-uid]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("DELETE", `/api/surveys/${state.current.id}/members/${btn.dataset.uid}`);
        toast("Сотрудник убран");
        renderMembersTab(container, canEdit);
      } catch (e) { toast(e.message); }
    });
  });
  if ($("addMemberBtn")) {
    $("addMemberBtn").addEventListener("click", async () => {
      try {
        await api("POST", `/api/surveys/${state.current.id}/members`, { user_id: Number($("memberSelect").value) });
        toast("Сотрудник назначен");
        renderMembersTab(container, canEdit);
      } catch (e) { toast(e.message); }
    });
  }
}

function renderSettingsTab(container, canEdit) {
  const survey = state.current;
  container.innerHTML = `
    <div class="panel">
      <h3>Настройки исследования</h3>
      <label for="stTitle">Название</label>
      <input id="stTitle" value="${esc(survey.title)}" ${canEdit ? "" : "disabled"}>
      <label for="stDescription">Описание</label>
      <textarea id="stDescription" ${canEdit ? "" : "disabled"}>${esc(survey.description)}</textarea>
      <label for="stGoal">Цель</label>
      <textarea id="stGoal" ${canEdit ? "" : "disabled"}>${esc(survey.goal)}</textarea>
      <label for="stTasks">Задачи</label>
      <textarea id="stTasks" ${canEdit ? "" : "disabled"}>${esc(survey.tasks)}</textarea>
      <label for="stStatus">Статус</label>
      <select id="stStatus" ${canEdit ? "" : "disabled"}>
        ${Object.entries(STATUS_LABELS).map(([value, label]) => `<option value="${value}" ${survey.status === value ? "selected" : ""}>${label}</option>`).join("")}
      </select>
      ${canEdit ? `<button id="saveSettingsBtn" style="margin-top:16px">Сохранить</button>` : ""}
    </div>
    ${state.user.role === "admin" ? `
    <div class="panel">
      <h3>Удаление</h3>
      <p class="muted" style="margin-bottom:12px">Будут удалены анкета, ответы и аналитика. Действие необратимо.</p>
      <button class="danger" id="deleteSurveyBtn">Удалить исследование</button>
    </div>` : ""}
  `;
  if ($("saveSettingsBtn")) {
    $("saveSettingsBtn").addEventListener("click", async () => {
      try {
        await api("PUT", `/api/surveys/${survey.id}`, {
          title: $("stTitle").value,
          description: $("stDescription").value,
          goal: $("stGoal").value,
          tasks: $("stTasks").value,
          status: $("stStatus").value,
        });
        toast("Сохранено");
        state.current.title = $("stTitle").value;
        state.current.status = $("stStatus").value;
        renderSurvey();
      } catch (e) { toast(e.message); }
    });
  }
  if ($("deleteSurveyBtn")) {
    $("deleteSurveyBtn").addEventListener("click", async () => {
      if (!confirm("Удалить исследование безвозвратно?")) return;
      try {
        await api("DELETE", `/api/surveys/${survey.id}`);
        toast("Исследование удалено");
        switchView("surveys");
      } catch (e) { toast(e.message); }
    });
  }
}

async function renderTeam() {
  const view = $("view");
  view.innerHTML = `
    <div class="section-title"><h2>Команда</h2></div>
    <div class="panel">
      <h3>Новый сотрудник</h3>
      <div class="grid2">
        <div><label for="uName">Имя</label><input id="uName"></div>
        <div><label for="uEmail">Почта</label><input id="uEmail" type="email"></div>
        <div><label for="uPassword">Пароль</label><input id="uPassword" type="password"></div>
        <div><label for="uRole">Роль</label>
          <select id="uRole">
            <option value="researcher">Исследователь</option>
            <option value="interviewer">Интервьюер</option>
            <option value="admin">Администратор</option>
          </select>
        </div>
      </div>
      <button id="createUserBtn" style="margin-top:16px">Создать</button>
    </div>
    <div class="panel">
      <h3>Сотрудники</h3>
      <div id="teamList" class="muted">Загрузка…</div>
    </div>
  `;
  $("createUserBtn").addEventListener("click", async () => {
    try {
      await api("POST", "/api/users", {
        name: $("uName").value,
        email: $("uEmail").value,
        password: $("uPassword").value,
        role: $("uRole").value,
      });
      toast("Сотрудник создан");
      renderTeam();
    } catch (e) { toast(e.message); }
  });
  try {
    const users = (await api("GET", "/api/users")).users;
    $("teamList").innerHTML = users.map((u) => `
      <div class="member-row">
        <div>${esc(u.name)} <span class="muted">· ${esc(u.email)}</span> ${u.is_active ? "" : '<span class="badge">отключён</span>'}</div>
        <div style="display:flex;gap:8px;align-items:center">
          <select data-role="${u.id}" style="width:auto">
            ${Object.entries(ROLE_LABELS).map(([value, label]) => `<option value="${value}" ${u.role === value ? "selected" : ""}>${label}</option>`).join("")}
          </select>
          <button class="ghost small" data-toggle="${u.id}" data-active="${u.is_active ? 1 : 0}">${u.is_active ? "Отключить" : "Включить"}</button>
        </div>
      </div>
    `).join("");
    document.querySelectorAll("[data-role]").forEach((select) => {
      select.addEventListener("change", async () => {
        try {
          await api("PATCH", `/api/users/${select.dataset.role}`, { role: select.value });
          toast("Роль обновлена");
          renderTeam();
        } catch (e) { toast(e.message); renderTeam(); }
      });
    });
    document.querySelectorAll("[data-toggle]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api("PATCH", `/api/users/${btn.dataset.toggle}`, { is_active: btn.dataset.active !== "1" });
          renderTeam();
        } catch (e) { toast(e.message); }
      });
    });
  } catch (e) {
    $("teamList").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
}

$("registerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("POST", "/api/register", {
      name: $("regName").value,
      email: $("regEmail").value,
      password: $("regPassword").value,
    });
    const session = await api("GET", "/api/session");
    if (session.authenticated) enterApp(session.user);
  } catch (e) { setAuthMessage(e.message); }
});

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("POST", "/api/login", {
      email: $("loginEmail").value,
      password: $("loginPassword").value,
    });
    showCode(data.challenge, data.delivered, data.dev_code);
  } catch (e) { setAuthMessage(e.message); }
});

$("codeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("POST", "/api/login/verify", {
      challenge: state.pendingChallenge,
      code: $("codeInput").value,
    });
    enterApp(data.user);
  } catch (e) { setAuthMessage(e.message); }
});

$("backToLogin").addEventListener("click", showLogin);
$("logoutBtn").addEventListener("click", async () => {
  try { await api("POST", "/api/logout"); } catch (e) { /* выходим локально */ }
  state.user = null;
  state.current = null;
  $("navTeam").classList.add("hidden");
  showLogin();
});
$("navSurveys").addEventListener("click", () => switchView("surveys"));
$("navTeam").addEventListener("click", () => switchView("team"));

initAuth();
