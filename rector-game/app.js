(function () {
  'use strict';

  var DATA = window.RECTOR_DOCS || { decree: {}, documents: [] };
  var DECK = Array.isArray(DATA.documents) ? DATA.documents : [];
  var DECREE = DATA.decree || {
    title: 'Указ Президента Российской Федерации от 09.11.2022 № 809',
    url: 'http://www.kremlin.ru/acts/bank/48502'
  };
  var FEEDBACK = ['Переделать', 'Почти', 'Чуть', 'Чуть подредактируйте', 'Сделайте красиво'];
  var MAX_REPRIMANDS = 3;
  var DOCS_PER_DAY = 24;
  var METER_SEGMENTS = 12;
  var BEST_KEY = 'rector-best-score';

  var $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text != null) { node.textContent = text; }
    return node;
  }
  function fmt(n) { return (Math.round(n * 100) / 100).toFixed(2).replace('.', ','); }

  // ----- recipients (derived from the deck) -----
  var deptMap = new Map();
  var prMap = new Map();
  DECK.forEach(function (doc) {
    if (!doc || !doc.destKey) { return; }
    if (String(doc.destKey).indexOf('prorector-') === 0) { prMap.set(doc.destKey, doc.destName); }
    else { deptMap.set(doc.destKey, doc.destName); }
  });
  var ROUTE_GROUPS = [
    { title: 'Подчинённые подразделения', items: Array.from(deptMap.entries()) },
    { title: 'Другие проректоры', items: Array.from(prMap.entries()) }
  ];

  // ----- DOM -----
  var startScreen = $('start');
  var gameScreen = $('game');
  var overScreen = $('over');
  var meterEl = $('meter');
  var hudDay = $('hudDay');
  var hudRep = $('hudRep');
  var hudScore = $('hudScore');
  var hudRemain = $('hudRemain');
  var timeFill = $('timeFill');
  var cardKind = $('cardKind');
  var cardCase = $('cardCase');
  var cardSender = $('cardSender');
  var chipTheme = $('chipTheme');
  var chipLevel = $('chipLevel');
  var cardBody = $('cardBody');
  var cardSlides = $('cardSlides');
  var actions = $('actions');
  var stampEl = $('stamp');
  var overTitle = $('overTitle');
  var overText = $('overText');
  var overStats = $('overStats');
  var helpModal = $('help');

  var scene = $('scene');
  var ctx = scene.getContext('2d');
  ctx.imageSmoothingEnabled = false;

  for (var s = 0; s < METER_SEGMENTS; s += 1) { meterEl.appendChild(el('i', 'seg')); }

  var state = null;
  var stampTimer = null;

  function newState() {
    var order = [];
    for (var i = 0; i < DECK.length; i += 1) { order.push(i); }
    for (var j = order.length - 1; j > 0; j -= 1) {
      var k = Math.floor(Math.random() * (j + 1));
      var tmp = order[j]; order[j] = order[k]; order[k] = tmp;
    }
    return {
      order: order,
      pos: 0,
      reprimands: 0,
      score: 0,
      docSeen: 0,
      docCorrect: 0,
      presSeen: 0,
      routeSeen: 0,
      routeCorrect: 0,
      step: 'verdict',
      doc: null,
      verdict: null,
      feedback: null,
      dest: null,
      mood: 'idle'
    };
  }

  function show(screen) {
    [startScreen, gameScreen, overScreen].forEach(function (node) { node.classList.add('hidden'); });
    screen.classList.remove('hidden');
    window.scrollTo(0, 0);
  }

  function startGame() {
    if (!DECK.length) {
      overTitle.textContent = 'НЕТ ДОКУМЕНТОВ';
      overText.textContent = 'Не удалось загрузить docs.js.';
      show(overScreen);
      return;
    }
    state = newState();
    show(gameScreen);
    loadDoc();
  }

  function loadDoc() {
    var doc = DECK[state.order[state.pos]];
    state.doc = doc;
    state.verdict = null;
    state.feedback = null;
    state.dest = null;
    state.step = 'verdict';
    state.mood = 'idle';
    hideStamp();
    render();
  }

  function render() {
    renderHud();
    renderCard();
    renderActions();
    drawScene();
  }

  function renderHud() {
    var day = Math.floor(state.pos / DOCS_PER_DAY) + 1;
    hudDay.textContent = String(day);
    hudScore.textContent = String(state.score);
    hudRemain.textContent = String(DECK.length - state.pos);
    hudRep.textContent = fmt(state.reprimands) + ' / ' + MAX_REPRIMANDS;
    timeFill.style.width = ((state.pos % DOCS_PER_DAY) / DOCS_PER_DAY * 100) + '%';

    var level = state.reprimands >= 2.25 ? 'level-3' : (state.reprimands >= 1.25 ? 'level-2' : '');
    meterEl.className = 'meter' + (level ? ' ' + level : '');
    var segs = meterEl.children;
    for (var i = 0; i < segs.length; i += 1) {
      segs[i].className = 'seg' + (state.reprimands >= (i + 1) * (MAX_REPRIMANDS / METER_SEGMENTS) - 1e-9 ? ' on' : '');
    }
  }

  function renderCard() {
    var doc = state.doc;
    cardKind.textContent = doc.kind;
    cardCase.textContent = '№ ' + doc.case;
    cardSender.textContent = doc.sender;
    chipTheme.textContent = 'ТЕМА: ' + doc.themeLabel;
    chipLevel.textContent = doc.level === 'coord'
      ? 'УРОВЕНЬ: КООРДИНАЦИЯ (проректор)'
      : 'УРОВЕНЬ: ИСПОЛНЕНИЕ (подразделение)';

    if (doc.type === 'presentation') {
      cardBody.classList.add('hidden');
      cardSlides.classList.remove('hidden');
      cardSlides.innerHTML = '';
      var ul = el('ul');
      doc.slides.forEach(function (slide) { ul.appendChild(el('li', null, slide)); });
      cardSlides.appendChild(ul);
    } else {
      cardSlides.classList.add('hidden');
      cardBody.classList.remove('hidden');
      cardBody.textContent = doc.body;
    }
  }

  function renderActions() {
    actions.innerHTML = '';
    var doc = state.doc;
    if (state.step === 'verdict') {
      renderVerdict(doc);
    } else if (state.step === 'feedback') {
      renderFeedback();
    } else if (state.step === 'route') {
      renderRoute();
    }
  }

  function veredictBtn(label, variant, handler) {
    var b = el('button', 'pixel-btn ' + variant + ' wide', label);
    b.type = 'button';
    b.addEventListener('click', handler);
    return b;
  }

  function renderVerdict(doc) {
    var title = el('div', 'block-title', 'Вердикт');
    actions.appendChild(title);
    var row = el('div', 'verdict-row');
    if (doc.type === 'document') {
      row.appendChild(veredictBtn('СООТВЕТСТВУЕТ УКАЗУ', 'green', function () { chooseVerdict(true); }));
      row.appendChild(veredictBtn('НЕ СООТВЕТСТВУЕТ', 'red', function () { chooseVerdict(false); }));
    } else {
      row.appendChild(veredictBtn('НРАВИТСЯ', 'green', function () { chooseVerdict('like'); }));
      row.appendChild(veredictBtn('НЕ НРАВИТСЯ', 'red', function () { chooseVerdict('dislike'); }));
    }
    actions.appendChild(row);
  }

  function renderFeedback() {
    actions.appendChild(el('div', 'block-title', 'Не понравилось. Дайте обратную связь:'));
    var row = el('div', 'feedback-row');
    FEEDBACK.forEach(function (option) {
      var b = el('button', 'pixel-btn amber', option);
      b.type = 'button';
      b.addEventListener('click', function () { chooseFeedback(option); });
      row.appendChild(b);
    });
    actions.appendChild(row);
  }

  function renderRoute() {
    var doc = state.doc;
    var block = el('div', 'route-block');
    var info = el('div', 'block-title', 'Расписать документ по теме и уровню');
    block.appendChild(info);
    if (doc.type === 'presentation' && state.feedback) {
      block.appendChild(el('div', 'chip', 'Обратная связь: ' + state.feedback));
    }

    var buttons = [];
    var confirm = el('button', 'pixel-btn primary wide', 'ОТПРАВИТЬ');
    confirm.type = 'button';
    confirm.disabled = !state.dest;
    confirm.addEventListener('click', submitDecision);

    function select(key) {
      state.dest = key;
      buttons.forEach(function (entry) {
        entry.button.classList.toggle('selected', entry.key === key);
      });
      confirm.disabled = false;
    }

    ROUTE_GROUPS.forEach(function (group) {
      if (!group.items.length) { return; }
      var groupNode = el('div', 'route-group');
      groupNode.appendChild(el('div', 'route-group-title', group.title));
      group.items.forEach(function (entry) {
        var key = entry[0];
        var name = entry[1];
        var b = el('button', 'route-item', name);
        b.type = 'button';
        if (state.dest === key) { b.classList.add('selected'); }
        b.addEventListener('click', function () { select(key); });
        buttons.push({ key: key, button: b });
        groupNode.appendChild(b);
      });
      block.appendChild(groupNode);
    });

    block.appendChild(confirm);
    actions.appendChild(block);
  }

  function chooseVerdict(value) {
    state.verdict = value;
    if (state.doc.type === 'presentation' && value === 'dislike') {
      state.step = 'feedback';
      renderActions();
      return;
    }
    state.step = 'route';
    renderActions();
  }

  function chooseFeedback(option) {
    state.feedback = option;
    state.step = 'route';
    renderActions();
  }

  function submitDecision() {
    if (!state.dest) { return; }
    var doc = state.doc;
    var rep = 0;
    var lines = [];

    if (doc.type === 'document') {
      var verdictOk = state.verdict === doc.compliant;
      state.docSeen += 1;
      if (verdictOk) {
        state.docCorrect += 1;
        state.score += 20;
        lines.push('Вердикт по указу верный.');
      } else {
        rep += 1;
        lines.push('Ошибка: документ ' + (doc.compliant ? 'СООТВЕТСТВУЕТ' : 'НЕ СООТВЕТСТВУЕТ') + ' Указу № 809.');
      }
      lines.push(doc.explanation);
    } else {
      state.presSeen += 1;
      state.score += 10;
      lines.push(state.feedback
        ? 'Обратная связь отправлена: «' + state.feedback + '».'
        : 'Презентация одобрена без замечаний.');
    }

    var routeOk = state.dest === doc.destKey;
    state.routeSeen += 1;
    if (routeOk) {
      state.routeCorrect += 1;
      state.score += 5;
      lines.push('Направлено верно: ' + doc.destName + '.');
    } else {
      rep += 0.25;
      lines.push('Адресат вернул документ с пометкой об ошибке. Правильно: ' + doc.destName + '.');
    }

    state.reprimands += rep;
    state.mood = rep === 0 ? 'happy' : 'angry';
    if (rep > 0) { showStamp('ВЫГОВОР +' + fmt(rep), 'bad'); } else { showStamp('ВЕРНО', 'good'); }
    renderHud();
    drawScene();
    renderResult(lines, rep);
  }

  function renderResult(lines, rep) {
    actions.innerHTML = '';
    var box = el('div', 'result ' + (rep > 0 ? 'bad' : 'good'));
    lines.forEach(function (line) { box.appendChild(el('p', 'result-line', line)); });
    box.appendChild(el('div', 'result-rep', rep > 0
      ? 'Выговоры: ' + fmt(state.reprimands) + ' / ' + MAX_REPRIMANDS
      : 'Нарушений нет'));
    var next = el('button', 'pixel-btn primary wide', 'ДАЛЕЕ');
    next.type = 'button';
    next.addEventListener('click', nextDoc);
    box.appendChild(next);
    actions.appendChild(box);
  }

  function nextDoc() {
    state.pos += 1;
    if (state.reprimands >= MAX_REPRIMANDS) { gameOver('fire'); return; }
    if (state.pos >= state.order.length) { gameOver('done'); return; }
    loadDoc();
  }

  function gameOver(reason) {
    show(overScreen);
    if (reason === 'fire') {
      overTitle.textContent = 'ВАС ОСВОБОДИЛИ ОТ ДОЛЖНОСТИ';
      overText.textContent = 'Накопилось три выговора. Приёмная закрыта, кабинет опечатан.';
    } else {
      overTitle.textContent = 'СМЕНА ОКОНЧЕНА';
      overText.textContent = 'Все документы разобраны, вы устояли на посту.';
    }

    var processed = state.pos;
    var docAcc = state.docSeen ? Math.round(state.docCorrect / state.docSeen * 100) : 100;
    var routeAcc = state.routeSeen ? Math.round(state.routeCorrect / state.routeSeen * 100) : 100;
    var best = 0;
    try { best = parseInt(localStorage.getItem(BEST_KEY) || '0', 10) || 0; } catch (e) { best = 0; }
    if (state.score > best) {
      best = state.score;
      try { localStorage.setItem(BEST_KEY, String(best)); } catch (e) { /* ignore */ }
    }

    overStats.innerHTML = '';
    [
      { num: String(processed), cap: 'Обработано' },
      { num: docAcc + '%', cap: 'Верно по указу' },
      { num: routeAcc + '%', cap: 'Верно по адресу' },
      { num: String(state.score), cap: 'Очки' },
      { num: fmt(state.reprimands), cap: 'Выговоры' },
      { num: String(best), cap: 'Рекорд' }
    ].forEach(function (stat) {
      var box = el('div', 'stat');
      box.appendChild(el('div', 'num', stat.num));
      box.appendChild(el('div', 'cap', stat.cap));
      overStats.appendChild(box);
    });
  }

  // ----- stamp -----
  function showStamp(text, kind) {
    if (stampTimer) { clearTimeout(stampTimer); }
    stampEl.textContent = text;
    stampEl.className = 'stamp ' + kind;
    void stampEl.offsetWidth;
    stampEl.classList.add('show');
    stampTimer = setTimeout(function () { stampEl.classList.remove('show'); }, 1600);
  }
  function hideStamp() {
    if (stampTimer) { clearTimeout(stampTimer); }
    stampEl.className = 'stamp hidden';
  }

  // ----- pixel scene -----
  function px(x, y, w, h, color) {
    ctx.fillStyle = color;
    ctx.fillRect(x, y, w, h);
  }

  function drawAvatar(mood) {
    px(22, 62, 34, 30, '#2b3a67');
    px(33, 62, 12, 30, '#eef2ff');
    px(38, 64, 3, 24, mood === 'angry' ? '#8e1010' : '#c0392b');
    px(34, 56, 10, 8, '#e8b98a');
    px(24, 37, 4, 12, '#5a3a1a');
    px(50, 37, 4, 12, '#5a3a1a');
    px(26, 34, 26, 24, '#e8b98a');
    px(24, 30, 30, 7, '#5a3a1a');
    px(24, 44, 3, 8, '#d9a878');
    px(51, 44, 3, 8, '#d9a878');

    if (mood === 'happy') {
      px(32, 47, 4, 2, '#20130a');
      px(42, 47, 4, 2, '#20130a');
      px(31, 41, 6, 2, '#3a2410');
      px(41, 41, 6, 2, '#3a2410');
      px(34, 53, 10, 2, '#7a2e2e');
      px(32, 52, 2, 2, '#7a2e2e');
      px(44, 52, 2, 2, '#7a2e2e');
    } else if (mood === 'angry') {
      px(32, 45, 4, 4, '#20130a');
      px(42, 45, 4, 4, '#20130a');
      px(31, 42, 6, 2, '#20130a');
      px(41, 43, 6, 2, '#20130a');
      px(34, 54, 10, 2, '#7a2e2e');
    } else {
      px(32, 45, 4, 4, '#20130a');
      px(42, 45, 4, 4, '#20130a');
      px(31, 41, 6, 2, '#3a2410');
      px(41, 41, 6, 2, '#3a2410');
      px(36, 54, 6, 2, '#7a2e2e');
    }
  }

  function drawScene() {
    var mood = state ? state.mood : 'idle';
    var remaining = state ? (DECK.length - state.pos) : DECK.length;

    px(0, 0, 160, 110, '#26406b');
    px(0, 0, 160, 6, '#1c3055');
    for (var x = 8; x < 160; x += 24) { px(x, 6, 2, 72, '#2d4a7a'); }

    // framed decree on the wall
    px(12, 8, 40, 30, '#0d1730');
    px(14, 10, 36, 26, '#f4f0e1');
    px(17, 14, 16, 3, '#c0392b');
    for (var line = 0; line < 6; line += 1) { px(17, 20 + line * 3, 30, 1, '#8a94b5'); }

    // window
    px(104, 8, 46, 34, '#5a3d1f');
    px(107, 11, 40, 28, '#7ec0ee');
    px(126, 11, 2, 28, '#5a3d1f');
    px(107, 24, 40, 2, '#5a3d1f');
    px(111, 15, 6, 6, '#ffffff');
    px(136, 18, 8, 5, '#cfeaff');

    // floor
    px(0, 78, 160, 32, '#6b4a2f');
    px(0, 78, 160, 2, '#5a3d24');

    drawAvatar(mood);

    // desk
    px(0, 84, 160, 8, '#b5793a');
    px(0, 92, 160, 18, '#8a5a2b');
    px(0, 84, 160, 2, '#c98d47');

    // nameplate
    px(10, 74, 46, 10, '#d9c26a');
    px(13, 77, 40, 4, '#3a2a10');

    // paper stack grows with the remaining documents
    var layers = Math.max(1, Math.min(8, Math.ceil(remaining / 25)));
    for (var i = 0; i < layers; i += 1) {
      px(116, 82 - i * 3, 34, 3, i % 2 ? '#f4f0e1' : '#ded8c4');
      px(116, 82 - i * 3, 34, 1, '#b9b29a');
    }

    // current paper on the desk
    px(70, 76, 30, 8, '#f4f0e1');
    px(70, 76, 30, 2, '#c0392b');
    px(73, 80, 22, 1, '#8a94b5');
  }

  // ----- wiring -----
  $('startBtn').addEventListener('click', startGame);
  $('restartBtn').addEventListener('click', startGame);
  $('helpBtn').addEventListener('click', function () { helpModal.classList.remove('hidden'); });
  $('helpBtnStart').addEventListener('click', function () { helpModal.classList.remove('hidden'); });
  $('helpClose').addEventListener('click', function () { helpModal.classList.add('hidden'); });
  helpModal.addEventListener('click', function (event) {
    if (event.target === helpModal) { helpModal.classList.add('hidden'); }
  });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('sw.js').catch(function () { /* offline support is optional */ });
    });
  }
})();
