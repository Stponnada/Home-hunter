let map, workPin, commuteCircle, exclLayers=[], markers=[], allPins={};
let work = {lat: 17.4148, lng: 78.3488}, dropWork = true, near = null;
map = L.map('map').setView([17.41, 78.355], 12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap'}).addTo(map);
workPin = L.marker([work.lat, work.lng], {draggable: true}).addTo(map).bindPopup('Work: Financial District — drag me or click map');
workPin.on('dragend', () => { const p = workPin.getLatLng(); work = {lat: p.lat, lng: p.lng}; run(); });
map.on('click', e => { if (!dropWork) return; work = {lat: e.latlng.lat, lng: e.latlng.lng}; workPin.setLatLng(e.latlng); run(); });

const qp = new URLSearchParams(location.search);
const focusPlace = qp.get('place');
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  return r.json();
}
function prefs() {
  const p = {budget_hard: +budget.value, floor_min: +floormin.value, floor_max: +floormax.value,
    max_commute_min: +commute.value, work_lat: work.lat, work_lng: work.lng, property_type: 'apartment', city: 'Hyderabad'};
  if (near) { p.near_lat = near.lat; p.near_lng = near.lng; p.near_radius_km = 3; }
  return p;
}
function card(x, cls) {
  const link = location.origin + location.pathname + '?place=' + x.id;
  return `<div class="card ${cls}" id="card-${x.id}"><b>${x.title}</b><br>${x.price_display || x.price} · fl ${x.floor} · ${x.beds}BR · ~${x.commute_min}min · trust ${x.trust}`
    + `<br><small>${cls === 'cut' ? '✕ ' + (x.why_out||[]).join('; ') : '✓ ' + (x.why_fit||[]).join('; ')}</small>`
    + `<br><a target="_blank" href="${x.streetview}">Street View</a> · <a target="_blank" href="https://wa.me/?text=${encodeURIComponent(x.title + ' ' + link)}">Family share</a> · <button data-copy="${link}">Copy link</button> · <button data-intel="${x.area}">Local intel</button></div>`;
}
function plotAll(list) {
  Object.values(allPins).forEach(m => map.removeLayer(m)); allPins = {};
  list.forEach(x => {
    allPins[x.id] = L.circleMarker([x.lat, x.lng], {radius: 7, color: 'blue'}).addTo(map).bindPopup(`<b>${x.title}</b>`);
  });
}
async function run(chatMsg) {
  markers.forEach(m => map.removeLayer(m)); markers = [];
  exclLayers.forEach(l => map.removeLayer(l)); exclLayers = [];
  if (commuteCircle) map.removeLayer(commuteCircle);
  Object.values(allPins).forEach(m => map.removeLayer(m)); allPins = {};
  reply.textContent = 'Searching… plotting every pin first.';
  const body = chatMsg ? {message: chatMsg, ...prefs()} : prefs();
  const r = await api(chatMsg ? '/api/chat' : '/api/sieve', body);
  const all = [...r.fit, ...(r.maybe||[]), ...r.rejected];
  plotAll(all);
  reply.textContent = `${all.length} places on map. Sieving…`;
  await sleep(500);
  // staged removal animation
  for (const st of (r.meta.stages || [])) {
    if (!st.cut.length) continue;
    reply.textContent = `✂ ${st.label}: cutting ${st.cut.length} — ${st.cut.slice(0,2).map(c=>c.title).join(' · ')}${st.cut.length>2?' …':''}`;
    st.cut.forEach(c => {
      const m = allPins[c.id];
      if (m) m.setStyle({color: 'grey', fillOpacity: 0.3});
      const el = document.getElementById('card-' + c.id);
      if (el) el.classList.add('cut');
    });
    await sleep(650);
  }
  reply.textContent = r.reply || r.summary;
  fitN.textContent = r.fit.length; mayN.textContent = (r.maybe||[]).length; rejN.textContent = r.rejected.length;
  cards.innerHTML = r.fit.map(x => card(x, 'fit')).join('') || '<i>No visits — loosen a filter.</i>';
  maybe.innerHTML = (r.maybe||[]).map(x => card(x, 'maybe')).join('');
  rej.innerHTML = r.rejected.map(x => card(x, 'cut')).join('');
  commuteCircle = L.circle([work.lat, work.lng], {radius: (r.meta.commute_radius_km||4.5)*1000, color: 'green', fillOpacity: 0.05}).addTo(map).bindPopup('Commute perimeter');
  (r.meta.exclusions||[]).forEach(h => exclLayers.push(L.circle([h.lat, h.lng], {radius: h.radius_km*1000, color: 'red', fillOpacity: 0.08}).addTo(map).bindPopup('Excluded: ' + h.name)));
  Object.values(allPins).forEach(m => map.removeLayer(m)); allPins = {};
  r.fit.forEach(x => markers.push(L.marker([x.lat, x.lng]).addTo(map).bindPopup(`<b>${x.title}</b><br>${x.price_display} fl ${x.floor}<br><a target=_blank href="${x.streetview}">Street View</a> <a href="?place=${x.id}">Share</a>`)));
  (r.maybe||[]).forEach(x => markers.push(L.circleMarker([x.lat, x.lng], {radius: 8, color: 'orange'}).addTo(map).bindPopup('MAYBE: ' + (x.why_fit||[]).join('; '))));
  r.rejected.forEach(x => markers.push(L.circleMarker([x.lat, x.lng], {radius: 5, color: 'grey'}).addTo(map).bindPopup('CUT: ' + (x.why_out||[]).join('; '))));
  if (focusPlace) {
    const f = all.find(x => x.id === focusPlace);
    if (f) map.setView([f.lat, f.lng], 14);
  }
  document.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => { navigator.clipboard.writeText(b.dataset.copy); b.textContent = 'Copied!'; });
  document.querySelectorAll('[data-intel]').forEach(b => b.onclick = async () => {
    const d = await (await fetch('/api/intel?area=' + encodeURIComponent(b.dataset.intel))).json();
    reply.textContent = `Intel: ${d.area}\nQueries: ${d.queries.join(' | ')}\nGoogle: ${d.links.google}\nReddit: ${d.links.reddit}\n` + (d.tavily.length ? d.tavily.map(t=>t.title||t.url).join('\n') : '(plug TAVILY_API_KEY for auto-fetched reviews)');
  });
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
