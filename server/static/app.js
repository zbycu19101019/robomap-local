(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const svg = $('mapSvg');
  const layers = {
    trail: $('trailLayer'), furniture: $('furnitureLayer'), wall: $('wallLayer'), opening: $('openingLayer'),
    virtual: $('virtualWallLayer'), zone: $('zoneLayer'), dock: $('dockLayer'), robot: $('robotLayer'), overlay: $('overlayLayer'),
  };
  const NS = 'http://www.w3.org/2000/svg';
  const mk = (tag, attrs = {}) => {
    const el = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
    return el;
  };
  const uid = (prefix) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const round = (v) => Math.round(v * 100) / 100;

  let doc = null;
  let robot = null;
  let localization = null;
  let liveLast=0, liveSocket=null, liveRetry=1000;
  function connectLive(){
    liveSocket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/live`);
    liveSocket.onmessage=e=>{try{
      const m=JSON.parse(e.data);if(m.type!=='live')return;
      liveLast=Date.now();liveRetry=1000;
      if(m.robot){robot=m.robot;updateRobotUi();}
      localization=m.localization;updateLocalizationUi();if(!pointerAction)renderMap();
    }catch(e){console.warn('Nieprawidłowa aktualizacja panelu');}};
    liveSocket.onclose=()=>{liveLast=0;setTimeout(connectLive,liveRetry);liveRetry=Math.min(liveRetry*2,15000);};
    liveSocket.onerror=()=>liveSocket.close();
  }
  const liveHealthy=()=>Date.now()-liveLast<3000;
  $('mapHistoryLoad').addEventListener('click',async()=>{
    try{const versions=await api('/api/maps/history');$('mapHistorySelect').replaceChildren();
      for(const v of versions){const o=document.createElement('option');o.value=v.revision;o.textContent=`Wersja ${v.revision} · ${v.created} UTC`;$('mapHistorySelect').append(o);}
    }catch(e){toast(e.message);}
  });
  $('mapHistoryRestore').addEventListener('click',async()=>{
    if(!$('mapHistorySelect').value)return;
    if($('saveState').textContent!=='zapisane'){toast('Najpierw zapisz bieżące zmiany lub odśwież mapę.');return;}
    try{const old=await api('/api/maps/history/'+$('mapHistorySelect').value);
      old.revision=doc.revision;doc=old;selected=null;markDirty();renderMap();fitMap();
      toast('Wersja wczytana do edytora. Kliknij Zapisz mapę, aby ją przywrócić.');
    }catch(e){toast(e.message);}
  });
  let survey = {points: [], walls: [], active: false};
  $('mappingPrepare').addEventListener('click', async () => {
    const button=$('mappingPrepare');button.disabled=true;
    $('mappingGuide').textContent='Wstrzymuję ruch i sprawdzam stan robota…';
    try {
      const result=await api('/api/mapping/prepare',{method:'POST',body:'{}'});
      setTool('robot');
      $('mappingGuide').textContent=result.detail;
      svg.scrollIntoView({block:'center',behavior:'smooth'});
      toast(result.detail,6000);
    } catch(e) { $('mappingGuide').textContent=e.message;toast(e.message); }
    finally {button.disabled=false;}
  });
  const surveyLayer = mk('g', {id:'surveyLayer', 'pointer-events':'none'});
  svg.append(surveyLayer);
  function renderSurvey() {
    surveyLayer.replaceChildren();
    if (!$('surveyVisible')?.checked) return;
    for (const p of survey.points) surveyLayer.append(mk('circle', {cx:p.x, cy:p.y, r:.085, fill:'#ff4867', opacity:.7}));
    for (const w of survey.walls) surveyLayer.append(mk('line', {x1:w.start.x,y1:w.start.y,x2:w.end.x,y2:w.end.y,stroke:'#ffd166','stroke-width':.055,'stroke-dasharray':'.12 .08'}));
  }
  function updateSurvey(s) {
    survey=s; renderSurvey();
    $('surveyState').textContent=`${s.active?'ZAPIS AKTYWNY':'ZAPIS WSTRZYMANY'} · ${s.points.length} punktów · ${s.walls.length} propozycji ścian · ${s.unlocated} zdarzeń bez wiarygodnej pozycji`;
  }
  for (const [id,action] of [['surveyStart','start'],['surveyPause','pause'],['surveyContact','contact']]) {
    $(id).addEventListener('click',async()=>{
      $(id).disabled=true;
      try {updateSurvey(await api('/api/mapping/'+action,{method:'POST',body:'{}'}));}
      catch(e){toast(e.message);} finally {$(id).disabled=false;}
    });
  }
  $('surveyVisible').addEventListener('change',renderSurvey);
  $('surveyAccept').addEventListener('click',()=>{
    if(!doc || !survey.walls.length){toast('Brak propozycji: zbierz minimum 3 pomiary wzdłuż ściany.');return;}
    doc.walls ||= [];
    for(const w of survey.walls) if(!doc.walls.some(v=>v.id===w.id)) doc.walls.push({id:w.id,start:{...w.start},end:{...w.end}});
    markDirty();renderMap();toast('Szkic dodany do edytora. Popraw ściany i zapisz mapę.');
  });
  $('surveyExport').addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(survey,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='robomap-pomiary.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  let surveyPolling=false;
  async function pollSurvey(){if(surveyPolling)return;surveyPolling=true;try{updateSurvey(await api('/api/mapping'));}catch(e){$('surveyState').textContent='Pomiary niedostępne: '+e.message;}finally{surveyPolling=false;}}
  let tool = 'pan';
  let currentPage = 'control';
  let selected = null;
  let pointerAction = null;
  let viewBox = {x: -1, y: -1, w: 8, h: 6};
  let trail = [];
  let lastRobotOnline = null;
  let manualHeld = null;
  let ignoreManualClickUntil = 0;
  let macroRecordingStartedLocal = null;
  let gamepadLastDirection = 'stop';
  let gamepadLastButtons = {};
  let gamepadLoopId = null;
  let activeGamepadIndex = null;
  let gamepadAckDirection = 'stop';
  let gamepadCandidateDirection = 'stop';
  let gamepadCandidateSince = 0;
  let gamepadNeutralSince = 0;
  let gamepadLastAttemptAt = 0;
  let gamepadKeepaliveBusy = false;
  let gamepadRcActive = false;
  let gamepadRcStarting = null;
  let gamepadRcGeneration = 0;
  let gamepadIssueBusy = false;
  let manualInFlight = false;
  let manualPendingStop = null;
  let manualPendingMove = null;

  function toast(message, duration = 2400) {
    const el = $('toast');
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove('show'), duration);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...(options.headers || {})},
      ...options,
    });
    if (!response.ok) {
      let detail = `${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }

  function stateLabel(state) {
    const map = {
      idle: 'Bezczynny', cleaning: 'Sprzątanie', paused: 'Pauza', error: 'Błąd', charging: 'Ładowanie',
      returning: 'Powrót do bazy', charged: 'Naładowany', docked: 'W bazie', offline: 'Offline', unconfigured: 'Brak konfiguracji', 'awaiting-token': 'E5 wykryty • czeka na token',
    };
    return map[state] || state || '—';
  }

  function robotOnline() {
    return !!robot && !['offline', 'unconfigured', 'awaiting-token'].includes(robot.state);
  }

  function setConnectionUi(mode) {
    const dot = $('connectionDot');
    const mapDot = $('mapStatusDot');
    [dot, mapDot].forEach((el) => { el.classList.remove('online', 'offline', 'connecting'); el.classList.add(mode); });
    const online = mode === 'online';
    $('connectionText').textContent = online ? 'Robot online' : mode === 'connecting' ? 'Łączenie…' : 'Robot offline';
    $$('.robot-command').forEach((el) => { el.disabled = !online; });
    $('offlineOverlay').classList.toggle('hidden', online || mode === 'connecting');
  }

  function batteryTone(value) {
    if (value == null) return '—';
    if (value < 20) return `🔴 ${value}%`;
    if (value < 50) return `🟡 ${value}%`;
    return `🟢 ${value}%`;
  }

  function updateRobotUi() {
    window.dispatchEvent(new CustomEvent('robomap-status', {detail: robot}));
    const online = robotOnline();
    const waitingToken = robot?.state === 'awaiting-token';
    setConnectionUi(robot ? (online ? 'online' : (waitingToken ? 'connecting' : 'offline')) : 'connecting');
    if (waitingToken) {
      $('connectionText').textContent = 'E5 wykryty • token';
      $('offlineOverlay').classList.remove('hidden');
      $('offlineOverlay').querySelector('strong').textContent = 'E5 znaleziony';
      $('offlineOverlay').querySelector('span').textContent = 'Robot jest w sieci. Brakuje tylko autoryzacji AutoToken.';
      $('configureTokenBtn')?.classList.remove('hidden');
    } else {
      $('offlineOverlay').querySelector('strong').textContent = 'Robot offline';
      $('offlineOverlay').querySelector('span').textContent = 'Sprawdź Wi‑Fi lub uruchom AutoPair.';
      $('configureTokenBtn')?.classList.add('hidden');
    }
    const state = stateLabel(robot?.state);
    const battery = robot?.battery;
    $('stateText').textContent = state;
    $('batteryText').textContent = battery == null ? '—' : `${battery}%`;
    $('headerBattery').textContent = battery == null ? '—' : `${battery}%`;
    $('mapBattery').textContent = batteryTone(battery);
    $('mapStatusText').textContent = state;
    $('robotDetail').textContent = robot?.detail || '';
    $('robotStateBadge').textContent = state;
    $('robotStateBadge').classList.toggle('live', online && robot?.state === 'cleaning');
    if (robot?.pose || robot?.pose_source) {
      localization = {
        ...(localization || {}),
        pose: robot.pose || localization?.pose || null,
        source: robot.pose_source || localization?.source || 'uncalibrated',
        confidence: robot.pose_confidence ?? localization?.confidence ?? 0,
        calibrated: !!robot.supports_pose,
        updated_at: robot.pose_updated_at || localization?.updated_at,
        note: robot.pose_note || localization?.note || '',
      };
      updateLocalizationUi();
    }

    if (lastRobotOnline === true && !online) pulseGamepad(120, 0.35);
    if (robot?.state === 'error') pulseGamepad(180, 0.6);
    lastRobotOnline = online;
    if (!pointerAction) renderMap();
  }

  async function refreshRobot() {
    try {
      robot = await api('/api/robot/status');
      updateRobotUi();
    } catch (err) {
      robot = {state: 'offline', battery: null, pose: null, detail: err.message};
      updateRobotUi();
    }
  }

  // ---------------- Map ----------------
  function svgPoint(event) {
    const pt = svg.createSVGPoint();
    pt.x = event.clientX; pt.y = event.clientY;
    const matrix = svg.getScreenCTM();
    if (!matrix) return {x: 0, y: 0};
    const r = pt.matrixTransform(matrix.inverse());
    return {x: r.x, y: r.y};
  }

  function setViewBox(next) {
    const w = clamp(next.w, 1, 80);
    const h = clamp(next.h, 0.75, 60);
    viewBox = {x: next.x, y: next.y, w, h};
    svg.setAttribute('viewBox', `${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`);
  }

  function allPoints() {
    if (!doc) return [];
    const pts = [];
    for (const group of [doc.walls, doc.doors, doc.windows, doc.openings, doc.virtual_walls]) {
      for (const s of group || []) pts.push(s.start, s.end);
    }
    for (const f of doc.furniture || []) {
      pts.push({x: f.center.x - f.width / 2, y: f.center.y - f.depth / 2}, {x: f.center.x + f.width / 2, y: f.center.y + f.depth / 2});
    }
    for (const z of doc.no_go_zones || []) pts.push({x: z.x, y: z.y}, {x: z.x + z.width, y: z.y + z.height});
    if (doc.dock) pts.push(doc.dock);
    if (localization?.calibrated && localization?.pose) pts.push(localization.pose);
    return pts.filter(Boolean);
  }

  function fitMap() {
    const pts = allPoints();
    if (!pts.length) { setViewBox({x: -3, y: -2, w: 6, h: 4}); return; }
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    let minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const pad = Math.max(.6, Math.max(maxX - minX, maxY - minY) * .12);
    minX -= pad; minY -= pad; maxX += pad; maxY += pad;
    let w = Math.max(.8, maxX - minX), h = Math.max(.8, maxY - minY);
    const rect = svg.getBoundingClientRect();
    const ratio = rect.width > 0 && rect.height > 0 ? rect.width / rect.height : 1.4;
    if (w / h > ratio) { const nh = w / ratio; minY -= (nh - h) / 2; h = nh; }
    else { const nw = h * ratio; minX -= (nw - w) / 2; w = nw; }
    setViewBox({x: minX, y: minY, w, h});
  }

  function zoom(factor) {
    const cx = viewBox.x + viewBox.w / 2, cy = viewBox.y + viewBox.h / 2;
    const w = viewBox.w * factor, h = viewBox.h * factor;
    setViewBox({x: cx - w / 2, y: cy - h / 2, w, h});
    renderMap();
  }

  function markDirty() { $('saveState').textContent = 'niezapisane'; $('saveState').style.color = 'var(--warning)'; }
  function markSaved() { $('saveState').textContent = 'zapisane'; $('saveState').style.color = ''; }
  function clearLayer(layer) { while (layer.firstChild) layer.removeChild(layer.firstChild); }
  function prettyCategory(value) { return String(value || 'mebel').replace(/[-_]/g, ' ').replace(/demo/gi, '').trim().slice(0, 18) || 'mebel'; }

  function renderHandles(z) {
    const pts = [['nw', z.x, z.y], ['ne', z.x + z.width, z.y], ['sw', z.x, z.y + z.height], ['se', z.x + z.width, z.y + z.height]];
    const size = Math.max(.10, viewBox.w / 70);
    pts.forEach(([pos, x, y]) => {
      const h = mk('rect', {x: x - size / 2, y: y - size / 2, width: size, height: size, rx: size * .18, class: 'handle'});
      h.dataset.handle = pos; h.dataset.id = z.id; layers.overlay.append(h);
    });
  }

  function localizationLabel(source) {
    const labels = {
      dock: 'BAZA / PEWNA',
      'manual-calibration': 'KALIBRACJA',
      'rc-odometry': 'RC / EST',
      'autonomous-last-known': 'OSTATNIA ZNANA',
      uncalibrated: 'NIEKALIBROWANA',
      'uncalibrated-odometry': 'RADAR / 0,0',
    };
    return labels[source] || String(source || 'EST').toUpperCase();
  }

  function renderRobot(pose) {
    const confidence = clamp(Number(localization?.confidence ?? robot?.pose_confidence ?? 0), 0, 1);
    const source = localization?.source || robot?.pose_source || 'estimated';
    const g = mk('g', {transform: `translate(${pose.x} ${pose.y}) rotate(${(pose.heading || 0) * 180 / Math.PI})`, class: 'robot-marker'});
    const ring = mk('circle', {cx: 0, cy: 0, r: .31, class: `robot-confidence ${confidence > .78 ? 'high' : confidence > .38 ? 'medium' : 'low'}`});
    ring.setAttribute('stroke-dasharray', `${Math.max(.1, confidence * 1.95)} 2`);
    g.append(ring);
    g.append(mk('circle', {cx: 0, cy: 0, r: .18, class: 'robot-body'}));
    // Heading 0 points to the top of the map; positive heading turns clockwise.
    g.append(mk('line', {x1: 0, y1: 0, x2: 0, y2: -.19, class: 'robot-arrow'}));
    layers.robot.append(g);
    const label = mk('g', {transform: `translate(${pose.x} ${pose.y + .43})`, class: 'robot-map-label'});
    const bg = mk('rect', {x: -.68, y: -.12, width: 1.36, height: .24, rx: .07, class: 'robot-label-bg'});
    const text = mk('text', {x: 0, y: .01, class: 'robot-label-text'});
    text.textContent = `${localizationLabel(source)} · ${Math.round(confidence * 100)}%`;
    label.append(bg, text); layers.robot.append(label);
  }

  function renderTrail() {
    const points = localization?.trail?.length ? localization.trail : trail;
    if (!points || points.length < 2) return;
    const d = points.map((p, i) => `${i ? 'L' : 'M'} ${p.x} ${p.y}`).join(' ');
    layers.trail.append(mk('path', {d, class: 'robot-trail'}));
  }

  function updateInspector() {
    $('deleteBtn').disabled = !selected;
    const z = selected?.type === 'zone' ? (doc?.no_go_zones || []).find((x) => x.id === selected.id) : null;
    if (!z) {
      $('zoneForm').classList.add('hidden'); $('nothingSelected').classList.remove('hidden'); return;
    }
    $('nothingSelected').classList.add('hidden'); $('zoneForm').classList.remove('hidden');
    $('zoneName').value = z.name || '';
    $('zoneX').value = round(z.x); $('zoneY').value = round(z.y); $('zoneW').value = round(z.width); $('zoneH').value = round(z.height);
  }

  function renderMap() {
    if (!doc) return;
    Object.values(layers).forEach(clearLayer);
    $('mapName').textContent = doc.name || 'Mieszkanie';
    $('mapMeta').textContent = `${doc.source || 'manual'} • rev ${doc.revision ?? 0}`;
    $('mapSource').textContent = doc.source || 'manual';
    $('zoneCount').textContent = String(doc.no_go_zones?.length || 0);

    for (const f of doc.furniture || []) {
      const g = mk('g', {transform: `translate(${f.center.x} ${f.center.y}) rotate(${(f.rotation || 0) * 180 / Math.PI})`});
      g.append(mk('rect', {x: -f.width / 2, y: -f.depth / 2, width: f.width, height: f.depth, rx: .06, class: 'furniture'}));
      const t = mk('text', {x: 0, y: 0, class: 'furniture-label'}); t.textContent = prettyCategory(f.category); g.append(t); layers.furniture.append(g);
    }
    for (const w of doc.walls || []) { const line = mk('line', {x1: w.start.x, y1: w.start.y, x2: w.end.x, y2: w.end.y, class: `wall editable-line ${selected?.type === 'structure' && selected.id === w.id ? 'selected-line' : ''}`}); line.dataset.id=w.id; line.dataset.kind='structure'; layers.wall.append(line); }
    for (const d of doc.doors || []) { const line = mk('line', {x1: d.start.x, y1: d.start.y, x2: d.end.x, y2: d.end.y, class: `door editable-line ${selected?.type === 'door' && selected.id === d.id ? 'selected-line' : ''}`}); line.dataset.id=d.id; line.dataset.kind='door'; layers.opening.append(line); }
    for (const w of doc.windows || []) layers.opening.append(mk('line', {x1: w.start.x, y1: w.start.y, x2: w.end.x, y2: w.end.y, class: 'window'}));
    for (const o of doc.openings || []) layers.opening.append(mk('line', {x1: o.start.x, y1: o.start.y, x2: o.end.x, y2: o.end.y, class: 'opening'}));
    for (const v of doc.virtual_walls || []) {
      const line = mk('line', {x1: v.start.x, y1: v.start.y, x2: v.end.x, y2: v.end.y, class: `vwall ${selected?.type === 'wall' && selected.id === v.id ? 'selected' : ''}`});
      line.dataset.id = v.id; line.dataset.kind = 'wall'; layers.virtual.append(line);
    }
    for (const z of doc.no_go_zones || []) {
      const g = mk('g');
      const r = mk('rect', {x: z.x, y: z.y, width: z.width, height: z.height, rx: .05, class: `zone ${selected?.type === 'zone' && selected.id === z.id ? 'selected' : ''}`});
      r.dataset.id = z.id; r.dataset.kind = 'zone';
      const t = mk('text', {x: z.x + z.width / 2, y: z.y + z.height / 2, class: 'zone-label'}); t.textContent = z.name || 'NO-GO';
      g.append(r, t); layers.zone.append(g);
      if (selected?.type === 'zone' && selected.id === z.id) renderHandles(z);
    }
    if (doc.dock) {
      const g = mk('g', {transform: `translate(${doc.dock.x} ${doc.dock.y}) rotate(${(doc.dock.rotation || 0) * 180 / Math.PI})`});
      g.append(mk('rect', {x: -.22, y: -.13, width: .44, height: .26, rx: .05, class: 'dock'}));
      const t = mk('text', {x: 0, y: .015, class: 'dock-text'}); t.textContent = '⌂'; g.append(t); layers.dock.append(g);
    }
    renderTrail();
    const livePose = localization?.calibrated ? localization.pose : robot?.pose;
    if (livePose) renderRobot(livePose);
    if (pointerAction?.type === 'set-robot') {
      const a = pointerAction.start, b = pointerAction.current || a;
      layers.overlay.append(mk('circle', {cx: a.x, cy: a.y, r: .14, class: 'calibration-point'}));
      layers.overlay.append(mk('line', {x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: 'calibration-heading'}));
    }
    updateInspector();
  }

  function setTool(next) {
    tool = next;
    $$('[data-tool]').forEach((b) => b.classList.toggle('active', b.dataset.tool === tool));
    const hints = {
      select: 'Dotknij strefy. Przeciągaj ją lub zmieniaj rozmiar uchwytami.',
      zone: 'Przeciągnij palcem/myszą, aby narysować strefę NO-GO.',
      structure: 'Przeciągnij od początku do końca, aby dodać prawdziwą ścianę mieszkania.',
      door: 'Przeciągnij po otworze drzwiowym, aby zaznaczyć drzwi.',
      wall: 'Przeciągnij od początku do końca wirtualnej granicy NO-GO.',
      dock: 'Dotknij miejsca, w którym stoi stacja dokująca.',
      robot: 'Kliknij pozycję robota i przeciągnij w kierunku, w który jest zwrócony. To kalibruje śledzenie.',
      pan: currentPage === 'map' ? 'Przeciągaj mapę.' : 'Mapa w trybie podglądu — przeciągaj, aby ją przesunąć.',
    };
    $('hint').textContent = hints[next] || '';
  }

  svg.addEventListener('pointerdown', (e) => {
    if (!doc) return;
    svg.setPointerCapture(e.pointerId);
    const p = svgPoint(e);
    if (currentPage !== 'map' && tool !== 'pan') setTool('pan');
    if (tool === 'zone') {
      const z = {id: uid('zone'), name: 'Strefa zakazana', x: p.x, y: p.y, width: .01, height: .01};
      doc.no_go_zones.push(z); selected = {type: 'zone', id: z.id}; pointerAction = {type: 'draw-zone', id: z.id, start: p}; renderMap(); return;
    }
    if (tool === 'structure') {
      const w = {id: uid('home-wall'), start: {...p}, end: {...p}, kind: 'wall'};
      doc.walls.push(w); selected = {type: 'structure', id: w.id}; pointerAction = {type: 'draw-structure', id: w.id, start: p}; renderMap(); return;
    }
    if (tool === 'door') {
      const d = {id: uid('door'), start: {...p}, end: {...p}, kind: 'door'};
      doc.doors.push(d); selected = {type: 'door', id: d.id}; pointerAction = {type: 'draw-door', id: d.id, start: p}; renderMap(); return;
    }
    if (tool === 'wall') {
      const v = {id: uid('wall'), name: 'Wirtualna ściana', start: {...p}, end: {...p}};
      doc.virtual_walls.push(v); selected = {type: 'wall', id: v.id}; pointerAction = {type: 'draw-wall', id: v.id, start: p}; renderMap(); return;
    }
    if (tool === 'dock') { doc.dock = {x: round(p.x), y: round(p.y), rotation: 0}; selected = null; markDirty(); renderMap(); return; }
    if (tool === 'robot') { pointerAction = {type: 'set-robot', start: {...p}, current: {...p}}; renderMap(); return; }
    if (tool === 'pan') { pointerAction = {type: 'pan', startClient: {x: e.clientX, y: e.clientY}, startView: {...viewBox}}; return; }
    if (tool === 'select') {
      const handle = e.target.dataset.handle;
      if (handle) {
        const z = doc.no_go_zones.find((x) => x.id === e.target.dataset.id);
        if (z) pointerAction = {type: 'resize-zone', id: z.id, handle, start: p, original: {...z}};
        return;
      }
      const kind = e.target.dataset.kind, id = e.target.dataset.id;
      if (kind === 'zone' && id) {
        const z = doc.no_go_zones.find((x) => x.id === id);
        selected = {type: 'zone', id}; pointerAction = {type: 'move-zone', id, start: p, original: {...z}}; renderMap(); return;
      }
      if (kind === 'wall' && id) { selected = {type: 'wall', id}; renderMap(); return; }
      if (kind === 'structure' && id) { selected = {type: 'structure', id}; renderMap(); return; }
      if (kind === 'door' && id) { selected = {type: 'door', id}; renderMap(); return; }
      selected = null; renderMap();
    }
  });

  svg.addEventListener('pointermove', (e) => {
    if (!pointerAction || !doc) return;
    const p = svgPoint(e);
    if (pointerAction.type === 'draw-zone') {
      const z = doc.no_go_zones.find((x) => x.id === pointerAction.id); if (!z) return;
      z.x = Math.min(pointerAction.start.x, p.x); z.y = Math.min(pointerAction.start.y, p.y);
      z.width = Math.max(.02, Math.abs(p.x - pointerAction.start.x)); z.height = Math.max(.02, Math.abs(p.y - pointerAction.start.y)); renderMap();
    } else if (pointerAction.type === 'draw-wall') {
      const v = doc.virtual_walls.find((x) => x.id === pointerAction.id); if (!v) return; v.end = {x: p.x, y: p.y}; renderMap();
    } else if (pointerAction.type === 'draw-structure') {
      const w = doc.walls.find((x) => x.id === pointerAction.id); if (!w) return; w.end = {x: p.x, y: p.y}; renderMap();
    } else if (pointerAction.type === 'draw-door') {
      const d = doc.doors.find((x) => x.id === pointerAction.id); if (!d) return; d.end = {x: p.x, y: p.y}; renderMap();
    } else if (pointerAction.type === 'move-zone') {
      const z = doc.no_go_zones.find((x) => x.id === pointerAction.id); if (!z) return;
      z.x = pointerAction.original.x + (p.x - pointerAction.start.x); z.y = pointerAction.original.y + (p.y - pointerAction.start.y); renderMap();
    } else if (pointerAction.type === 'resize-zone') {
      const z = doc.no_go_zones.find((x) => x.id === pointerAction.id); if (!z) return;
      const o = pointerAction.original, h = pointerAction.handle;
      let left = o.x, right = o.x + o.width, top = o.y, bottom = o.y + o.height;
      if (h.includes('w')) left = Math.min(p.x, right - .05); if (h.includes('e')) right = Math.max(p.x, left + .05);
      if (h.includes('n')) top = Math.min(p.y, bottom - .05); if (h.includes('s')) bottom = Math.max(p.y, top + .05);
      z.x = left; z.y = top; z.width = right - left; z.height = bottom - top; renderMap();
    } else if (pointerAction.type === 'set-robot') {
      pointerAction.current = {...p}; renderMap();
    } else if (pointerAction.type === 'pan') {
      const rect = svg.getBoundingClientRect();
      const dx = (e.clientX - pointerAction.startClient.x) / rect.width * pointerAction.startView.w;
      const dy = (e.clientY - pointerAction.startClient.y) / rect.height * pointerAction.startView.h;
      setViewBox({x: pointerAction.startView.x - dx, y: pointerAction.startView.y - dy, w: pointerAction.startView.w, h: pointerAction.startView.h});
    }
  });

  function finishMapPointer() {
    if (!pointerAction) return;
    const action = pointerAction;
    const type = action.type;
    if (type !== 'pan' && type !== 'set-robot') markDirty();
    if (type === 'draw-wall') {
      const v = doc.virtual_walls.find((x) => x.id === action.id);
      if (v && Math.hypot(v.end.x - v.start.x, v.end.y - v.start.y) < .08) doc.virtual_walls = doc.virtual_walls.filter((x) => x.id !== v.id);
    }
    if (type === 'draw-structure') {
      const w = doc.walls.find((x) => x.id === action.id);
      if (w && Math.hypot(w.end.x - w.start.x, w.end.y - w.start.y) < .08) doc.walls = doc.walls.filter((x) => x.id !== w.id);
    }
    if (type === 'draw-door') {
      const d = doc.doors.find((x) => x.id === action.id);
      if (d && Math.hypot(d.end.x - d.start.x, d.end.y - d.start.y) < .08) doc.doors = doc.doors.filter((x) => x.id !== d.id);
    }
    pointerAction = null; renderMap();
    if (type === 'set-robot') {
      const a = action.start, b = action.current || a;
      const drag = Math.hypot(b.x - a.x, b.y - a.y);
      const previousHeading = Number(localization?.pose?.heading || 0);
      const heading = drag > .08 ? Math.atan2(b.x - a.x, -(b.y - a.y)) : previousHeading;
      api('/api/localization/pose', {method: 'POST', body: JSON.stringify({x: round(a.x), y: round(a.y), heading})})
        .then((loc) => { localization = loc; updateLocalizationUi(); renderMap(); $('mappingGuide').textContent='Pozycja zapisana. Teraz kliknij „Rozpocznij zapis” i używaj pilota.'; toast('Pozycja robota skalibrowana na mapie.'); })
        .catch((err) => toast(`Kalibracja pozycji: ${err.message}`));
    }
  }
  svg.addEventListener('pointerup', finishMapPointer);
  svg.addEventListener('pointercancel', finishMapPointer);
  svg.addEventListener('wheel', (e) => { e.preventDefault(); zoom(e.deltaY < 0 ? .9 : 1.1); }, {passive: false});

  $$('[data-tool]').forEach((btn) => btn.addEventListener('click', () => setTool(btn.dataset.tool)));
  $('zoomIn').addEventListener('click', () => zoom(.82));
  $('zoomOut').addEventListener('click', () => zoom(1.22));
  $('fitMap').addEventListener('click', fitMap);
  $('deleteBtn').addEventListener('click', () => {
    if (!selected || !doc) return;
    if (selected.type === 'zone') doc.no_go_zones = doc.no_go_zones.filter((x) => x.id !== selected.id);
    if (selected.type === 'wall') doc.virtual_walls = doc.virtual_walls.filter((x) => x.id !== selected.id);
    if (selected.type === 'structure') doc.walls = doc.walls.filter((x) => x.id !== selected.id);
    if (selected.type === 'door') doc.doors = doc.doors.filter((x) => x.id !== selected.id);
    selected = null; markDirty(); renderMap();
  });
  $('zoneForm').addEventListener('submit', (e) => {
    e.preventDefault();
    if (selected?.type !== 'zone') return;
    const z = doc.no_go_zones.find((x) => x.id === selected.id); if (!z) return;
    const values = [Number($('zoneX').value), Number($('zoneY').value), Number($('zoneW').value), Number($('zoneH').value)];
    if (values.some((v) => !Number.isFinite(v)) || values[2] < .05 || values[3] < .05) return toast('Sprawdź wartości strefy.');
    z.name = ($('zoneName').value || 'Strefa zakazana').trim().slice(0, 80); [z.x, z.y, z.width, z.height] = values; markDirty(); renderMap();
  });

  async function loadMap(fit = false) {
    try { doc = await api('/api/map'); selected = null; markSaved(); renderMap(); if (fit) fitMap(); }
    catch (err) { toast(`Mapa: ${err.message}`); }
  }
  async function saveMap() {
    if ($('saveBtn').disabled) return false;
    $('saveBtn').disabled = true;
    try { $('saveState').textContent = 'zapisuję…'; doc = await api('/api/map', {method: 'PUT', body: JSON.stringify(doc)}); markSaved(); renderMap(); toast('Mapa zapisana lokalnie.'); return true; }
    catch (err) { $('saveState').textContent = 'błąd'; toast(`Zapis mapy: ${err.message}`); return false; }
    finally { $('saveBtn').disabled = false; }
  }
  $('saveBtn').addEventListener('click', saveMap);
  $('reloadBtn').addEventListener('click', () => loadMap(true));

  // ---------------- Live map localization ----------------
  function updateLocalizationUi() {
    if (!localization) return;
    const p = localization.pose;
    const confidence = clamp(Number(localization.confidence || 0), 0, 1);
    const source = localizationLabel(localization.source);
    if ($('mapPoseMode')) $('mapPoseMode').textContent = localization.calibrated ? source : 'NIEKALIBROWANA';
    if ($('mapCoordinates')) $('mapCoordinates').textContent = p && localization.calibrated ? `X ${p.x.toFixed(2)} · Y ${p.y.toFixed(2)} m` : '—';
    if ($('mapPoseConfidence')) $('mapPoseConfidence').textContent = localization.calibrated ? `${Math.round(confidence * 100)}%` : '0%';
    if ($('mapPositionSource')) $('mapPositionSource').textContent = source;
    if ($('localizationState')) $('localizationState').textContent = localization.note || '—';
    if ($('localizationBadge')) {
      const stale = localization.source === 'autonomous-last-known';
      $('localizationBadge').textContent = !localization.calibrated ? 'KALIBRUJ' : stale ? 'OSTATNIA ZNANA' : confidence > .78 ? 'PEWNA' : confidence > .38 ? 'SZACOWANA' : 'OSTATNIA ZNANA';
      $('localizationBadge').classList.toggle('live', !stale && localization.calibrated && confidence > .78);
    }
    if ($('linearSpeed') && document.activeElement !== $('linearSpeed')) $('linearSpeed').value = Math.round(Number(localization.linear_speed_mps || .10) * 1000) / 10;
    if ($('turnRate') && document.activeElement !== $('turnRate')) $('turnRate').value = Math.round(Number(localization.turn_rate_dps || 90) * 10) / 10;
  }

  let localizationBusy = false;
  async function refreshLocalization() {
    if (localizationBusy) return;
    localizationBusy = true;
    try {
      localization = await api('/api/localization');
      updateLocalizationUi();
      if (!pointerAction) renderMap();
    } catch (_) {} finally { localizationBusy = false; }
  }

  $('snapDockBtn')?.addEventListener('click', async () => {
    try {
      if ($('saveState')?.textContent !== 'zapisane' && !await saveMap()) return;
      localization = await api('/api/localization/dock', {method: 'POST', body: '{}'}); updateLocalizationUi(); renderMap(); toast('Robot ustawiony dokładnie w pozycji bazy.');
    } catch (err) { toast(`Pozycja: ${err.message}`); }
  });

  $('clearTrailBtn')?.addEventListener('click', async () => {
    try { localization = await api('/api/localization/trail/clear', {method: 'POST', body: '{}'}); updateLocalizationUi(); renderMap(); toast('Ślad robota wyczyszczony.'); }
    catch (err) { toast(`Ślad: ${err.message}`); }
  });

  $('localizationConfigForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      localization = await api('/api/localization/config', {method: 'PUT', body: JSON.stringify({linear_speed_cm_s: Number($('linearSpeed').value), turn_rate_deg_s: Number($('turnRate').value)})});
      updateLocalizationUi(); toast('Kalibracja ruchu zapisana.');
    } catch (err) { toast(`Kalibracja ruchu: ${err.message}`); }
  });

  $('roomplanImportBtn')?.addEventListener('click', () => $('roomplanFile')?.click());
  $('roomplanFile')?.addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      if (file.size > 5*1024*1024) throw new Error('Plik mapy może mieć najwyżej 5 MB.');
      const raw = JSON.parse(await file.text());
      const payload = raw.room || raw.capturedRoom || raw;
      if (!Array.isArray(payload.walls)) throw new Error('Plik nie zawiera tablicy walls. Eksportuj JSON zgodny z formatem RoomPlan/RoboMap.');
      doc = await api('/api/maps/roomplan', {method: 'POST', body: JSON.stringify({
        name: payload.name || file.name.replace(/\.json$/i, '') || 'Skan z iPhone',
        source: payload.source || 'apple-roomplan',
        device_name: payload.device_name || 'iPhone RoomPlan',
        walls: payload.walls || [], doors: payload.doors || [], windows: payload.windows || [], openings: payload.openings || [], furniture: payload.furniture || [],
      })});
      selected = null; markSaved(); renderMap(); fitMap(); toast('Geometria RoomPlan zaimportowana. Ustaw bazę i skalibruj robota.');
    } catch (err) { toast(`Import RoomPlan: ${err.message}`, 5200); }
    finally { e.target.value = ''; }
  });

  // ---------------- Navigation ----------------
  function setPage(page) {
    if(page === "rc"){ $("manualDetails").open=true; $("extraControls").open=true; }
    currentPage = page;
    document.body.dataset.page = page; window.scrollTo({top:0, behavior:"instant"});
    $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.page === page));
    $$('.rail-panel').forEach((p) => p.classList.toggle('active', p.dataset.panel === (page === 'rc' ? 'control' : page)));
    $('mapEditToolbar').classList.toggle('hidden', page !== 'map');
    if (page === 'map') setTool('select'); else setTool('pan');
    if (page === 'stats') loadStats();
    if (page === 'macros') loadMacros();
    if (page === 'automations') refreshPresence();
  }
  $$('.nav-btn').forEach((btn) => btn.addEventListener('click', () => setPage(btn.dataset.page)));
  $('configureTokenBtn')?.addEventListener('click', () => { setPage('settings'); setTimeout(() => $('cloudTokenCard')?.scrollIntoView({behavior:'smooth', block:'center'}), 80); });

  // ---------------- Basic robot commands ----------------
  $$('[data-robot]').forEach((btn) => btn.addEventListener('click', async () => {
    const action = btn.dataset.robot;
    btn.disabled = true;
    try { await api(`/api/robot/${action}`, {method: 'POST', body: '{}'}); toast(`Robot: ${stateLabel(action === 'start' ? 'cleaning' : action)}`); await refreshRobot(); }
    catch (err) { toast(`Robot: ${err.message}`); }
    finally { btn.disabled = !robotOnline(); }
  }));

  // RC transport: latest-wins instead of an unbounded Promise queue.
  // A slow/lost UDP reply can therefore delay at most one current command; stale
  // direction changes are discarded and STOP always gets the next slot.
  function manualRequest(direction, source = 'pilot') {
    return new Promise((resolve) => {
      const item = {direction, source, resolve};
      if (direction === 'stop') {
        if (manualPendingStop) manualPendingStop.resolve(false);
        manualPendingStop = item;
        if (manualPendingMove) { manualPendingMove.resolve(false); manualPendingMove = null; }
      } else {
        if (manualPendingMove) manualPendingMove.resolve(false);
        manualPendingMove = item;
      }
      pumpManualTransport();
    });
  }

  async function manualHttp(direction) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1550);
    try {
      await api(`/api/robot/manual/${direction}`, {method: 'POST', body: '{}', signal: controller.signal});
      return true;
    } finally {
      clearTimeout(timer);
    }
  }

  async function pumpManualTransport() {
    if (manualInFlight) return;
    manualInFlight = true;
    try {
      while (manualPendingStop || manualPendingMove) {
        const item = manualPendingStop || manualPendingMove;
        if (manualPendingStop) manualPendingStop = null; else manualPendingMove = null;
        try {
          const ok = await manualHttp(item.direction);
          item.resolve(ok);
        } catch (err) {
          item.resolve(false);
          if (err?.name !== 'AbortError') toast(`${item.source}: ${err.message}`);
        }
      }
    } finally {
      manualInFlight = false;
      if (manualPendingStop || manualPendingMove) queueMicrotask(pumpManualTransport);
    }
  }

  function sendManual(direction, source = 'pilot') {
    return manualRequest(direction, source);
  }

  async function renewManualLease(direction, source = 'Pilot') {
    if (!direction || direction === 'stop') return true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 650);
    try {
      await api(`/api/robot/manual/keepalive/${direction}`, {method: 'POST', body: '{}', signal: controller.signal});
      return true;
    } catch (_) {
      // The robot/backend may have missed the original transition. Retry one actual
      // direction write instead of continuously spamming MIoT as the old code did.
      if ((manualHeld === direction || gamepadLastDirection === direction) && robotOnline()) {
        sendManual(direction, source);
      }
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  async function stopManual() {
    manualHeld = null;
    $$('.pad-btn').forEach((b) => b.classList.remove('pressed'));
    if (robotOnline()) await sendManual('stop');
  }

  $$('[data-manual]').forEach((btn) => {
    const direction = btn.dataset.manual;
    if (direction === 'stop') {
      btn.addEventListener('click', () => sendManual('stop'));
      return;
    }
    btn.addEventListener('pointerdown', (e) => {
      if (!robotOnline()) return;
      e.preventDefault();
      btn.setPointerCapture?.(e.pointerId);
      ignoreManualClickUntil = Date.now() + 500;
      manualHeld = direction; btn.classList.add('pressed'); sendManual(direction);
    });
    const release = () => { if (manualHeld === direction) stopManual(); };
    btn.addEventListener('pointerup', release);
    btn.addEventListener('pointercancel', release);
    btn.addEventListener('lostpointercapture', release);
    btn.addEventListener('click', () => {
      if (Date.now() < ignoreManualClickUntil || !robotOnline()) return;
      // Accessible keyboard/click fallback: short pulse.
      sendManual(direction); setTimeout(() => sendManual('stop'), 350);
    });
  });

  $$('[data-suction]').forEach((btn) => btn.addEventListener('click', async () => {
    const level = Number(btn.dataset.suction);
    try {
      await api(`/api/robot/suction/${level}`, {method: 'POST', body: '{}'});
      $$('[data-suction]').forEach((b) => b.classList.toggle('active', b === btn));
      const label = ['Cicha', 'Standard', 'Mocna'][level] || '—'; $('suctionLabel').textContent = label; toast(`Moc: ${label}`);
    } catch (err) { toast(`Moc ssania: ${err.message}`); }
  }));

  async function emergencyStop() {
    try { await api('/api/robot/emergency-stop', {method: 'POST', body: '{}'}); pulseGamepad(180, .8); toast('AWARYJNY STOP wysłany.'); }
    catch (err) { toast(`STOP: ${err.message}`); }
  }
  $('emergencyStop').addEventListener('click', emergencyStop);

  // ---------------- Gamepad ----------------
  function pulseGamepad(duration = 80, strong = .25) {
    if (!$('gamepadEnabled').checked) return;
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    for (const pad of pads) {
      if (!pad) continue;
      const actuator = pad.vibrationActuator || pad.hapticActuators?.[0];
      try {
        if (actuator?.playEffect) actuator.playEffect('dual-rumble', {duration, strongMagnitude: strong, weakMagnitude: Math.min(1, strong * .65)});
        else if (actuator?.pulse) actuator.pulse(strong, duration);
      } catch (_) {}
    }
  }

  function activeGamepad() {
    if (!navigator.getGamepads) return null;
    const pads = navigator.getGamepads();
    if (activeGamepadIndex != null && pads[activeGamepadIndex]) return pads[activeGamepadIndex];
    const pad = Array.from(pads).find(Boolean) || null;
    activeGamepadIndex = pad?.index ?? null;
    return pad;
  }

  function gamepadDirection(pad, current = 'stop') {
    const up = pad.buttons[12]?.pressed, down = pad.buttons[13]?.pressed, left = pad.buttons[14]?.pressed, right = pad.buttons[15]?.pressed;
    if (up) return 'forward'; if (down) return 'back'; if (left) return 'left'; if (right) return 'right';
    const x = Number(pad.axes[0] || 0), y = Number(pad.axes[1] || 0);
    const magnitude = Math.max(Math.abs(x), Math.abs(y));
    // Hysteresis: entering movement needs a stronger deflection than staying in it.
    // This prevents worn/noisy sticks from rapidly toggling stop/left/right near zero.
    const threshold = current === 'stop' ? .48 : .32;
    if (magnitude < threshold) return 'stop';
    const ax = Math.abs(x), ay = Math.abs(y), margin = .10;
    if ((current === 'forward' || current === 'back') && ay + margin >= ax) return y < 0 ? 'forward' : 'back';
    if ((current === 'left' || current === 'right') && ax + margin >= ay) return x < 0 ? 'left' : 'right';
    if (ay >= ax) return y < 0 ? 'forward' : 'back';
    return x < 0 ? 'left' : 'right';
  }

  function edgePressed(pad, idx) {
    const pressed = !!pad.buttons[idx]?.pressed;
    const was = !!gamepadLastButtons[idx];
    gamepadLastButtons[idx] = pressed;
    return pressed && !was;
  }

  async function ensureGamepadRcSession() {
    if (gamepadRcActive) return true;
    if (gamepadRcStarting) return gamepadRcStarting;
    const generation = gamepadRcGeneration;
    gamepadRcStarting = (async () => {
      try {
        await api('/api/robot/rc/begin', {method: 'POST', body: '{}'});
        if (generation !== gamepadRcGeneration || !$('gamepadEnabled').checked) {
          api('/api/robot/rc/end', {method: 'POST', body: '{}'}).catch(() => {});
          return false;
        }
        gamepadRcActive = true;
        if ($('gamepadRcState')) $('gamepadRcState').textContent = 'RC CLEAN • szczotki ON • DND 24h podczas jazdy';
        return true;
      } catch (err) {
        toast(`RC Clean: ${err.message}`);
        if ($('gamepadRcState')) $('gamepadRcState').textContent = 'Nie udało się uruchomić szczotek';
        return false;
      } finally {
        if (generation === gamepadRcGeneration) gamepadRcStarting = null;
      }
    })();
    return gamepadRcStarting;
  }

  async function endGamepadRcSession(source = 'Gamepad') {
    gamepadRcGeneration += 1;
    gamepadRcActive = false;
    gamepadRcStarting = null;
    if ($('gamepadRcState')) $('gamepadRcState').textContent = 'Gotowy • gałka uruchamia szczotki automatycznie';
    if (!robotOnline()) return;
    try { await api('/api/robot/rc/end', {method: 'POST', body: '{}'}); }
    catch (err) { if (source !== 'beforeunload') toast(`${source}: ${err.message}`); }
  }

  async function issueGamepadDirection(direction) {
    if (gamepadIssueBusy) return;
    gamepadIssueBusy = true;
    gamepadLastAttemptAt = performance.now();
    try {
      if (direction !== 'stop') {
        const ready = await ensureGamepadRcSession();
        if (!ready || gamepadLastDirection !== direction) return;
      }
      const ok = await sendManual(direction, 'Gamepad');
      if (ok && gamepadLastDirection === direction) gamepadAckDirection = direction;
      if (!ok && direction === 'stop') gamepadAckDirection = 'stop';
    } finally {
      gamepadIssueBusy = false;
    }
  }

  function gamepadFrame() {
    const enabled = $('gamepadEnabled').checked;
    const pad = activeGamepad();
    $('gamepadState').textContent = !enabled ? 'Nieaktywny' : pad ? pad.id.slice(0, 28) : 'Czekam na pad…';
    if (enabled && pad && robotOnline()) {
      const now = performance.now();
      let raw = gamepadDirection(pad, gamepadLastDirection);
      // When moving the stick from one sector to another it often crosses the centre for
      // a few frames. Sending STOP there creates an extra MIoT write (and on E5 an extra
      // confirmation beep). Keep the previous direction for a short 140 ms neutral grace.
      if (raw === 'stop' && gamepadLastDirection !== 'stop') {
        if (!gamepadNeutralSince) gamepadNeutralSince = now;
        if (now - gamepadNeutralSince < 140) raw = gamepadLastDirection;
      } else {
        gamepadNeutralSince = 0;
      }
      if (raw !== gamepadCandidateDirection) {
        gamepadCandidateDirection = raw;
        gamepadCandidateSince = now;
      }
      // Direction changes must be stable for 90 ms. This deliberately trades a tiny bit
      // of twitch response for fewer direction writes/beeps and much smoother steering.
      const stable = raw === 'stop' || now - gamepadCandidateSince >= 90;
      if (stable && raw !== gamepadLastDirection) {
        gamepadLastDirection = raw;
        issueGamepadDirection(raw);
      } else if (gamepadAckDirection !== gamepadLastDirection && now - gamepadLastAttemptAt > 320) {
        // If the transition packet was lost/timeouted, retry only the current desired
        // direction. Old intermediate directions never build up in a queue.
        issueGamepadDirection(gamepadLastDirection);
      }
      if (edgePressed(pad, 1)) emergencyStop();
      if (edgePressed(pad, 9)) api('/api/robot/start', {method: 'POST', body: '{}'}).then(refreshRobot).catch((e) => toast(`Gamepad: ${e.message}`));
      if (edgePressed(pad, 8)) api('/api/robot/dock', {method: 'POST', body: '{}'}).then(refreshRobot).catch((e) => toast(`Gamepad: ${e.message}`));
    } else if (gamepadLastDirection !== 'stop') {
      gamepadLastDirection = 'stop';
      gamepadCandidateDirection = 'stop';
      issueGamepadDirection('stop');
    }
    gamepadLoopId = requestAnimationFrame(gamepadFrame);
  }
  window.addEventListener('gamepadconnected', (e) => {
    if (activeGamepadIndex == null) activeGamepadIndex = e.gamepad.index;
    $('gamepadState').textContent = e.gamepad.id.slice(0, 28);
    toast('Gamepad wykryty.');
  });
  window.addEventListener('gamepaddisconnected', (e) => {
    if (activeGamepadIndex === e.gamepad.index) activeGamepadIndex = null;
    if (gamepadLastDirection !== 'stop' && robotOnline()) issueGamepadDirection('stop');
    endGamepadRcSession('Pad odłączony');
    gamepadLastDirection = gamepadAckDirection = gamepadCandidateDirection = 'stop'; gamepadNeutralSince = 0;
    $('gamepadState').textContent = 'Pad odłączony';
  });
  $('gamepadEnabled').addEventListener('change', () => {
    gamepadLastButtons = {};
    if (!$('gamepadEnabled').checked && robotOnline()) { issueGamepadDirection('stop'); endGamepadRcSession('Gamepad'); }
    gamepadLastDirection = gamepadAckDirection = gamepadCandidateDirection = 'stop'; gamepadNeutralSince = 0;
  });
  window.addEventListener('blur', () => {
    if ((manualHeld || gamepadLastDirection !== 'stop') && robotOnline()) sendManual('stop', 'Bezpieczeństwo');
    if (gamepadRcActive) endGamepadRcSession('Bezpieczeństwo');
    manualHeld = null; gamepadLastDirection = gamepadAckDirection = gamepadCandidateDirection = 'stop'; gamepadNeutralSince = 0;
    $$('.pad-btn').forEach((b) => b.classList.remove('pressed'));
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && (manualHeld || gamepadLastDirection !== 'stop') && robotOnline()) {
      sendManual('stop', 'Bezpieczeństwo');
      if (gamepadRcActive) endGamepadRcSession('Bezpieczeństwo');
      manualHeld = null; gamepadLastDirection = gamepadAckDirection = gamepadCandidateDirection = 'stop'; gamepadNeutralSince = 0;
      $$('.pad-btn').forEach((b) => b.classList.remove('pressed'));
    }
  });

  // ---------------- Macros ----------------
  function formatMacroTime(ms) {
    const sec = Math.max(0, ms) / 1000;
    const m = Math.floor(sec / 60); const s = sec - m * 60;
    return `${String(m).padStart(2, '0')}:${s.toFixed(1).padStart(4, '0')}`;
  }

  async function loadMacros() {
    try {
      const [items, state] = await Promise.all([api('/api/macros'), api('/api/macros/state')]);
      renderMacros(items); updateMacroState(state);
    } catch (err) { toast(`Makra: ${err.message}`); }
  }

  function renderMacros(items) {
    const list = $('macroList'); list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement('div'); empty.className = 'empty-state'; empty.textContent = 'Nie masz jeszcze zapisanych makr.'; list.append(empty); return;
    }
    items.slice().reverse().forEach((m) => {
      const row = document.createElement('div'); row.className = 'macro-item';
      const info = document.createElement('div');
      const strong = document.createElement('strong'); strong.textContent = m.name;
      const meta = document.createElement('span'); meta.textContent = `${formatMacroTime(m.duration_ms)} • ${m.steps.length} komend`;
      info.append(strong, meta);
      const actions = document.createElement('div'); actions.className = 'macro-item-actions';
      const play = document.createElement('button'); play.type = 'button'; play.textContent = '▶'; play.title = 'Odtwórz makro';
      play.addEventListener('click', async () => { try { await api(`/api/macros/${m.id}/play`, {method: 'POST', body: '{}'}); toast(`Odtwarzam: ${m.name}`); } catch (e) { toast(`Makro: ${e.message}`); } });
      const del = document.createElement('button'); del.type = 'button'; del.textContent = '🗑'; del.title = 'Usuń makro';
      del.addEventListener('click', async () => { if (!confirm(`Usunąć makro „${m.name}”?`)) return; try { await api(`/api/macros/${m.id}`, {method: 'DELETE'}); loadMacros(); } catch (e) { toast(e.message); } });
      actions.append(play, del); row.append(info, actions); list.append(row);
    });
  }

  function updateMacroState(state) {
    const rec = state.recording || {};
    const recording = !!rec.recording;
    $('macroRecordStart').disabled = recording;
    $('macroRecordStop').disabled = !recording;
    $('macroRecordCancel').disabled = !recording;
    $('macroRecordBadge').textContent = recording ? '● NAGRYWANIE' : state.playback?.playing ? '▶ ODTWARZANIE' : 'Gotowe';
    $('macroRecordBadge').classList.toggle('recording', recording);
    $('macroRecordBadge').classList.toggle('live', !!state.playback?.playing);
    $('macroStepCount').textContent = `${rec.step_count || 0} komend`;
    $('macroTimer').textContent = formatMacroTime(rec.elapsed_ms || 0);
    macroRecordingStartedLocal = recording ? Date.now() - (rec.elapsed_ms || 0) : null;
  }

  $('macroRecordStart').addEventListener('click', async () => {
    try { const r = await api('/api/macros/record/start', {method: 'POST', body: JSON.stringify({name: $('macroName').value.trim() || 'Nowe makro'})}); updateMacroState({recording: r, playback: {playing: false}}); toast('Nagrywanie makra rozpoczęte. Steruj robotem.'); }
    catch (err) { toast(`Nagrywanie: ${err.message}`); }
  });
  $('macroRecordStop').addEventListener('click', async () => {
    try { const r = await api('/api/macros/record/stop', {method: 'POST', body: '{}'}); toast(r.saved ? 'Makro zapisane.' : 'Makro było puste — nie zapisano.'); await loadMacros(); }
    catch (err) { toast(`Makro: ${err.message}`); }
  });
  $('macroRecordCancel').addEventListener('click', async () => { try { await api('/api/macros/record/cancel', {method: 'POST', body: '{}'}); await loadMacros(); toast('Nagrywanie anulowane.'); } catch (e) { toast(e.message); } });
  $('stopMacroPlayback').addEventListener('click', async () => { try { await api('/api/macros/playback/stop', {method: 'POST', body: '{}'}); await loadMacros(); toast('Makro zatrzymane.'); } catch (e) { toast(e.message); } });

  // ---------------- Automations ----------------
  async function loadAutomations() {
    try {
      const a = await api('/api/automations');
      $('lockEnabled').checked = a.lock_enabled; $('lockDelay').value = a.lock_delay_seconds; $('minBattery').value = a.min_battery; $('dockOnUnlock').checked = a.dock_on_unlock;
      $('quietEnabled').checked = a.quiet_enabled; $('quietStart').value = a.quiet_start; $('quietEnd').value = a.quiet_end; $('quietForce').checked = a.quiet_force_suction; $('quietBlockLock').checked = a.quiet_block_lock_automation;
    } catch (e) { toast(`Automatyzacje: ${e.message}`); }
  }
  $('automationForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      lock_enabled: $('lockEnabled').checked,
      lock_delay_seconds: clamp(Number($('lockDelay').value) || 120, 5, 3600),
      dock_on_unlock: $('dockOnUnlock').checked,
      min_battery: clamp(Number($('minBattery').value) || 0, 0, 100),
      quiet_enabled: $('quietEnabled').checked,
      quiet_start: $('quietStart').value || '22:00', quiet_end: $('quietEnd').value || '07:00',
      quiet_force_suction: $('quietForce').checked, quiet_block_lock_automation: $('quietBlockLock').checked,
    };
    try { await api('/api/automations', {method: 'PUT', body: JSON.stringify(payload)}); toast('Automatyzacje zapisane lokalnie.'); refreshPresence(); }
    catch (err) { toast(`Automatyzacje: ${err.message}`); }
  });

  async function refreshPresence() {
    try {
      const p = await api('/api/system/presence');
      $('presenceState').textContent = !p.lock_detection_supported ? 'Tylko Windows' : p.locked == null ? 'Nieznany' : p.locked ? `🔒 zablokowany ${p.locked_for_seconds}s` : '🔓 odblokowany';
      $('quietState').textContent = p.quiet_active ? 'Aktywny' : 'Nieaktywny';
      $('quietBadge').classList.toggle('hidden', !p.quiet_active);
    } catch (_) {}
  }

  // ---------------- Statistics / blackbox ----------------
  async function loadStats() {
    try {
      const [stats, history] = await Promise.all([api('/api/stats?days=30'), api('/api/history?limit=80')]);
      $('statRuns').textContent = stats.cleaning_runs;
      $('statMinutes').textContent = stats.cleaning_minutes >= 60 ? `${(stats.cleaning_minutes / 60).toFixed(1)} h` : `${stats.cleaning_minutes} min`;
      $('statCommands').textContent = stats.command_count; $('statErrors').textContent = stats.error_count;
      renderWeekChart(stats.last7 || []); renderHistory(history.events || []);
    } catch (err) { toast(`Statystyki: ${err.message}`); }
  }

  function renderWeekChart(days) {
    const chart = $('weekChart'); chart.replaceChildren();
    const max = Math.max(1, ...days.map((d) => Number(d.minutes) || 0));
    const fmt = new Intl.DateTimeFormat('pl-PL', {weekday: 'short'});
    days.forEach((d) => {
      const box = document.createElement('div'); box.className = 'day-bar'; box.title = `${d.date}: ${d.minutes} min`;
      const track = document.createElement('div'); track.className = 'day-bar-track';
      const fill = document.createElement('div'); fill.className = 'day-bar-fill'; fill.style.height = `${Math.max(2, (Number(d.minutes) || 0) / max * 100)}%`; track.append(fill);
      const label = document.createElement('div'); label.className = 'day-bar-label';
      const date = new Date(`${d.date}T12:00:00`); label.textContent = fmt.format(date).replace('.', '');
      box.append(track, label); chart.append(box);
    });
  }

  function renderHistory(events) {
    const list = $('historyList'); list.replaceChildren();
    if (!events.length) { const e = document.createElement('div'); e.className = 'empty-state'; e.textContent = 'Dziennik jest jeszcze pusty.'; list.append(e); return; }
    events.forEach((event) => {
      const row = document.createElement('div'); row.className = 'history-row';
      const time = document.createElement('time'); const dt = new Date(event.ts); time.textContent = Number.isNaN(dt.getTime()) ? '—' : dt.toLocaleString('pl-PL', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
      const info = document.createElement('div'); const strong = document.createElement('strong'); strong.textContent = `${event.category} • ${event.action}`;
      const span = document.createElement('span'); span.textContent = [event.source, event.state, event.battery == null ? null : `${event.battery}%`, event.detail].filter(Boolean).join(' • ');
      info.append(strong, span); row.append(time, info); list.append(row);
    });
  }
  $('refreshStats').addEventListener('click', loadStats);

  // ---------------- Connection settings ----------------
  function setSetupStep(id, mode, text) {
    const el = $(id); if (!el) return;
    el.classList.remove('done', 'active', 'warn');
    if (mode) el.classList.add(mode);
    const textEl = $(`${id}Text`); if (textEl && text) textEl.textContent = text;
  }

  function renderSetupProgress({found = false, tokenReady = false, tokenHidden = false, autoTokenState = ''} = {}) {
    setSetupStep('stepLan', found ? 'done' : 'active', found ? 'E5 znaleziony' : 'Wykrywanie w LAN');
    if (tokenReady) setSetupStep('stepToken', 'done', 'Token zapisany');
    else if (tokenHidden || autoTokenState === 'authorization-required') setSetupStep('stepToken', 'warn', 'Wymaga autoryzacji');
    else if (autoTokenState === 'fetching' || autoTokenState === 'queued') setSetupStep('stepToken', 'active', 'Pobieranie automatyczne');
    else setSetupStep('stepToken', found ? 'active' : '', found ? 'Szukam tokenu' : 'Oczekiwanie');
    setSetupStep('stepReady', tokenReady ? 'done' : '', tokenReady ? 'Sterowanie LAN gotowe' : 'Czeka na token');
    const badge = $('setupOverallBadge'); const hero = $('setupHeroState');
    if (tokenReady) { badge.textContent = 'GOTOWE'; badge.className = 'state-badge live'; hero.textContent = 'ONLINE'; }
    else if (found) { badge.textContent = 'E5 WYKRYTY'; badge.className = 'state-badge'; hero.textContent = tokenHidden ? 'TOKEN' : 'ŁĄCZĘ'; }
    else { badge.textContent = 'AUTO'; badge.className = 'state-badge'; hero.textContent = 'SZUKAM'; }
  }

  function renderAutoTokenState(result) {
    if (!result) return;
    const badge = $('cloudTokenBadge'); const state = $('cloudTokenState'); const orb = $('autoTokenOrb');
    const headline = $('autoTokenHeadline'); const cloudCard = $('cloudTokenCard');
    const mode = result.state || 'idle';
    badge.classList.remove('ok', 'warn', 'searching');
    orb.classList.remove('ready', 'waiting', 'working', 'error');
    if (result.token_ready || mode === 'ready') {
      badge.textContent = 'TOKEN OK'; badge.classList.add('ok'); orb.classList.add('ready'); orb.textContent = '✓';
      headline.textContent = 'Token miIO gotowy';
      state.textContent = result.detail || 'Token jest zapisany lokalnie.';
      cloudCard.classList.remove('attention');
    } else if (mode === 'fetching' || mode === 'queued') {
      badge.textContent = 'POBIERAM'; badge.classList.add('searching'); orb.classList.add('working'); orb.textContent = '↻';
      headline.textContent = 'AutoToken pracuje'; state.textContent = result.detail || 'Pobieram token automatycznie…';
      cloudCard.classList.add('attention');
    } else if (mode === 'authorization-required') {
      badge.textContent = '1× LOGOWANIE'; badge.classList.add('warn'); orb.classList.add('waiting'); orb.textContent = '🔑';
      headline.textContent = 'E5 ukrywa token — połącz konto Xiaomi raz';
      state.textContent = result.detail || 'Po jednorazowym uwierzytelnieniu RoboMap pobierze token sam.';
      cloudCard.classList.add('attention');
    } else if (mode === 'error') {
      badge.textContent = 'BŁĄD'; badge.classList.add('warn'); orb.classList.add('error'); orb.textContent = '!';
      headline.textContent = 'AutoToken wymaga uwagi'; state.textContent = result.detail || 'Nie udało się pobrać tokenu.';
      cloudCard.classList.add('attention');
    } else {
      badge.textContent = 'AUTO'; orb.classList.add('waiting'); orb.textContent = '🔐';
      headline.textContent = 'AutoToken czeka'; state.textContent = result.detail || 'Czekam na wykrycie E5.';
    }
    if ($('cloudTokenForgetBtn')) $('cloudTokenForgetBtn').disabled = !result.credential_saved || !cloudTokenAllowedHere;
    if ($('cloudTokenRetryBtn')) $('cloudTokenRetryBtn').disabled = !result.credential_saved || !cloudTokenAllowedHere || !!result.token_ready;
    if (result.credential_saved && result.username_hint) {
      $('xiaomiUsername').placeholder = `${result.username_hint} • zapisane w Windows DPAPI`;
    }
    renderSetupProgress({
      found: !!(result.robot_ip || ($('robotIp') && $('robotIp').value)),
      tokenReady: !!result.token_ready,
      tokenHidden: mode === 'authorization-required',
      autoTokenState: mode,
    });
  }

  function renderDiscovery(result) {
    const state = $('autoDiscoverState'); const badge = $('autoDiscoveryBadge'); const list = $('autoDiscoverCandidates');
    if (!state || !badge || !list) return;
    state.textContent = result?.detail || 'Brak danych ze skanowania.';
    badge.classList.remove('ok', 'searching', 'warn');
    if (result?.exact_model && result?.token_ready) { badge.textContent = 'E5 + TOKEN'; badge.classList.add('ok'); }
    else if (result?.token_hidden) { badge.textContent = 'E5 WYKRYTY'; badge.classList.add('warn'); }
    else if (result?.found) { badge.textContent = 'WYKRYTO'; badge.classList.add('warn'); }
    else { badge.textContent = 'LAN'; }
    const cloudCard = $('cloudTokenCard');
    if (cloudCard) cloudCard.classList.toggle('attention', !!(result?.exact_model && !result?.token_ready));
    list.textContent = '';
    (result?.candidates || []).slice(0, 6).forEach((item) => {
      const row = document.createElement('div'); row.className = 'candidate-row';
      const left = document.createElement('div'); const strong = document.createElement('strong'); const meta = document.createElement('span');
      strong.textContent = item.model || 'Urządzenie MiIO';
      meta.textContent = [item.ip, item.device_id ? `ID ${item.device_id}` : '', item.token_available ? 'token dostępny' : (result?.token_hidden && item.exact_model ? 'sparowany • token chroniony' : 'token niewidoczny')].filter(Boolean).join(' • ');
      left.append(strong, meta);
      const tag = document.createElement('span'); tag.className = 'candidate-tag'; tag.textContent = item.exact_model ? 'E5' : 'LAN';
      row.append(left, tag); list.append(row);
    });
    renderSetupProgress({found: !!result?.exact_model, tokenReady: !!result?.token_ready, tokenHidden: !!result?.token_hidden, autoTokenState: result?.cloud_autotoken?.state || ''});
    if (result?.cloud_autotoken) renderAutoTokenState(result.cloud_autotoken);
  }

  async function apiFirst(paths, options = {}) {
    let lastError = null;
    for (const path of paths) {
      try { return await api(path, options); }
      catch (err) {
        lastError = err;
        if (!['404', '405', 'Not Found', 'Method Not Allowed'].includes(String(err.message))) break;
      }
    }
    throw lastError || new Error('Brak zgodnego endpointu AutoPair');
  }

  async function loadAutoTokenState() {
    if (!cloudTokenAllowedHere) return;
    try { renderAutoTokenState(await api('/api/connection/autotoken/status')); } catch (_) {}
  }

  async function loadDiscoveryState() {
    try { renderDiscovery(await apiFirst(['/api/autopair', '/api/connection/autodiscover', '/api/discovery'])); } catch (_) {}
    await loadAutoTokenState();
  }

  async function runAutoDiscover({quiet = false} = {}) {
    const btn = $('autoDiscoverBtn'); const badge = $('autoDiscoveryBadge');
    if (btn) btn.disabled = true;
    if (badge) { badge.textContent = 'SZUKAM…'; badge.classList.remove('ok', 'warn'); badge.classList.add('searching'); }
    $('autoDiscoverState').textContent = 'Szukam Xiaomi E5 wyłącznie w bieżącej sieci lokalnej…';
    try {
      const stamp = Date.now();
      const result = await apiFirst([`/api/autopair/run?_=${stamp}`, `/api/connection/autodiscover?run=1&_=${stamp}`, `/api/discovery/run?_=${stamp}`]);
      renderDiscovery(result);
      await loadConnection();
      await loadAutoTokenState();
      await refreshRobot();
      if (!quiet) toast(result.token_ready ? 'E5 i token skonfigurowane automatycznie.' : (result.exact_model ? 'E5 znaleziony. AutoToken przejmuje następny krok.' : result.detail));
      return result;
    } catch (err) {
      $('autoDiscoverState').textContent = `Błąd skanowania: ${err.message}`;
      if (!quiet) toast(`Wykrywanie: ${err.message}`);
      return null;
    } finally { if (btn) btn.disabled = false; }
  }

  async function loadConnection() {
    try {
      const cfg = await api('/api/connection');
      $('robotIp').value = cfg.robot_ip || ''; $('robotToken').value = '';
      $('autoDiscoveryEnabled').checked = cfg.auto_discovery !== false;
      $('connectionState').textContent = cfg.token_set ? `Gotowe • ${cfg.robot_ip || 'E5'} • token zapisany lokalnie` : (cfg.robot_ip ? `E5 wykryty: ${cfg.robot_ip} • czekam na AutoToken` : 'AutoPair wyszuka E5 automatycznie.');
      renderSetupProgress({found: !!cfg.robot_ip, tokenReady: !!cfg.token_set, tokenHidden: !!cfg.robot_ip && !cfg.token_set});
      return cfg;
    } catch (err) { $('connectionState').textContent = `Błąd: ${err.message}`; return null; }
  }

  const localHostnames = new Set(['127.0.0.1', 'localhost', '::1', '[::1]']);
  const cloudTokenAllowedHere = localHostnames.has(location.hostname);
  if (!cloudTokenAllowedHere) {
    $('cloudTokenBtn').disabled = true;
    $('cloudTokenRetryBtn').disabled = true;
    $('cloudTokenForgetBtn').disabled = true;
    $('cloudTokenState').textContent = 'AutoToken konta Xiaomi jest dostępny tylko w Launcherze / 127.0.0.1. Z telefonu możesz sterować robotem po zakończeniu konfiguracji.';
  }

  $('cloudTokenForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!cloudTokenAllowedHere) return toast('AutoToken konta Xiaomi działa tylko lokalnie na tym komputerze.');
    const username = $('xiaomiUsername').value.trim();
    const password = $('xiaomiPassword').value;
    const region = $('xiaomiRegion').value;
    const remember = $('xiaomiRemember').checked;
    if (!username || !password) return toast('Podaj login i hasło do własnego konta Xiaomi — tylko ten jeden raz.');
    $('cloudTokenBtn').disabled = true;
    renderAutoTokenState({state:'fetching', detail:'Łączę konto Xiaomi, dopasowuję wykryty E5 i zasysam jego token miIO…', token_ready:false});
    try {
      const result = await api('/api/connection/autotoken/xiaomi', {method: 'POST', body: JSON.stringify({username, password, region, remember})});
      $('xiaomiPassword').value = '';
      renderAutoTokenState({state:'ready', token_ready:true, credential_saved:!!result.credential_saved, detail: result.detail || 'Token zapisany.'});
      await loadConnection(); await loadDiscoveryState(); await refreshRobot();
      toast('Gotowe: token E5 został pobrany i zapisany automatycznie.');
    } catch (err) {
      $('xiaomiPassword').value = '';
      renderAutoTokenState({state:'error', token_ready:false, detail:`Nie udało się pobrać tokenu: ${err.message}`});
      toast(`AutoToken: ${err.message}`, 4800);
    } finally { $('cloudTokenBtn').disabled = !cloudTokenAllowedHere; }
  });

  $('cloudTokenRetryBtn').addEventListener('click', async () => {
    if (!cloudTokenAllowedHere) return;
    $('cloudTokenRetryBtn').disabled = true;
    renderAutoTokenState({state:'fetching', token_ready:false, credential_saved:true, detail:'Ponawiam automatyczne pobieranie tokenu…'});
    try { renderAutoTokenState(await api('/api/connection/autotoken/run', {method:'POST', body:'{}'})); await loadConnection(); await refreshRobot(); }
    catch (err) { renderAutoTokenState({state:'error', token_ready:false, credential_saved:true, detail:err.message}); }
    finally { await loadAutoTokenState(); }
  });

  $('cloudTokenForgetBtn').addEventListener('click', async () => {
    if (!cloudTokenAllowedHere || !confirm('Usunąć zapisaną, zaszyfrowaną autoryzację Xiaomi z tego komputera? Sam token E5 pozostanie zapisany.')) return;
    try { renderAutoTokenState(await api('/api/connection/autotoken/credentials', {method:'DELETE'})); $('xiaomiRemember').checked = false; toast('Autoryzacja Xiaomi usunięta. Token E5 pozostał lokalnie.'); }
    catch (err) { toast(`Nie udało się usunąć autoryzacji: ${err.message}`); }
  });

  $('connectionForm').addEventListener('submit', async (e) => {
    e.preventDefault(); const ip = $('robotIp').value.trim(); const token = $('robotToken').value.trim();
    if (!ip && !$('autoDiscoveryEnabled').checked) return toast('Wpisz IP robota albo włącz automatyczne wykrywanie.');
    if (token && !/^[0-9a-fA-F]{32}$/.test(token)) return toast('Token miIO powinien mieć 32 znaki HEX.');
    try { await api('/api/connection', {method: 'PUT', body: JSON.stringify({robot_ip: ip, token: token || null, auto_discovery: $('autoDiscoveryEnabled').checked})}); $('robotToken').value = ''; await loadConnection(); await refreshRobot(); toast('Połączenie zapisane.'); }
    catch (err) { toast(`Połączenie: ${err.message}`); }
  });
  $('autoDiscoveryEnabled').addEventListener('change', async () => {
    try {
      await api('/api/connection', {method: 'PUT', body: JSON.stringify({robot_ip: $('robotIp').value.trim(), token: null, auto_discovery: $('autoDiscoveryEnabled').checked})});
      toast($('autoDiscoveryEnabled').checked ? 'Badawcza łatka AutoPair włączona.' : 'Badawcza łatka AutoPair wyłączona.');
      if ($('autoDiscoveryEnabled').checked) runAutoDiscover({quiet: true});
    } catch (err) { toast(`Ustawienia: ${err.message}`); }
  });
  $('autoDiscoverBtn').addEventListener('click', () => runAutoDiscover());
  $('testConnectionBtn').addEventListener('click', async () => {
    $('testConnectionBtn').disabled = true; $('connectionState').textContent = 'Łączę bezpośrednio z E5…';
    try { const r = await api('/api/connection/test', {method: 'POST', body: '{}'}); $('connectionState').textContent = r.detail || 'Robot odpowiada.'; toast('Połączenie działa.'); await refreshRobot(); }
    catch (err) { $('connectionState').textContent = `Błąd: ${err.message}`; toast(`Robot nie odpowiada: ${err.message}`); }
    finally { $('testConnectionBtn').disabled = false; }
  });
  $('toggleToken').addEventListener('click', () => { $('robotToken').type = $('robotToken').type === 'password' ? 'text' : 'password'; });
  async function loadNetworkInfo() {
    try { const n = await api('/api/network'); $('currentUrl').textContent = n.lan_url || location.href; }
    catch (_) { $('currentUrl').textContent = location.href; }
  }

  // ---------------- Timers / startup ----------------
  function updateLocalMacroTimer() {
    if (macroRecordingStartedLocal) $('macroTimer').textContent = formatMacroTime(Date.now() - macroRecordingStartedLocal);
  }

  async function pollMacroState() {
    try { const state = await api('/api/macros/state'); updateMacroState(state); }
    catch (_) {}
  }

  async function init() {
    connectLive();
    pollSurvey();setInterval(pollSurvey,1500);
    setConnectionUi('connecting');
    setPage('control');
    setTool('pan');
    gamepadLoopId = requestAnimationFrame(gamepadFrame);
    const startup = await Promise.allSettled([loadMap(true), loadConnection(), loadAutomations(), refreshRobot(), refreshPresence(), loadMacros(), loadDiscoveryState(), loadNetworkInfo(), refreshLocalization()]);
    $('loadingOverlay').classList.add('done');
    const cfg = startup[1]?.status === 'fulfilled' ? startup[1].value : null;
    if (cfg?.auto_discovery !== false) setTimeout(() => runAutoDiscover({quiet: true}), 900);
    // RC heartbeat only renews a server-side lease; it no longer writes the same
    // MIoT direction every 450 ms. This removes the command backlog that could make
    // a released stick look "stuck" for several seconds on packet loss.
    setInterval(async () => {
      const dir = manualHeld || (gamepadLastDirection !== 'stop' ? gamepadLastDirection : null);
      if (!dir || document.hidden || gamepadKeepaliveBusy) return;
      gamepadKeepaliveBusy = true;
      try {
        const ok = await renewManualLease(dir, manualHeld ? 'Pilot' : 'Gamepad');
        if (ok && !manualHeld && gamepadLastDirection === dir) gamepadAckDirection = dir;
      } finally {
        gamepadKeepaliveBusy = false;
      }
    }, 220);
    setInterval(()=>{if(!liveHealthy())refreshRobot();}, 2500);
    // Pure local endpoint: keeps the marker fluid without adding MIoT traffic to E5.
    setInterval(()=>{if(!liveHealthy())refreshLocalization();}, 1000);
    setInterval(loadDiscoveryState, 10000);
    setInterval(refreshPresence, 2500);
    setInterval(pollMacroState, 1000);
    setInterval(updateLocalMacroTimer, 100);
    if ('serviceWorker' in navigator) navigator.serviceWorker.getRegistrations().then((regs) => Promise.all(regs.map((r) => r.unregister()))).catch(() => {}); if ('caches' in window) caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith('robomap-')).map((k) => caches.delete(k)))).catch(() => {});
  }

  window.addEventListener('beforeunload', () => { if ((manualHeld || gamepadLastDirection !== 'stop' || gamepadRcActive) && robotOnline()) navigator.sendBeacon?.('/api/robot/rc/end'); if (gamepadLoopId) cancelAnimationFrame(gamepadLoopId); });
  init();
})();
