'use strict';

// ---------- настройки и хранилище (всё хранится только на этом телефоне)

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; }
    catch { return false; }
  },
};

let settings = store.get('settings', { url: '', secret: '', age: '', city: '' });
const MAX_SIDE = 1280;      // до какого размера уменьшаем фото перед отправкой
const HISTORY_LIMIT = 30;

const $ = (id) => document.getElementById(id);

// ---------- экраны

function show(screen) {
  for (const id of ['pickScreen', 'photoScreen', 'historyScreen']) $(id).hidden = id !== screen;
  window.scrollTo(0, 0);
}

function updateSetupHint() {
  $('setupHint').hidden = Boolean(settings.url && settings.secret);
}

// ---------- фото и метка

let bitmap = null;      // исходное фото
let marker = null;      // {x, y} в долях от ширины/высоты, или null

async function loadPhoto(file) {
  if (!file) return;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch {
    // запасной путь для форматов, которые createImageBitmap не понимает
    const img = new Image();
    img.src = URL.createObjectURL(file);
    await img.decode();
    bitmap = img;
  }
  marker = null;
  clearAnswer();
  drawPreview();
  $('tapHint').textContent = '👆 Ткни в предмет, о котором хочешь узнать';
  show('photoScreen');
}

function drawPreview() {
  const canvas = $('photo');
  const w = bitmap.width, h = bitmap.height;
  const scale = Math.min(1, 1400 / Math.max(w, h));
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  if (marker) drawMarker(ctx, canvas.width, canvas.height);
}

function drawMarker(ctx, w, h) {
  const r = Math.max(18, Math.min(w, h) * 0.07);
  const x = marker.x * w, y = marker.y * h;
  ctx.lineWidth = Math.max(4, r * 0.14);
  ctx.strokeStyle = 'rgba(255,255,255,0.9)';
  ctx.beginPath(); ctx.arc(x, y, r + ctx.lineWidth * 0.6, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = '#ff1a1a';
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
}

$('photo').addEventListener('click', (e) => {
  const rect = e.target.getBoundingClientRect();
  marker = {
    x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
  };
  drawPreview();
  $('tapHint').textContent = '✅ Отмечено. Можно ткнуть в другое место.';
});

function photoForUpload() {
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const c = document.createElement('canvas');
  c.width = Math.round(bitmap.width * scale);
  c.height = Math.round(bitmap.height * scale);
  const ctx = c.getContext('2d');
  ctx.drawImage(bitmap, 0, 0, c.width, c.height);
  if (marker) drawMarker(ctx, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.85);
}

// Увеличенный фрагмент вокруг места касания — чтобы модель точно поняла, о каком предмете речь
function cropForUpload() {
  if (!marker) return null;
  const side = Math.min(bitmap.width, bitmap.height) * 0.4;
  const sx = Math.min(Math.max(0, marker.x * bitmap.width - side / 2), bitmap.width - side);
  const sy = Math.min(Math.max(0, marker.y * bitmap.height - side / 2), bitmap.height - side);
  const out = Math.min(768, Math.round(side * 2));
  const c = document.createElement('canvas');
  c.width = out; c.height = out;
  c.getContext('2d').drawImage(bitmap, sx, sy, side, side, 0, 0, out, out);
  return c.toDataURL('image/jpeg', 0.85);
}

function thumbnail() {
  const size = 160;
  const c = document.createElement('canvas');
  const s = Math.max(size / bitmap.width, size / bitmap.height);
  c.width = size; c.height = size;
  const w = bitmap.width * s, h = bitmap.height * s;
  c.getContext('2d').drawImage(bitmap, (size - w) / 2, (size - h) / 2, w, h);
  return c.toDataURL('image/jpeg', 0.6);
}

// ---------- запрос к серверу

const LOADING_PHRASES = ['Разглядываю…', 'Вспоминаю…', 'Листаю энциклопедию…', 'Подбираю слова для малыша…'];

async function callServer(body) {
  const res = await fetch(settings.url, {
    method: 'POST',
    // text/plain — чтобы браузер не делал предварительный CORS-запрос
    headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
    body: JSON.stringify({ ...body, secret: settings.secret }),
  });
  let data = {};
  try { data = await res.json(); } catch { /* ответ не JSON */ }
  if (!res.ok || data.error) throw new Error(data.error || `Сервер ответил ${res.status}`);
  return data;
}

async function ask(mode, question = '') {
  if (!settings.url || !settings.secret) { openSettings(); return; }
  if (!bitmap) return;

  clearAnswer();
  setBusy(true);
  let i = 0;
  $('loadingText').textContent = LOADING_PHRASES[0];
  const timer = setInterval(() => { $('loadingText').textContent = LOADING_PHRASES[++i % LOADING_PHRASES.length]; }, 2500);

  try {
    const image = photoForUpload().split(',')[1];
    const crop = cropForUpload();
    const data = await callServer({
      mode, question, image,
      crop: crop ? crop.split(',')[1] : '',
      marker: Boolean(marker),
      age: settings.age, city: settings.city,
    });
    showAnswer(data.text);
    saveToHistory(mode, question, data.text);
  } catch (err) {
    const msg = err instanceof TypeError
      ? 'Нет связи с сервером. Проверь интернет и адрес в настройках.'
      : err.message;
    $('error').textContent = '⚠️ ' + msg;
    $('error').hidden = false;
  } finally {
    clearInterval(timer);
    setBusy(false);
  }
}

function setBusy(busy) {
  $('loading').hidden = !busy;
  for (const id of ['aboutBtn', 'historyInfoBtn', 'askInput']) $(id).disabled = busy;
}

$('aboutBtn').addEventListener('click', () => ask('about'));
$('historyInfoBtn').addEventListener('click', () => ask('history'));
$('askForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = $('askInput').value.trim();
  if (q) ask('ask', q);
});

