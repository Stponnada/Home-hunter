let map, workPin, commuteCircle, exclLayers = [], markers = [], currentPrefs = {};
let work = {lat: 17.4148, lng: 78.3488};
const $ = id => document.getElementById(id);

map = L.map('map', {zoomControl: false}).setView([17.41, 78.355], 12);
L.control.zoom({position: 'bottomleft'}).addTo(map);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap'}).addTo(map);
workPin = L.marker([work.lat, work.lng]).addTo(map).bindPopup('<b>Financial District</b><br>Your default commute origin.');

async function api(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (!r.ok) throw new Error('Could not complete that search.');
  return r.json();
}
function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function addMessage(text, who='assistant') { const el = document.createElement('div'); el.className = `message ${who}`; el.innerHTML = text.split('\n').map(x => `<p>${escapeHtml(x)}</p>`).join(''); $('conversation').append(el); $('conversation').scrollTop = $('conversation').scrollHeight; return el; }
function addAssistant(text) { return addMessage(text, 'assistant'); }
function listingCard(x, kind) {
  const why = kind === 'maybe' ? (x.why_fit || []).slice(-2).join(' · ') : (x.why_fit || []).slice(0,3).join(' · ');
  return `<a class="result-card ${kind}" href="${x.streetview}" target="_blank" rel="noopener" data-id="${x.id}"><div class="result-title"><span>${escapeHtml(x.title)}</span><span>${escapeHtml(x.price_display)}</span></div><div class="result-meta">${x.beds} BHK · ${x.area_sqft ? x.area_sqft.toLocaleString("en-IN") + " sq ft" : "Area not disclosed"} · ${x.floor == null ? "Floor not disclosed" : "Floor " + x.floor + " of " + x.total_floors} · ~${x.commute_min} min commute</div><div class="result-why">${kind === 'maybe' ? 'Worth considering: ' : 'Why it fits: '}${escapeHtml(why)}</div></a>`;
}
function addResults(r) {
  const box = document.createElement('section');
  const visit = r.fit.slice(0, 6), maybe = (r.maybe || []).slice(0, 5);
  box.innerHTML = `<div class="results-heading"><span>Worth visiting</span><span class="count">${r.fit.length}</span></div>${visit.map(x => listingCard(x, 'visit')).join('') || '<div class="empty">No exact matches yet. Try widening the budget, floor range, or commute in your next message.</div>'}<div class="results-heading"><span>Maybe worth visiting</span><span class="count">${(r.maybe || []).length}</span></div>${maybe.map(x => listingCard(x, 'maybe')).join('') || '<div class="empty">No close calls right now.</div>'}`;
  $('conversation').append(box); $('conversation').scrollTop = $('conversation').scrollHeight;
  box.querySelectorAll('[data-id]').forEach(a => a.onclick = () => { const x = [...r.fit, ...(r.maybe || [])].find(v => v.id === a.dataset.id); if (x) map.setView([x.lat, x.lng], 15); });
}
function renderMap(r) {
  markers.forEach(m => map.removeLayer(m)); exclLayers.forEach(l => map.removeLayer(l)); if (commuteCircle) map.removeLayer(commuteCircle); markers = []; exclLayers = [];
  commuteCircle = L.circle([work.lat, work.lng], {radius:(r.meta.commute_radius_km || 4.5) * 1000, color:'#176b45', weight:1.5, fillOpacity:.05}).addTo(map).bindPopup('Your commute area');
  (r.meta.exclusions || []).forEach(h => exclLayers.push(L.circle([h.lat,h.lng], {radius:h.radius_km*1000,color:'#bb4d42',weight:1,fillOpacity:.05}).addTo(map)));
  r.fit.forEach(x => markers.push(L.marker([x.lat,x.lng]).addTo(map).bindPopup(`<b>${escapeHtml(x.title)}</b><br>${x.price_display} · ${x.commute_min} min`)));
  (r.maybe || []).forEach(x => markers.push(L.circleMarker([x.lat,x.lng], {radius:8,color:'#c67528',fillColor:'#f6c678',fillOpacity:.9,weight:2}).addTo(map).bindPopup(`<b>Maybe: ${escapeHtml(x.title)}</b><br>${x.price_display} · ${x.commute_min} min`)));
}
async function search(message) {
  if (!message.trim()) return;
  addMessage(message, 'user'); $('q').value = ''; $('go').disabled = true;
  const loading = addAssistant('I’m looking for places that match that…');
  try {
    const r = await api('/api/chat', {message, prefs:currentPrefs, work_lat:work.lat, work_lng:work.lng});
    currentPrefs = r.prefs; loading.remove(); addAssistant(r.reply); addResults(r); renderMap(r);
  } catch (e) { loading.textContent = e.message; }
  finally { $('go').disabled = false; $('q').focus(); }
}
$('composer').onsubmit = e => { e.preventDefault(); search($('q').value); };
$('q').oninput = () => { $('q').style.height = 'auto'; $('q').style.height = Math.min($('q').scrollHeight,120) + 'px'; };
document.querySelectorAll('[data-prompt]').forEach(b => b.onclick = () => search(b.dataset.prompt));
fetch('/api/health').then(r => r.json()).then(h => $('health').textContent = h.llm ? 'AI-assisted search' : 'Natural-language search').catch(() => {});
