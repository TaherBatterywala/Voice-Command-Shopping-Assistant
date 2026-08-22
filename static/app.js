/**
 * VoiceShop AI — app.js
 * Client-side logic for the Voice Command Shopping Assistant.
 *
 * Key fixes vs previous version:
 *  - Remove button calls DELETE /api/v1/cart/{name} silently (NO transcript banner)
 *  - +/- qty buttons call MODIFY_QUANTITY via voice command
 *  - Recognition bubble replaces old transcript banner
 *  - Waveform bars animate during recording
 *  - Suggestions show BELOW search in right panel
 */
'use strict';

/* ── API ────────────────────────────────────────────────────── */
const API = {
  VOICE:      '/api/v1/voice-command',
  CART:       '/api/v1/cart',
  CART_ITEM:  (name) => `/api/v1/cart/${encodeURIComponent(name)}`,
  SUGGEST:    '/api/v1/suggestions',
};

/* ── State ─────────────────────────────────────────────────── */
const state = { isRecording: false, isLoading: false, timerInterval: null };

/* ── DOM refs ──────────────────────────────────────────────── */
const el = {
  micBtn:       document.getElementById('micBtn'),
  micIcon:      document.getElementById('micIcon'),
  stopIcon:     document.getElementById('stopIcon'),
  micLabel:     document.getElementById('micLabel'),
  micPulse:     document.getElementById('micPulse'),
  recTimer:     document.getElementById('recTimer'),
  timerDisplay: document.getElementById('timerDisplay'),
  waveLeft:     document.getElementById('waveLeft'),
  waveRight:    document.getElementById('waveRight'),
  bubbleHint:   document.getElementById('bubbleHint'),
  bubbleResp:   document.getElementById('bubbleResponse'),
  intentTag:    document.getElementById('intentTag'),
  bubbleTrans:  document.getElementById('bubbleTranscript'),
  bubbleMsg:    document.getElementById('bubbleMsg'),
  loadingBar:   document.getElementById('loadingBar'),
  textInput:    document.getElementById('textInput'),
  sendBtn:      document.getElementById('sendBtn'),
  productArea:  document.getElementById('productArea'),
  emptyBoard:   document.getElementById('emptyBoard'),
  headerCount:  document.getElementById('headerCount'),
  searchSection:document.getElementById('searchSection'),
  searchCards:  document.getElementById('searchCards'),
  searchCount:  document.getElementById('searchCount'),
  subSection:   document.getElementById('subSection'),
  subCards:     document.getElementById('subCards'),
  seasonalCards:document.getElementById('seasonalCards'),
  restockCards: document.getElementById('restockCards'),
  themeToggle:  document.getElementById('themeToggle'),
  themeIcon:    document.getElementById('themeIcon'),
  clearCartBtn: document.getElementById('clearCartBtn'),
  itemsPill:    document.getElementById('itemsPill'),
  navItems:     document.querySelectorAll('.nav-item[data-view]'),
};

/* ── Item → emoji mapping ──────────────────────────────────── */
const ITEM_EMOJI = {
  milk:'🥛','almond milk':'🥛','oat milk':'🥛',
  cheese:'🧀',yogurt:'🫙',butter:'🧈',cream:'🍦',eggs:'🥚',
  apple:'🍎',apples:'🍎',orange:'🍊',oranges:'🍊',mango:'🥭',
  mangoes:'🥭',banana:'🍌',bananas:'🍌',watermelon:'🍉',
  grapes:'🍇',strawberry:'🍓',strawberries:'🍓',lemon:'🍋',
  tomato:'🍅',tomatoes:'🍅',potato:'🥔',potatoes:'🥔',
  onion:'🧅',onions:'🧅',spinach:'🥬',carrot:'🥕',carrots:'🥕',
  garlic:'🧄',mushroom:'🍄',mushrooms:'🍄',lettuce:'🥗',
  broccoli:'🥦',cucumber:'🥒',
  bread:'🍞',pasta:'🍝',rice:'🍚',flour:'🌾',cereal:'🥣',
  ketchup:'🍅',sauce:'🫙',oil:'🫙',vinegar:'🫙',
  water:'💧',juice:'🧃',soda:'🥤',tea:'🍵',coffee:'☕',
  'energy drink':'🥤','aloe vera':'🧃',
  chips:'🍿',popcorn:'🍿',cookies:'🍪',chocolate:'🍫',
  candy:'🍬',nuts:'🥜','peanut butter':'🥜',
  soap:'🧼',shampoo:'🧴','toilet paper':'🧻',
};

const CAT_EMOJI = {
  Dairy:'🥛', Produce:'🥦', Snacks:'🍿',
  Beverages:'🥤', Pantry:'🫙', Other:'📦',
};

