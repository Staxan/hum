/* hum — логика интерфейса каталога моделей. Без внешних зависимостей. */
'use strict';

const state = {
  models: [],
  agents: [],
  favs: new Set(),
  filters: { type: '', price: 'all', ctx: 0, pin: 0, pout: 0, tools: new Set(), vendor: '', q: '' },
  sort: 'vendor',
  onlyFav: false,
};

const $ = (s) => document.querySelector(s);
const body = $('#body');

/* ── утилиты ───────────────────────────────────────────────────────── */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtCtx(n) {
  if (!n) return '—';
  if (n >= 1000000) return (n / 1000000).toFixed(n % 1000000 ? 1 : 0) + 'M';
  if (n >= 1000) return Math.round(n / 1000) + 'K';
  return String(n);
}
function fmtPrice(v) {
  if (v === null || v === undefined) return '—';
  if (v === 0) return '$0';
  return '$' + Number(v).toFixed(2).replace(/\.00$/, '');
}
function fmtDate(ts) {
  if (!ts) return '—';
  const d = new Date(Number(ts));
  if (isNaN(d)) return '—';
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
}
function fmtLat(ms) {
  if (!ms) return '—';
  return ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms) + 'ms';
}
function toast(msg, kind) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast ' + (kind || '');
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 4200);
}

/* ── загрузка ──────────────────────────────────────────────────────── */
async function loadCatalog(refresh) {
  const sub = $('#sub');
  sub.textContent = refresh ? 'Обновляю цены и доступность…' : 'Загружаю каталог…';
  try {
    const r = await fetch('/api/catalog' + (refresh ? '?refresh=1' : ''));
    const data = await r.json();
    state.models = data.models || [];
    state.favs = new Set(data.favorites || []);
    const src = data.sources || {};
    const bits = [`${state.models.length} моделей`];
    if (src.page_metadata) bits.push('цены UnoRouter');
    if (src.api_models) bits.push('список API');
    if (data.stale) bits.push('⚠ показаны прошлые данные');
    sub.textContent = bits.join(' · ');
    fillVendors();
    render();
  } catch (e) {
    sub.textContent = 'Ошибка загрузки: ' + e.message;
  }
}

async function loadAgents() {
  try {
    const r = await fetch('/api/agents');
    const d = await r.json();
    state.agents = d.agents || [];
  } catch (e) { state.agents = []; }
}

