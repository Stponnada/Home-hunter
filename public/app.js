let map, workPin, commuteCircle, exclLayers = [], markers = [], currentPrefs = {}, dropWork = false;
let work = {lat: 17.4148, lng: 78.3488};
const $ = id => document.getElementById(id);

map = L.map('map', {zoomControl: false}).setView([17.41, 78.355], 12);
L.control.zoom({position: 'bottomleft'}).addTo(map);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap'}).addTo(map);
workPin = L.marker([work.lat, work.lng], {draggable: true}).addTo(map).bindPopup('<b>Financial District</b><br>Your default commute origin. Drag me or use Drop workplace.');
workPin.on('dragend', () => { const p = workPin.getLatLng(); work = {lat: p.lat, lng: p.lng}; });
map.on('click', e => {
  if (!dropWork) return;
  work = {lat: e.latlng.lat, lng: e.latlng.lng}; workPin.setLatLng(e.latlng);
  dropWork = false; $('dropWork').textContent = 'Drop workplace'; $('dropWork').classList.remove('active');
  addAssistant(`Workplace set — I'll measure commute from ${work.lat.toFixed(4)}, ${work.lng.toFixed(4)}.`);
});

async function api(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (!r.ok) throw new Error('Could not complete that search.');
  return r.json();
}
function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function nearBottom() { const c = $('conversation'); return c.scrollHeight - c.scrollTop - c.clientHeight < 140; }
function pin() { if (nearBottom()) { const c = $('conversation'); c.scrollTop = c.scrollHeight; } }
function renderMd(src) {
  const code = [];
  let s = escapeHtml(src || '').replace(/`([^`\n]+)`/g, (m, c) => { code.push(c); return `\u0000${code.length - 1}\u0000`; });
  const inline = t => t
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const lines = s.split('\n'), out = [];
  let list = null;
  const close = () => { if (list) { out.push(list === 'ul' ? '</ul>' : '</ol>'); list = null; } };
  for (const line of lines) {
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { close(); out.push(`<p class="mdhead">${inline(m[2])}</p>`); }
    else if ((m = line.match(/^\s*(?:•|-|\*)\s+(.*)/))) { if (list !== 'ul') { close(); out.push('<ul>'); list = 'ul'; } out.push(`<li>${inline(m[1])}</li>`); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== 'ol') { close(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inline(m[1])}</li>`); }
    else if (!line.trim()) { close(); }
    else { close(); out.push(`<p>${inline(line)}</p>`); }
  }
  close();
  return out.join('').replace(/\u0000(\d+)\u0000/g, (m, i) => `<code>${code[+i]}</code>`);
}
function addMessage(text, who='assistant') { const el = document.createElement('div'); el.className = `message ${who}`; el.innerHTML = who === 'assistant' ? renderMd(text) : text.split('\n').map(x => `<p>${escapeHtml(x)}</p>`).join(''); const stick = nearBottom(); $('conversation').append(el); if (stick) $('conversation').scrollTop = $('conversation').scrollHeight; return el; }
function addAssistant(text) { return addMessage(text, 'assistant'); }
function listingCard(x, kind) {
  const why = kind === 'maybe' ? (x.why_fit || []).slice(-2).join(' · ') : (x.why_fit || []).slice(0,3).join(' · ');
  return `<a class="result-card ${kind}" href="${x.streetview}" target="_blank" rel="noopener" data-id="${x.id}"><div class="result-title"><span>${escapeHtml(x.title)}</span><span>${escapeHtml(x.price_display)}</span></div><div class="result-meta">${x.beds} BHK · ${escapeHtml((x.area || '').split(',')[0] || 'Financial District')} · ${x.area_sqft ? x.area_sqft.toLocaleString("en-IN") + " sq ft" : "sq ft not disclosed"} · ${x.floor == null ? "Floor not disclosed" : "Floor " + x.floor + " of " + x.total_floors} · ~${x.commute_min} min commute</div><div class="result-why">${kind === 'maybe' ? 'Worth considering: ' : 'Why it fits: '}${escapeHtml(why)}</div></a>`;
}
function addResults(r) {
  const box = document.createElement('section');
  const visit = r.fit.slice(0, 6), maybe = (r.maybe || []).slice(0, 5);
  box.innerHTML = `<div class="results-heading"><span>Worth visiting</span><span class="count">${r.fit.length}</span></div>${visit.map(x => listingCard(x, 'visit')).join('') || '<div class="empty">No exact matches yet. Try widening the budget, floor range, or commute in your next message.</div>'}<div class="results-heading"><span>Maybe worth visiting</span><span class="count">${(r.maybe || []).length}</span></div>${maybe.map(x => listingCard(x, 'maybe')).join('') || '<div class="empty">No close calls right now.</div>'}`;
  $('conversation').append(box); pin();
  box.querySelectorAll('[data-id]').forEach(a => a.onclick = () => { const x = [...r.fit, ...(r.maybe || [])].find(v => v.id === a.dataset.id); if (x) map.setView([x.lat, x.lng], 15); });
}
function pinLabel(x) { return `${x.title} · ${x.lat.toFixed(4)}, ${x.lng.toFixed(4)}`; }
function labelOpts(dy) { return {permanent: true, direction: 'top', offset: [0, dy || -12], className: 'pin-label', opacity: 1}; }
function renderMap(r) {
  markers.forEach(m => map.removeLayer(m)); exclLayers.forEach(l => map.removeLayer(l)); if (commuteCircle) map.removeLayer(commuteCircle); markers = []; exclLayers = [];
  commuteCircle = L.circle([work.lat, work.lng], {radius:(r.meta.commute_radius_km || 4.5) * 1000, color:'#176b45', weight:1.5, fillOpacity:.05}).addTo(map).bindPopup('Your commute area');
  (r.meta.exclusions || []).forEach(h => exclLayers.push(L.circle([h.lat,h.lng], {radius:h.radius_km*1000,color:'#bb4d42',weight:1,fillOpacity:.05}).addTo(map)));
  r.fit.forEach(x => markers.push(L.marker([x.lat,x.lng]).addTo(map).bindPopup(`<b>${escapeHtml(x.title)}</b><br>${x.price_display} · ${x.commute_min} min`).bindTooltip(pinLabel(x), labelOpts(-14))));
  (r.maybe || []).forEach(x => markers.push(L.circleMarker([x.lat,x.lng], {radius:8,color:'#c67528',fillColor:'#f6c678',fillOpacity:.9,weight:2}).addTo(map).bindPopup(`<b>Maybe: ${escapeHtml(x.title)}</b><br>${x.price_display} · ${x.commute_min} min`).bindTooltip(pinLabel(x), labelOpts(-12))));
  (r.rejected || []).forEach(x => markers.push(L.circleMarker([x.lat,x.lng], {radius:5,color:'#b9bdb9',fillColor:'#cfd2cf',fillOpacity:.7,weight:1}).addTo(map).bindPopup(`<b>Cut: ${escapeHtml(x.title)}</b><br>${escapeHtml((x.why_out || []).join('; '))}`).bindTooltip(pinLabel(x), labelOpts(-10))));
}
let streamPins = {};
function streamText(el, t) {
  el._raw = (el._raw || '') + t;
  const stick = nearBottom();
  el.innerHTML = renderMd(el._raw);
  if (stick) $('conversation').scrollTop = $('conversation').scrollHeight;
}
function addThought(text) {
  const el = document.createElement('div'); el.className = 'message thought';
  el.innerHTML = `<p>${escapeHtml(text)}</p>`;
  const stick = nearBottom();
  $('conversation').append(el); if (stick) $('conversation').scrollTop = $('conversation').scrollHeight; return el;
}
function plotStream(places) {
  Object.values(streamPins).forEach(m => map.removeLayer(m)); streamPins = {};
  places.forEach(x => {
    streamPins[x.id] = L.circleMarker([x.lat, x.lng], {radius: 7, color: '#4b7fc4', fillOpacity: .8}).addTo(map).bindPopup(`<b>${escapeHtml(x.title)}</b>`);
  });
}
function greyStage(ids) {
  (ids || []).forEach(id => { const m = streamPins[id]; if (m) m.setStyle({color: '#b9bdb9', fillOpacity: .3}); });
}
async function search(message) {
  if (!message.trim()) return;
  addMessage(message, 'user'); $('q').value = ''; $('go').disabled = true;
  if (message.trim().startsWith('@agent')) {
    const loading = addAssistant('Agent is thinking… (opencode terminal)');
    try {
      const r = await api('/api/agent', {message: message.replace(/^@agent\s*/, ''), prefs: currentPrefs});
      loading.remove(); addAssistant(`[agent: ${r.model}] ` + r.reply);
    } catch (e) { loading.textContent = 'Agent error: ' + e.message; }
    finally { $('go').disabled = false; $('q').focus(); }
    return;
  }
  const bubble = addAssistant('');
  bubble._raw = '';
  const params = new URLSearchParams({message, prefs: JSON.stringify(currentPrefs),
    work_lat: work.lat, work_lng: work.lng, session_id: window._session || ''});
  const es = new EventSource('/api/chat/stream?' + params);
  const done = () => { try { es.close(); } catch (e) {} $('go').disabled = false; $('q').focus(); };
  es.onmessage = e => {
    let d;
    try { d = JSON.parse(e.data); } catch (err) { return; }
    if (d.type === 'thought') addThought(d.text);
    else if (d.type === 'token') streamText(bubble, d.text);
    else if (d.type === 'plot') plotStream(d.places);
    else if (d.type === 'stage') greyStage(d.cut);
    else if (d.type === 'result') {
      currentPrefs = d.prefs || currentPrefs;
      if (d.session_id) window._session = d.session_id;
      Object.values(streamPins).forEach(m => map.removeLayer(m)); streamPins = {};
      if (d.searched) { addResults(d); renderMap(d); }
    }
    else if (d.type === 'done') done();
  };
  es.onerror = () => { streamText(bubble, '\n(connection lost — please retry.)'); done(); };
}
$('dropWork').onclick = () => {
  dropWork = !dropWork;
  $('dropWork').textContent = dropWork ? 'Click map to place…' : 'Drop workplace';
  $('dropWork').classList.toggle('active', dropWork);
};
$('composer').onsubmit = e => { e.preventDefault(); search($('q').value); };
$('q').oninput = () => { $('q').style.height = 'auto'; $('q').style.height = Math.min($('q').scrollHeight,120) + 'px'; };
document.querySelectorAll('[data-prompt]').forEach(b => b.onclick = () => search(b.dataset.prompt));
fetch('/api/health').then(r => r.json()).then(h => $('health').textContent = h.llm ? 'AI-assisted search' : 'Natural-language search').catch(() => {});