const CAT_ORDER = ['Dairy','Produce','Beverages','Snacks','Pantry','Other'];

function itemEmoji(name) {
  const n = name.toLowerCase().trim();
  if (ITEM_EMOJI[n]) return ITEM_EMOJI[n];
  for (const [key, val] of Object.entries(ITEM_EMOJI)) {
    if (n.includes(key)) return val;
  }
  return '🛒';
}

/* ── MediaRecorder ─────────────────────────────────────────── */
let mediaRecorder = null, audioChunks = [];

function bestMime() {
  const candidates = ['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'];
  return candidates.find(t => MediaRecorder.isTypeSupported(t)) || '';
}

async function startRecording() {
  if (state.isLoading) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    const mime = bestMime();
    mediaRecorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const finalMime = mediaRecorder.mimeType || 'audio/webm';
      const blob = new Blob(audioChunks, { type: finalMime });
      const ext = finalMime.includes('ogg') ? 'ogg' : finalMime.includes('mp4') ? 'mp4' : 'webm';
      const fd = new FormData();
      fd.append('audio', blob, `recording.${ext}`);
      await callVoiceAPI(fd);
    };
    mediaRecorder.start(200);
    state.isRecording = true;
    setRecordingUI(true);
    startTimer();
  } catch(err) {
    const msg = err.name === 'NotAllowedError'
      ? 'Microphone access denied. Please allow mic permissions in browser settings.'
      : `Mic error: ${err.message}`;
    alert(msg);
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
  state.isRecording = false;
  setRecordingUI(false);
  stopTimer();
}

