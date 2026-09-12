let map, workPin, commuteCircle, exclLayers=[], markers=[];
let work = {lat: 1.2841, lng: 103.8511}, dropWork = true, near = null;
map = L.map('map').setView([1.3521, 103.8198], 11);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap'}).addTo(map);
workPin = L.marker([work.lat, work.lng], {draggable: true}).addTo(map).bindPopup('Work — drag me or click map');
workPin.on('dragend', () => { const p = workPin.getLatLng(); work = {lat: p.lat, lng: p.lng}; run(); });
map.on('click', e => { if (!dropWork) return; work = {lat: e.latlng.lat, lng: e.latlng.lng}; workPin.setLatLng(e.latlng); run(); });

// deep-link: /?place=sg-01
const qp = new URLSearchParams(location.search);
const focusPlace = qp.get('place');

async function api(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  return r.json();
}
function prefs() {
  const p = {budget_hard: +budget.value, floor_min: +floormin.value, floor_max: +floormax.value,
    max_commute_min: +commute.value, work_lat: work.lat, work_lng: work.lng, property_type: 'apartment'};
  if (near) { p.near_lat = near.lat; p.near_lng = near.lng; p.near_radius_km = 3; }
  return p;
}
function card(x, cls) {
  const link = location.origin + location.pathname + '?place=' + x.id;
  return `<div class="card ${cls}"><b>${x.title}</b><br>$${x.price} · fl ${x.floor} · ${x.beds}BR · ~${x.commute_min}min · trust ${x.trust}`
    + `<br><small>${cls === 'cut' ? '✕ ' + (x.why_out||[]).join('; ') : '✓ ' + (x.why_fit||[]).join('; ')}</small>`
    + `<br><a target="_blank" href="${x.streetview}">Street View</a> · <a target="_blank" href="https://wa.me/?text=${encodeURIComponent(x.title + ' ' + link)}">Family share</a> · <button data-copy="${link}">Copy link</button></div>`;
}
async function run(chatMsg) {
  markers.forEach(m => map.removeLayer(m)); markers = [];
  exclLayers.forEach(l => map.removeLayer(l)); exclLayers = [];
  if (commuteCircle) map.removeLayer(commuteCircle);
  const body = chatMsg ? {message: chatMsg, ...prefs()} : prefs();
  const r = await api(chatMsg ? '/api/chat' : '/api/sieve', body);
  reply.textContent = r.reply || r.summary;
  fitN.textContent = r.fit.length; mayN.textContent = (r.maybe||[]).length; rejN.textContent = r.rejected.length;
  cards.innerHTML = r.fit.map(x => card(x, 'fit')).join('') || '<i>No visits — loosen a filter.</i>';
  maybe.innerHTML = (r.maybe||[]).map(x => card(x, 'maybe')).join('');
  rej.innerHTML = r.rejected.map(x => card(x, 'cut')).join('');
  // commute perimeter
  commuteCircle = L.circle([work.lat, work.lng], {radius: (r.meta.commute_radius_km||5)*1000, color: 'green', fillOpacity: 0.05}).addTo(map);
  (r.meta.exclusions||[]).forEach(h => exclLayers.push(L.circle([h.lat, h.lng], {radius: h.radius_km*1000, color: 'red', fillOpacity: 0.08}).addTo(map).bindPopup('Excluded: ' + h.name)));
  r.fit.forEach(x => markers.push(L.marker([x.lat, x.lng]).addTo(map).bindPopup(`<b>${x.title}</b><br><a target=_blank href="${x.streetview}">Street View</a> <a href="?place=${x.id}">Share</a>`)));
  (r.maybe||[]).forEach(x => markers.push(L.circleMarker([x.lat, x.lng], {radius: 8, color: 'orange'}).addTo(map).bindPopup('MAYBE: ' + (x.why_fit||[]).join('; '))));
  r.rejected.forEach(x => markers.push(L.circleMarker([x.lat, x.lng], {radius: 5, color: 'grey'}).addTo(map).bindPopup('CUT: ' + (x.why_out||[]).join('; '))));
  if (focusPlace) {
    const all = [...r.fit, ...(r.maybe||[]), ...r.rejected];
    const f = all.find(x => x.id === focusPlace);
    if (f) map.setView([f.lat, f.lng], 14);
  }
  document.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => { navigator.clipboard.writeText(b.dataset.copy); b.textContent = 'Copied!'; });
  return r;
}
go.onclick = () => run(q.value || '');
workBtn.onclick = () => { dropWork = !dropWork; workBtn.textContent = `💼 Drop work pin: ${dropWork ? 'ON (click map)' : 'OFF'}`; };
nearBtn.onclick = () => {
  if (!navigator.geolocation) return alert('No GPS');
  navigator.geolocation.getCurrentPosition(p => {
    near = {lat: p.coords.latitude, lng: p.coords.longitude};
    L.circle([near.lat, near.lng], {radius: 3000, color: 'blue', fillOpacity: 0.05}).addTo(map).bindPopup('You — 3km near-me filter on').openPopup();
    map.setView([near.lat, near.lng], 13); run();
  });
};
fetch('/api/health').then(r => r.json()).then(h => health.textContent = h.llm ? 'LLM: ' + h.model : 'local sieve (no key needed)');
run();