function fillVendors() {
  const sel = $('#vendor');
  const cur = sel.value;
  const set = [...new Set(state.models.map((m) => m.vendor).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">Все провайдеры</option>' +
    set.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  sel.value = cur;
}

/* ── фильтрация и сортировка ───────────────────────────────────────── */
function visible() {
  const f = state.filters;
  const q = f.q.trim().toLowerCase();
  let out = state.models.filter((m) => {
    if (q && !(m.id.toLowerCase().includes(q) || (m.vendor || '').toLowerCase().includes(q))) return false;
    if (f.type && m.type !== f.type) return false;
    if (f.price === 'free' && !m.is_free) return false;
    if (f.price === 'paid' && m.is_free) return false;
    if (f.vendor && m.vendor !== f.vendor) return false;
    if (f.ctx && (m.context_window || 0) < f.ctx) return false;
    if (f.pin !== '' && (m.input_price === null || m.input_price > f.pin)) return false;
    if (f.pout !== '' && (m.output_price === null || m.output_price > f.pout)) return false;
    if (f.tools.has('online') && !m.online) return false;
    if (f.tools.has('tools') && !m.supports_tools) return false;
    if (f.tools.has('vision') && !m.supports_vision) return false;
    if (f.tools.has('reasoning') && !m.is_reasoning) return false;
    if (f.tools.has('cache') && !m.supports_cache) return false;
    if (state.onlyFav && !state.favs.has(m.id)) return false;
    return true;
  });

  const key = state.sort;
  const num = (v) => (v === null || v === undefined ? -1 : Number(v));
  out.sort((a, b) => {
    if (key === 'name') return a.id.localeCompare(b.id);
    if (key === 'ctx-desc') return num(b.context_window) - num(a.context_window);
    if (key === 'in-asc') return num(a.input_price) - num(b.input_price);
    if (key === 'out-asc') return num(a.output_price) - num(b.output_price);
    if (key === 'lat-asc') return num(a.avg_latency_ms) - num(b.avg_latency_ms);
    if (key === 'succ-desc') return num(b.success_rate) - num(a.success_rate);
    if (key === 'rel-desc') return num(b.release_ts) - num(a.release_ts);
    // По умолчанию: провайдер, затем имя. Избранные не трогаем — их порядок задаёт сортировка.
    return (a.vendor || '').localeCompare(b.vendor || '') || a.id.localeCompare(b.id);
  });

  // Избранные всегда сверху, как на сайте: закреплённые строки приподнимаются.
  const favs = out.filter((m) => state.favs.has(m.id));
  const rest = out.filter((m) => !state.favs.has(m.id));
  return favs.concat(rest);
}

/* ── отрисовка ─────────────────────────────────────────────────────── */
function row(m) {
  const isFav = state.favs.has(m.id);
  const dIn = (m.original_input_price && m.original_input_price > m.input_price)
    ? `<span class="discount">скидка ${Math.round((1 - m.input_price / m.original_input_price) * 100)}%</span>` : '';
  const dOut = (m.original_output_price && m.original_output_price > m.output_price)
    ? `<span class="discount">скидка ${Math.round((1 - m.output_price / m.original_output_price) * 100)}%</span>` : '';
  const tags = (m.is_free ? '<span class="tag free">free</span>' : '') +
    (m.supports_tools ? '<span class="tag">tools</span>' : '') +
    (m.is_reasoning ? '<span class="tag">reasoning</span>' : '');
  return `<tr data-id="${esc(m.id)}" class="${isFav ? 'fav-row' : ''}">
    <td class="c-fav"><button class="star ${isFav ? 'on' : ''}" data-fav="${esc(m.id)}" title="В избранное">★</button></td>
    <td>
      <div class="model-name">${esc(m.id)}</div>
      <div>${tags}</div>
    </td>
    <td class="vendor">${esc(m.vendor || '—')}</td>
    <td class="price">${fmtPrice(m.input_price)}${dIn}</td>
    <td class="price">${fmtPrice(m.output_price)}${dOut}</td>
    <td class="price">${fmtCtx(m.context_window)}</td>
    <td><span class="dot ${m.online ? 'on' : 'off'}"></span>${m.online ? 'доступна' : 'недоступна'}</td>
    <td class="price">${m.success_rate !== null && m.success_rate !== undefined ? m.success_rate + '%' : '—'}</td>
    <td class="price">${fmtLat(m.avg_latency_ms)}</td>
    <td class="muted">${fmtDate(m.release_ts)}</td>
    <td class="c-act">
      <div class="act">
        <button class="mini" data-check="${esc(m.id)}" ${m.is_free ? '' : 'disabled title="Проверяются только бесплатные модели"'}>Проверить</button>
        <button class="mini" data-connect="${esc(m.id)}">Подключить</button>
      </div>
      <div class="check-res" data-res="${esc(m.id)}"></div>
    </td>
  </tr>`;
}

function render() {
  const rows = visible();
  body.innerHTML = rows.map(row).join('');
  $('#empty').hidden = rows.length > 0;
  const favCount = state.favs.size;
  $('#counter').textContent =
    `Показано ${rows.length} из ${state.models.length} · избранных ${favCount}`;
}

/* ── действия ──────────────────────────────────────────────────────── */
document.addEventListener('click', async (ev) => {
  const favBtn = ev.target.closest('[data-fav]');
  if (favBtn) {
    const id = favBtn.dataset.fav;
    const r = await fetch('/api/favorite', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: id }),
    }).then((x) => x.json());
    if (r.favorite) state.favs.add(id); else state.favs.delete(id);
    render();
    return;
  }

  const chk = ev.target.closest('[data-check]');
  if (chk) {
    const id = chk.dataset.check;
    chk.disabled = true; chk.textContent = 'Проверяю…';
    const res = document.querySelector(`[data-res="${CSS.escape(id)}"]`);
    if (res) res.textContent = '';
    try {
      const r = await fetch('/api/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: id }),
      }).then((x) => x.json());
      if (r.ok) {
        chk.classList.add('ok'); chk.textContent = 'Работает';
        if (res) res.textContent = '«' + r.prompt.slice(0, 40) + '…» → ' + r.reply.slice(0, 90);
        toast(id + ' — модель отвечает', 'ok');
      } else {
        chk.classList.add('bad'); chk.textContent = 'Ошибка';
        if (res) res.textContent = r.error || 'не отвечает';
        toast(id + ' — ' + (r.error || 'не отвечает'), 'err');
      }
    } catch (e) {
      chk.classList.add('bad'); chk.textContent = 'Ошибка';
      toast('Проверка не прошла: ' + e.message, 'err');
    }
    return;
  }

  const con = ev.target.closest('[data-connect]');
  if (con) {
    openDropdown(con, con.dataset.connect);
    return;
  }

  // Клик вне выпадающего списка — закрыть.
  const dd = ev.target.closest('.dropdown');
  document.querySelectorAll('.dropdown').forEach((d) => {
    if (!dd || d !== dd) d.remove();
  });
});