/* ── Timer ─────────────────────────────────────────────────── */
let timerStart = null;
function startTimer() {
  timerStart = Date.now();
  el.recTimer.classList.remove('hidden');
  state.timerInterval = setInterval(() => {
    const s = Math.floor((Date.now() - timerStart) / 1000);
    el.timerDisplay.textContent = `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
  }, 500);
}
function stopTimer() {
  clearInterval(state.timerInterval);
  el.recTimer.classList.add('hidden');
  el.timerDisplay.textContent = '0:00';
}

/* ── Recording UI ──────────────────────────────────────────── */
function setRecordingUI(on) {
  el.micBtn.classList.toggle('recording', on);
  el.micIcon.classList.toggle('hidden', on);
  el.stopIcon.classList.toggle('hidden', !on);
  el.micLabel.textContent = on ? 'Tap to stop' : 'Speak Now';
  el.micBtn.setAttribute('aria-label', on ? 'Stop recording' : 'Start recording');
  // Animate waveform
  [el.waveLeft, el.waveRight].forEach(w => {
    w.classList.toggle('waveform-active', on);
    w.querySelectorAll('.bar').forEach((b, i) => {
      b.style.setProperty('--dur', `${0.8 + i * 0.08}s`);
    });
  });
}

/* ── Text command ──────────────────────────────────────────── */
function sendText(text) {
  if (!text || !text.trim() || state.isLoading) return;
  const fd = new FormData();
  fd.append('transcript_override', text.trim());
  el.textInput.value = '';
  callVoiceAPI(fd);
}

/* ── Call voice API ────────────────────────────────────────── */
async function callVoiceAPI(formData) {
  setLoading(true);
  try {
    const res = await fetch(API.VOICE, { method:'POST', body:formData });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `Server error ${res.status}`);
    }
    const data = await res.json();
    handleVoiceResponse(data);
  } catch(err) {
    showBubble('', 'UNKNOWN', `⚠️ ${err.message}`);
  } finally {
    setLoading(false);
  }
}

/* ── Handle response ───────────────────────────────────────── */
function handleVoiceResponse(data) {
  showBubble(data.transcript || '', data.intent || 'UNKNOWN', data.message || '');
  if (Array.isArray(data.cart)) renderCart(data.cart);
  if (data.suggestions)         renderSuggestions(data.suggestions);
  if (data.intent === 'SEARCH_FILTER') {
    renderSearchResults(data.search_results || []);
    el.searchSection.classList.remove('hidden');
  } else {
    el.searchSection.classList.add('hidden');
  }
}

/* ── Silent item remove (via DELETE endpoint) ──────────────── */
async function removeItemSilent(name) {
  try {
    const res = await fetch(API.CART_ITEM(name), { method:'DELETE' });
    if (res.ok) {
      const data = await res.json();
      renderCart(data.cart);
    }
  } catch(err) {
    console.error('Remove error:', err);
  }
}

/* ── Modify quantity via voice API ─────────────────────────── */
function modifyQty(name, delta, currentQty) {
  const newQty = Math.max(1, currentQty + delta);
  const fd = new FormData();
  fd.append('transcript_override', `Update ${name} quantity to ${newQty}`);
  callVoiceAPI(fd);
}

/* ── Loading ───────────────────────────────────────────────── */
function setLoading(on) {
  state.isLoading = on;
  el.loadingBar.classList.toggle('hidden', !on);
  el.sendBtn.disabled = on;
  el.micBtn.disabled  = on;
}

/* ── Recognition bubble ────────────────────────────────────── */
function showBubble(transcript, intent, message) {
  el.bubbleHint.classList.add('hidden');
  el.bubbleResp.classList.remove('hidden');
  el.bubbleTrans.textContent = transcript;
  el.bubbleMsg.textContent   = message;
  const label = intent.replace(/_/g,' ');
  el.intentTag.textContent = label;
  el.intentTag.className   = `intent-tag ib-${intent}`;
}

/* ── Render: Cart ──────────────────────────────────────────── */
function renderCart(cart) {
  el.productArea.innerHTML = '';
  const total = cart.length;
  el.headerCount.textContent = `${total} item${total !== 1 ? 's' : ''}`;

  if (!total) {
    el.productArea.appendChild(el.emptyBoard);
    el.emptyBoard.classList.remove('hidden');
    return;
  }

  // Group by category
  const groups = {};
  cart.forEach(item => {
    const cat = item.category || 'Other';
    (groups[cat] = groups[cat] || []).push(item);
  });

  const sortedCats = [
    ...CAT_ORDER.filter(c => groups[c]),
    ...Object.keys(groups).filter(c => !CAT_ORDER.includes(c)),
  ];

  sortedCats.forEach(cat => {
    const section = document.createElement('div');
    section.className = 'cat-section';

    const label = document.createElement('div');
    label.className = `cat-label cat-${cat}`;
    label.innerHTML = `<span>${CAT_EMOJI[cat] || '📦'}</span> ${cat}`;
    section.appendChild(label);

    const grid = document.createElement('div');
    grid.className = 'product-grid';

    groups[cat].forEach(item => {
      grid.appendChild(makeCard(item));
    });

    section.appendChild(grid);
    el.productArea.appendChild(section);
  });
}

function makeCard(item) {
  const card = document.createElement('div');
  card.className = 'product-card';

  const qty = Number(item.quantity);
  const qtyStr = qty === Math.floor(qty) ? qty : qty.toFixed(1);
  const qtyLabel = item.unit ? `${qtyStr} ${item.unit}` : `×${qtyStr}`;
  const emoji = itemEmoji(item.item_name);
  const cat = item.category || 'Other';

  // Badge class
  const badgeClass = `cat-${cat}`;

  card.innerHTML = `
    <button class="card-remove" title="Remove ${esc(item.item_name)}" aria-label="Remove ${esc(item.item_name)}">✕</button>
    <div class="card-emoji">${emoji}</div>
    <div class="card-name">${esc(item.item_name)}</div>
    ${item.price_estimate != null ? `<div class="card-price">$${Number(item.price_estimate).toFixed(2)}</div>` : ''}
    <div class="qty-row">
      <button class="qty-btn qty-minus" aria-label="Decrease quantity">−</button>
      <span class="qty-val">${esc(qtyLabel)}</span>
      <button class="qty-btn qty-plus" aria-label="Increase quantity">+</button>
    </div>
    <div class="card-badge ${badgeClass}">${CAT_EMOJI[cat] || '📦'} ${cat}</div>
  `;

  // Remove button — silent delete
  card.querySelector('.card-remove').addEventListener('click', (e) => {
    e.stopPropagation();
    card.style.opacity = '0.4';
    removeItemSilent(item.item_name);
  });

  // Qty buttons — update via command
  card.querySelector('.qty-minus').addEventListener('click', (e) => {
    e.stopPropagation();
    if (qty <= 1) { removeItemSilent(item.item_name); }
    else          { modifyQty(item.item_name, -1, qty); }
  });
  card.querySelector('.qty-plus').addEventListener('click', (e) => {
    e.stopPropagation();
    modifyQty(item.item_name, +1, qty);
  });

  return card;
}

/* ── Render: Suggestions ───────────────────────────────────── */
function renderSuggestions(sugg) {
  renderSugChips(el.seasonalCards, sugg.seasonal_recommendations || []);
  renderSugChips(el.restockCards,  sugg.historical_recommendations || []);
  renderSubstitutes(sugg.substitutes || []);
}

function renderSugChips(container, items) {
  container.innerHTML = '';
  if (!items.length) {
    container.innerHTML = '<p style="font-size:11.5px;color:var(--txt3);padding:4px 0">None available</p>';
    return;
  }
  items.forEach(name => {
    const card = document.createElement('div');
    card.className = 'sug-card';
    card.setAttribute('role', 'listitem');
    card.innerHTML = `
      <div class="sug-name">${esc(name)}</div>
      <div class="sug-actions">
        <button class="sug-btn add">+ Add to List</button>
      </div>
    `;
    card.querySelector('.sug-btn.add').addEventListener('click', (e) => {
      e.stopPropagation();
      sendText(`Add ${name}`);
    });
    container.appendChild(card);
  });
}

function renderSubstitutes(subs) {
  el.subCards.innerHTML = '';
  if (!subs.length) { el.subSection.classList.add('hidden'); return; }
  el.subSection.classList.remove('hidden');
  subs.forEach(sub => {
    const card = document.createElement('div');
    card.className = 'sub-card';
    card.setAttribute('role', 'listitem');
    card.innerHTML = `
      <div class="sub-orig">${esc(sub.original)}</div>
      <div class="sub-new">${esc(sub.substitute)}</div>
      <div class="sub-reason">${esc(sub.reason)}</div>
    `;
    card.addEventListener('click', () => sendText(`Add ${sub.substitute}`));
    el.subCards.appendChild(card);
  });
}

/* ── Render: Search results ────────────────────────────────── */
function renderSearchResults(results) {
  el.searchCards.innerHTML = '';
  el.searchCount.textContent = results.length;

  if (!results.length) {
    el.searchCards.innerHTML =
      '<p style="font-size:12px;color:var(--txt3);padding:6px 0">No products matched.</p>';
    return;
  }

  results.forEach(p => {
    const card = document.createElement('div');
    card.className = 'sug-card';
    card.setAttribute('role', 'listitem');

    const metaParts = [p.brand, p.category].filter(Boolean).map(esc).join(' · ');
    card.innerHTML = `
      <div class="sug-meta">
        <div class="sug-name">${esc(p.name)}</div>
        ${p.price != null ? `<div class="sug-price">$${Number(p.price).toFixed(2)}</div>` : ''}
      </div>
      ${metaParts ? `<div class="sug-sub">${metaParts}</div>` : ''}
      <div class="sug-actions">
        <button class="sug-btn add">+ Add to List</button>
        <button class="sug-btn sub">Substitutes</button>
      </div>
    `;
    card.querySelector('.sug-btn.add').addEventListener('click', (e) => {
      e.stopPropagation();
      sendText(`Add ${p.name}`);
    });
    card.querySelector('.sug-btn.sub').addEventListener('click', (e) => {
      e.stopPropagation();
      sendText(`Suggest substitutes for ${p.name}`);
    });
    el.searchCards.appendChild(card);
  });
}

/* ── Hydrate on load ───────────────────────────────────────── */
async function hydrate() {
  try {
    const res = await fetch(API.CART);
    if (res.ok) { const d = await res.json(); renderCart(d.cart || []); }
  } catch(e) { /* offline — skip */ }
  try {
    const res = await fetch(API.SUGGEST);
    if (res.ok) { const d = await res.json(); renderSuggestions(d); }
  } catch(e) { /* offline — skip */ }
}

/* ── Clear cart ─────────────────────────────────────────────── */
async function clearAllItems() {
  if (!confirm('Clear your entire shopping list?')) return;
  try {
    const res = await fetch(API.CART, { method:'DELETE' });
    if (res.ok) {
      renderCart([]);
      el.searchSection.classList.add('hidden');
      el.subSection.classList.add('hidden');
      el.bubbleHint.classList.remove('hidden');
      el.bubbleResp.classList.add('hidden');
    }
  } catch(e) { console.error(e); }
}

/* ── Theme ─────────────────────────────────────────────────── */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  // Update icon
  el.themeIcon.innerHTML = t === 'dark'
    ? '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>'
    : '<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>';
}

/* ── Sidebar nav ───────────────────────────────────────────── */
el.navItems.forEach(btn => {
  btn.addEventListener('click', () => {
    el.navItems.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

/* ── Utility ───────────────────────────────────────────────── */
function esc(str) {
  const d = document.createElement('div');
  d.textContent = String(str || '');
  return d.innerHTML;
}

/* ── Event listeners ───────────────────────────────────────── */
el.micBtn.addEventListener('click', () => {
  if (state.isLoading) return;
  state.isRecording ? stopRecording() : startRecording();
});

el.sendBtn.addEventListener('click', () => sendText(el.textInput.value));
el.textInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(el.textInput.value); }
});

el.themeToggle.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  localStorage.setItem('vc-theme', next);
});

el.clearCartBtn.addEventListener('click', clearAllItems);

/* ── Bootstrap ─────────────────────────────────────────────── */
(function init() {
  const saved = localStorage.getItem('vc-theme') || 'light';
  applyTheme(saved);
  hydrate();
})();