// ---------- показ ответа (простой markdown → HTML)

let lastAnswer = '';

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function inline(s) {
  return escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
}

function renderMarkdown(md) {
  const out = [];
  let list = false;
  for (const raw of md.split('\n')) {
    const line = raw.trim();
    const isItem = /^([-*•]|\d+[.)])\s+/.test(line);
    if (list && !isItem) { out.push('</ul>'); list = false; }
    if (!line) continue;
    if (/^#{1,6}\s/.test(line)) out.push(`<h2>${inline(line.replace(/^#+\s*/, ''))}</h2>`);
    else if (isItem) {
      if (!list) { out.push('<ul>'); list = true; }
      out.push(`<li>${inline(line.replace(/^([-*•]|\d+[.)])\s+/, ''))}</li>`);
    } else out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push('</ul>');
  return out.join('');
}

function showAnswer(text) {
  lastAnswer = text;
  $('answerText').innerHTML = renderMarkdown(text);
  $('answer').hidden = false;
  $('answer').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function clearAnswer() {
  stopSpeaking();
  $('answer').hidden = true;
  $('error').hidden = true;
}

// ---------- озвучка

function plain(md) {
  return md.replace(/^#+\s*/gm, '').replace(/[*_`#]/g, '').replace(/^[-•]\s+/gm, '');
}

function kidPart(md) {
  // берём разделы «Как рассказать…» и «Вопрос ребёнку»
  const parts = md.split(/^##\s+/m);
  const keep = parts.filter((p) => /^(как рассказать|вопрос)/i.test(p.trim()));
  return keep.length ? keep.map((p) => p.split('\n').slice(1).join('\n')).join('\n') : md;
}

function speak(text) {
  if (!('speechSynthesis' in window)) { alert('Этот браузер не умеет читать вслух'); return; }
  stopSpeaking();
  const u = new SpeechSynthesisUtterance(plain(text));
  u.lang = 'ru-RU';
  u.rate = 0.95;
  const ru = speechSynthesis.getVoices().find((v) => v.lang && v.lang.toLowerCase().startsWith('ru'));
  if (ru) u.voice = ru;
  u.onend = u.onerror = () => { $('stopBtn').hidden = true; };
  $('stopBtn').hidden = false;
  speechSynthesis.speak(u);
}

function stopSpeaking() {
  if ('speechSynthesis' in window) speechSynthesis.cancel();
  $('stopBtn').hidden = true;
}

$('speakBtn').addEventListener('click', () => speak(lastAnswer));
$('speakKidBtn').addEventListener('click', () => speak(kidPart(lastAnswer)));
$('stopBtn').addEventListener('click', stopSpeaking);

// ---------- история

const MODE_LABEL = { about: 'Что это', history: 'История', ask: 'Вопрос' };

function titleOf(text) {
  const m = text.match(/##\s*Что это\s*\n+([^\n]+)/i);
  return plain(m ? m[1] : text.split('\n').find((l) => l.trim()) || '').slice(0, 80);
}

function saveToHistory(mode, question, text) {
  const items = store.get('history', []);
  items.unshift({ t: Date.now(), mode, question, text, thumb: thumbnail() });
  // если место в памяти кончилось — выкидываем старые записи
  let list = items.slice(0, HISTORY_LIMIT);
  while (list.length && !store.set('history', list)) list = list.slice(0, -1);
}

function renderHistory() {
  const items = store.get('history', []);
  const ul = $('historyList');
  ul.innerHTML = '';
  $('historyEmpty').hidden = items.length > 0;
  $('historyClear').hidden = items.length === 0;
  for (const it of items) {
    const li = document.createElement('li');
    const date = new Date(it.t).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    li.innerHTML = `<img alt="" src="${it.thumb || ''}"><div><div class="h-title">${escapeHtml(titleOf(it.text))}</div>` +
      `<div class="h-meta">${MODE_LABEL[it.mode] || ''} · ${date}</div></div>`;
    li.addEventListener('click', () => {
      // показываем сохранённый ответ без фото
      bitmap = null;
      show('photoScreen');
      $('photo').width = 0; $('photo').height = 0;
      $('tapHint').textContent = it.question ? `Вопрос: ${it.question}` : '';
      showAnswer(it.text);
    });
    ul.appendChild(li);
  }
}

$('historyBtn').addEventListener('click', () => { renderHistory(); show('historyScreen'); });
$('historyClose').addEventListener('click', () => show(bitmap ? 'photoScreen' : 'pickScreen'));
$('historyClear').addEventListener('click', () => {
  if (confirm('Удалить всю историю?')) { store.set('history', []); renderHistory(); }
});

// ---------- выбор фото

for (const id of ['cameraInput', 'galleryInput']) {
  $(id).addEventListener('change', (e) => { loadPhoto(e.target.files[0]); e.target.value = ''; });
}
$('newPhotoBtn').addEventListener('click', () => { stopSpeaking(); show('pickScreen'); });

// ---------- настройки

function openSettings() {
  $('sUrl').value = settings.url;
  $('sSecret').value = settings.secret;
  $('sAge').value = settings.age;
  $('sCity').value = settings.city;
  $('sStatus').textContent = '';
  $('settings').showModal();
}

function readSettingsForm() {
  return {
    url: $('sUrl').value.trim(),
    secret: $('sSecret').value.trim(),
    age: $('sAge').value.trim(),
    city: $('sCity').value.trim(),
  };
}

$('settingsBtn').addEventListener('click', openSettings);
$('settingsForm').addEventListener('submit', () => {
  settings = readSettingsForm();
  store.set('settings', settings);
  updateSetupHint();
});
$('sTest').addEventListener('click', async () => {
  const prev = settings;
  settings = readSettingsForm();
  $('sStatus').textContent = 'Проверяю…';
  try {
    const data = await callServer({ ping: true });
    $('sStatus').textContent = `✅ Связь есть, модель: ${data.model}`;
  } catch (err) {
    $('sStatus').textContent = '⚠️ ' + (err instanceof TypeError ? 'Сервер недоступен — проверь адрес' : err.message);
  } finally {
    settings = prev;
  }
});

// ---------- старт

updateSetupHint();
show('pickScreen');
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