function openDropdown(btn, modelId) {
  const existing = btn.parentElement.querySelector('.dropdown');
  if (existing) { existing.remove(); return; }
  document.querySelectorAll('.dropdown').forEach((d) => d.remove());

  const dd = document.createElement('div');
  dd.className = 'dropdown';
  dd.innerHTML = state.agents.map((a) => `
    <button data-agent="${esc(a.id)}" data-model="${esc(modelId)}">
      <div class="ag-name">
        ${a.model === modelId ? '<span class="dot on"></span>' : ''}
        ${esc(a.label)}
        ${a.running === false ? '<span class="tag off">не запущен</span>' : ''}
      </div>
      <div class="ag-model">сейчас: <b>${esc(a.model || 'не задана')}</b> → ${esc(modelId)}</div>
    </button>`).join('');
  btn.parentElement.appendChild(dd);

  dd.addEventListener('click', async (ev) => {
    const item = ev.target.closest('[data-agent]');
    if (!item) return;
    const agent = item.dataset.agent;
    const model = item.dataset.model;
    dd.remove();
    btn.disabled = true; btn.textContent = 'Подключаю…';
    try {
      const r = await fetch('/api/connect', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, profile: agent, restart: true }),
      }).then((x) => x.json());
      if (r.ok) {
        btn.classList.add('ok'); btn.textContent = 'Подключено';
        toast(`${r.label}: ${model} подключена, gateway перезапущен`, 'ok');
        await loadAgents();
        render();
      } else {
        btn.classList.add('bad'); btn.textContent = 'Ошибка';
        toast(r.error || 'Не удалось подключить', 'err');
      }
    } catch (e) {
      btn.classList.add('bad'); btn.textContent = 'Ошибка';
      toast('Подключение не прошло: ' + e.message, 'err');
    }
  });
}

/* ── фильтры ───────────────────────────────────────────────────────── */
$('#q').addEventListener('input', (e) => { state.filters.q = e.target.value; render(); });
$('#ctx').addEventListener('input', (e) => { state.filters.ctx = Number(e.target.value) || 0; render(); });
$('#price-in').addEventListener('input', (e) => { state.filters.pin = e.target.value === '' ? '' : Number(e.target.value); render(); });
$('#price-out').addEventListener('input', (e) => { state.filters.pout = e.target.value === '' ? '' : Number(e.target.value); render(); });
$('#vendor').addEventListener('change', (e) => { state.filters.vendor = e.target.value; render(); });
$('#sort').addEventListener('change', (e) => { state.sort = e.target.value; render(); });
$('#only-fav').addEventListener('change', (e) => { state.onlyFav = e.target.checked; render(); });

function chipGroup(id, apply) {
  $(id).addEventListener('click', (ev) => {
    const chip = ev.target.closest('.chip');
    if (!chip) return;
    $(id).querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
    chip.classList.add('active');
    apply(chip.dataset.val);
    render();
  });
}
chipGroup('#chips-type', (v) => { state.filters.type = v; });
chipGroup('#chips-price', (v) => { state.filters.price = v; });
$('#chips-tool').addEventListener('click', (ev) => {
  const chip = ev.target.closest('.chip');
  if (!chip) return;
  const v = chip.dataset.val;
  if (state.filters.tools.has(v)) { state.filters.tools.delete(v); chip.classList.remove('active'); }
  else { state.filters.tools.add(v); chip.classList.add('active'); }
  render();
});

document.querySelectorAll('thead th[data-sort]').forEach((th) => {
  th.addEventListener('click', () => { state.sort = th.dataset.sort; render(); });
});

$('#btn-refresh').addEventListener('click', async (e) => {
  e.target.disabled = true;
  await loadCatalog(true);
  e.target.disabled = false;
});

$('#btn-checkall').addEventListener('click', async (e) => {
  const btn = e.target;
  const free = state.models.filter((m) => m.is_free);
  if (!free.length) { toast('Бесплатных моделей нет', 'err'); return; }
  if (!confirm(`Проверить ${free.length} бесплатных моделей? Каждой будет отправлен один короткий запрос.`)) return;
  btn.disabled = true;
  let ok = 0, fail = 0;
  for (let i = 0; i < free.length; i++) {
    const m = free[i];
    btn.textContent = `Проверяю ${i + 1}/${free.length}…`;
    try {
      const r = await fetch('/api/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: m.id }),
      }).then((x) => x.json());
      const cell = document.querySelector(`[data-res="${CSS.escape(m.id)}"]`);
      if (r.ok) { ok++; if (cell) cell.textContent = 'Работает: ' + r.reply.slice(0, 80); }
      else { fail++; if (cell) cell.textContent = r.error || 'не отвечает'; }
    } catch (e) { fail++; }
    const b = document.querySelector(`[data-check="${CSS.escape(m.id)}"]`);
    if (b) { b.classList.toggle('ok', fail === 0 && ok > 0); }
    render();
  }
  btn.disabled = false;
  btn.textContent = 'Проверить все бесплатные';
  toast(`Готово: работают ${ok}, не отвечают ${fail}`, fail ? 'err' : 'ok');
});

/* ── старт ─────────────────────────────────────────────────────────── */
(async function start() {
  await loadAgents();
  await loadCatalog(false);
})();