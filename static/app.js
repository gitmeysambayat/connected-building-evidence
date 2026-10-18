import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (x) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const number = (value, digits = 0) => value!==null && value!==undefined && value!=='' && Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-GB', {maximumFractionDigits: digits}) : 'Unavailable';
const asArray = (value, keys = []) => Array.isArray(value) ? value : keys.map(k => value?.[k]).find(Array.isArray) || [];
const readable = (value) => String(value ?? '').replace(/_/g,' ').replace(/([a-z])([A-Z])/g,'$1 $2');
const safeURL = (value) => {try {const u = new URL(value, location.href); return ['http:','https:'].includes(u.protocol) ? u.href : '#';} catch {return '#';}};
const isoDate = (value, withTime = true) => {const d = new Date(value); return Number.isNaN(+d) ? String(value ?? '') : new Intl.DateTimeFormat('en-GB', {day:'2-digit',month:'short',year:'numeric',...(withTime ? {hour:'2-digit',minute:'2-digit'} : {}),timeZone:'UTC'}).format(d);};
const localInput = (value) => value ? new Date(value).toISOString().slice(0,16) : '';
const metricName = (metric) => ({Brightness:'Illuminance',brightness:'Illuminance',ThermostatTemperature:'Thermostat temperature',thermostat_temperature:'Thermostat temperature',Temperature:'Air temperature',temperature:'Air temperature',CO2:'Carbon dioxide',co2:'Carbon dioxide',Humidity:'Relative humidity',humidity:'Relative humidity',Valve:'Valve position',Setpoint:'Controller setpoint'})[metric] || readable(metric);
const unitName = (unit) => ({C:'°C',degC:'°C',celsius:'°C','ug/m3':'µg/m³','µg/m3':'µg/m³'})[unit] || unit || '';
const roomGuid = (r) => r.guid || r.room_guid || r.global_id;
const roomName = (r) => r.room_id || r.long_name || r.name || roomGuid(r);
const channelId = (c) => c.id || c.channel_id;
const state = {summary:{},rooms:[],channels:[],selectedRoom:null,selectedChannel:null,series:null,points:[],cursor:0,view:'twin',roomMeshes:new Map(),physicalMeshes:[],geometry:null,shellVisible:true,loadedViews:new Set(),seriesRequest:0,replayTimer:null,session:null,sessionError:null,replayStatus:null,replayPending:false,replayRequest:0};

async function api(path, options = {}) {
  const response = await fetch(path, {cache:'no-store', credentials:'same-origin', ...options, headers:{Accept:'application/json',...(options.body ? {'Content-Type':'application/json'} : {}),...options.headers}});
  if (!response.ok) {const details = await response.text(); const error=new Error(`The service returned ${response.status}. ${details.slice(0,180)}`);error.status=response.status;throw error;}
  return response.json();
}
function notice(message, isError = false) {const box = $('global-notice'); box.textContent = message; box.classList.toggle('error',isError); box.classList.toggle('hidden',!message);}
function setText(id,value){if($(id)) $(id).textContent = value;}
function measuredChannels(room = null) {return state.channels.filter(c => (!room || c.room_guid === roomGuid(room)) && c.mapping_verified !== false && (!c.source_kind || c.source_kind === 'measured_sensor'));}
function channelsFor(room) {return state.channels.filter(c => c.room_guid === roomGuid(room) && c.mapping_verified !== false);}
function monitored(room) {return measuredChannels(room).length > 0;}
function renderStats() {
  const s = state.summary;
  const measured = measuredChannels();
  const rows = s.measured_records ?? s.record_count ?? s.total_readings ?? s.totals?.readings ?? s.counts?.readings ?? measured.reduce((sum,c) => sum+Number(c.source_rows || c.rows || 0),0);
  setText('stat-rooms',number(state.rooms.length));
  setText('stat-monitored',`${state.rooms.filter(monitored).length} / ${state.rooms.length}`);
  setText('stat-records',number(rows));
  const verified = state.channels.filter(c => c.mapping_verified === true).length;
  setText('stat-mapping',verified ? `${verified} verified` : 'Inspect joins');
  setText('stat-mapping-caption', 'Channels linked through source evidence');
  setText('room-count',`${state.rooms.length} spaces`);
  if(s.first_timestamp && s.last_timestamp) setText('record-period',`${isoDate(s.first_timestamp,false)} to ${isoDate(s.last_timestamp,false)}`);
  const label = s.building?.name || s.building_name || s.dataset?.building || 'Published apartment case study';
  setText('building-label', typeof label === 'string' ? label : 'Published building');
  if (s.monitoring_period || s.period) {
    const period = s.monitoring_period || s.period;
    if (typeof period === 'string') setText('record-period',period);
    else if(period.start) setText('record-period', `${isoDate(period.start,false)} to ${isoDate(period.end,false)}`);
  }
}
function renderRooms() {
  $('room-list').innerHTML = state.rooms.map(room => {
    const hasData = monitored(room), selected = state.selectedRoom && roomGuid(state.selectedRoom)===roomGuid(room), count = measuredChannels(room).length;
    const title = roomName(room);
    return `<button class="room-row ${selected?'active':''} ${hasData?'':'unmonitored'}" data-room-guid="${esc(roomGuid(room))}" aria-pressed="${selected}"><span class="room-left"><span class="room-icon">⌑</span><span><span class="room-name">${esc(title)}</span><span class="room-description">${esc(room.long_name && room.long_name!==title ? room.long_name : `IFC space · #${room.ifc_step_id || room.step_id || '…'}`)}</span></span></span><span class="room-row-right"><span class="small-dot"></span>${hasData ? `${count} channel${count===1?'':'s'}` : 'No sensor data'}</span></button>`;
  }).join('') || '<p class="empty-state">No IFC rooms were returned by the source model.</p>';
}
async function selectRoom(guid, {scroll = false} = {}) {
  const room = state.rooms.find(r => roomGuid(r)===guid);
  if(!room) return;
  ++state.seriesRequest;
  state.selectedRoom=room;
  notice('');
  renderRooms(); highlightRooms();
  setText('selected-room-title',roomName(room));
  setText('selected-room-subtitle',`${room.long_name || room.name || 'IFC space'} · ${guid}`);
  $('room-evidence-button').disabled=false;
  const channels = channelsFor(room), measured = measuredChannels(room);
  const options = channels;
  const groups=[['Measured sensor observations','measured_sensor'],['Controller setpoints','controller_setpoint'],['External virtual weather','external_virtual_weather']];
  $('channel-select').innerHTML = options.length ? groups.map(([label,kind]) => {const items=options.filter(c=>(c.source_kind || 'measured_sensor')===kind);return items.length?`<optgroup label="${label}">${items.map(c=>`<option value="${esc(channelId(c))}">${esc(metricName(c.metric))} (${esc(unitName(c.unit))})</option>`).join('')}</optgroup>`:'';}).join('') : '<option>No verified measured channels</option>';
  $('channel-select').disabled = !options.length;
  $('apply-range').disabled = !options.length;
  $('series-start').disabled = !options.length;
  $('series-end').disabled = !options.length;
  $('download-series').disabled = !options.length;
  const info = $('unmonitored-message');
  info.classList.toggle('hidden', measured.length>0);
  info.textContent = options.length ? 'This room has controller data. Controller setpoints are not measured environmental observations.' : 'This IFC room has no verified measured sensor series in the published dataset. The room remains visible without invented readings or assumed sensor locations.';
  if(options.length) await selectChannel(channelId(measured.find(c=>String(c.metric).toLowerCase()==='temperature') || measured[0] || options[0]));
  else {
    state.selectedChannel=null;state.series=null;state.points=[];state.cursor=0;state.chartScale=null;
    $('series-start').value='';$('series-end').value='';
    setExportURL();renderChart();
    setText('chart-channel','No verified monitored channel');
    setText('chart-resolution','No verified observations');$('chart-resolution').title='';
    setText('reading-label','No observation selected');
    setText('reading-timezone','Not applicable');$('reading-timezone').title='No verified channel is associated with this room.';
    setText('plot-method','No verified measured series is associated with this IFC space. No observations are plotted.');
    setText('series-provenance','Room geometry comes from the source IFC. No sensor-to-room connection is claimed.');
  }
  if(scroll && state.selectedRoom===room) $('selected-room-title').scrollIntoView({behavior:'smooth',block:'center'});
}
async function selectChannel(id) {
  state.selectedChannel=state.channels.find(c => channelId(c)===id);
  const c=state.selectedChannel;
  if(!c) return;
  $('channel-select').value=id;
  const typeNotice=$('unmonitored-message'); typeNotice.classList.toggle('hidden',c.source_kind==='measured_sensor' || !c.source_kind); typeNotice.textContent=c.source_kind==='external_virtual_weather'?'External virtual weather data, published under this room context. The six room weather files contain the same source series. This is not a room sensor reading.':'Controller setpoint events describe the commanded temperature. They are not measured room temperature.';
  setText('reading-label',c.source_kind==='external_virtual_weather'?'External weather record':c.source_kind==='controller_setpoint'?'Controller event':'Selected measured observation');
  $('series-start').value=localInput(c.first_timestamp || c.start || c.first);
  $('series-end').value=localInput(c.last_timestamp || c.end || c.last);
  await loadSeries();
}
async function loadSeries() {
  if(!state.selectedChannel) return;
  const token = ++state.seriesRequest;
  const c=state.selectedChannel;
  setExportURL();
  const params = new URLSearchParams({limit:'1600'});
  if($('series-start').value) params.set('start', new Date(`${$('series-start').value}:00Z`).toISOString());
  if($('series-end').value) params.set('end', new Date(`${$('series-end').value}:59Z`).toISOString());
  if(params.has('start') && params.has('end') && params.get('start')>params.get('end')) {notice('The start of the historical range must be before its end.',true);return;}
  state.series=null;state.points=[];state.cursor=0;renderChart();
  $('timeseries-chart').innerHTML='<p class="empty-state">Loading verified source observations…</p>';setText('chart-resolution','Loading selected channel…');setText('series-provenance',`Loading ${channelId(c)} from the published source…`);
  setText('chart-channel',`${metricName(c.metric)} · ${unitName(c.unit)}`);
  try {
    const payload = await api(`/api/series/${encodeURIComponent(channelId(c))}?${params}`);
    if(token!==state.seriesRequest) return;
    state.series=payload;
    const raw=asArray(payload,['readings','points','data','series','values']);
    state.points=raw.map((r,i) => ({timestamp:r.timestamp || r.time || r.datetime || (Array.isArray(r)?r[0]:null),value:Number(r.value ?? (Array.isArray(r)?r[1]:NaN)),source_line:r.source_line,id:r.id,gap_before_seconds:r.gap_before_seconds,gap_before:r.gap_before,index:i})).filter(r => r.timestamp && Number.isFinite(r.value)).sort((a,b)=>new Date(a.timestamp)-new Date(b.timestamp));
    state.cursor=state.points.length-1;
    const sampling=payload.sampling;
    $('chart-resolution').title=typeof sampling==='string'?sampling:JSON.stringify(sampling || 'All available records');
    setText('chart-resolution', `${number(state.points.length)} shown${payload.total ? ` / ${number(payload.total)} in range` : ''}${sampling ? ` · ${typeof sampling==='string'?readable(sampling):'API sampling'}` : ''}`);
    setText('reading-timezone', 'UTC, from source epochs'); $('reading-timezone').title=payload.timestamp_basis || c.timestamp_basis || 'UTC from the API';
    setText('plot-method', c.source_kind==='controller_setpoint'?'Controller events are shown as a step history. No cadence gap rule is applied to command events.':`${payload.total>payload.returned?'Selected original source observations':'All original observations in the selected range'}. Lines break where consecutive original records contain a gap longer than one hour. No values are filled or averaged.`);
    const kind = c.source_kind === 'controller_setpoint' ? 'Published controller setpoints' : c.source_kind === 'external_virtual_weather' ? 'Published external virtual weather, not an indoor sensor' : 'Published measured sensor records';
    $('series-provenance').innerHTML=`<span><span class="verified-icon">✓</span> ${esc(kind)}</span><span>Channel: <code>${esc(channelId(c))}</code></span><span>Source: <code>${esc(c.source_csv || c.source_file || 'See source evidence')}</code></span><a href="/api/series/${encodeURIComponent(channelId(c))}?${params}" target="_blank" rel="noopener">Inspect response ↗</a>`;
    notice('');renderChart();
  } catch(err) {if(token!==state.seriesRequest) return;state.points=[];renderChart();notice(`The measurements could not be loaded. ${err.message}`,true);}
}

function plotSize() {return {width:Math.max($('timeseries-chart').clientWidth,300),height:$('timeseries-chart').clientHeight || 227};}
function renderChart() {
  const host=$('timeseries-chart'),points=state.points;
  $('chart-cursor').disabled=!points.length;
  $('chart-cursor').max=Math.max(0,points.length-1);$('chart-cursor').value=Math.max(0,state.cursor);
  if(!points.length) {host.innerHTML='<p class="empty-state">No verified observations are available in this selection.</p>';setText('reading-value','·');setText('reading-unit','');setText('reading-time','No observation selected.');setText('reading-count','0');setText('reading-min','·');setText('reading-max','·');setText('cursor-timestamp','No observation selected');return;}
  const {width:w,height:h}=plotSize(), margin={left:42,right:15,top:23,bottom:31};
  const times=points.map(r=>+new Date(r.timestamp)),values=points.map(r=>r.value),t0=times[0],t1=times.at(-1);
  let ymin=Math.min(...values),ymax=Math.max(...values),pad=(ymax-ymin)*.13 || Math.max(Math.abs(ymax)*.05,.5);ymin-=pad;ymax+=pad;
  const x=(t)=>margin.left+(t-t0)/(t1-t0 || 1)*(w-margin.left-margin.right),y=(v)=>h-margin.bottom-(v-ymin)/(ymax-ymin)*(h-margin.top-margin.bottom);
  state.chartScale={x,y,t0,t1,margin,w,h};
  const colour=getComputedStyle(document.body).getPropertyValue('--accent').trim(),grid=getComputedStyle(document.body).getPropertyValue('--chart-grid').trim(),muted=getComputedStyle(document.body).getPropertyValue('--muted').trim();
  let ticks='';
  for(let i=0;i<5;i++) {const val=ymin+(ymax-ymin)*i/4,yy=y(val);ticks+=`<line x1="${margin.left}" x2="${w-margin.right}" y1="${yy}" y2="${yy}" stroke="${grid}"/><text x="${margin.left-9}" y="${yy+3}" text-anchor="end" fill="${muted}" font-size="8">${number(val,1)}</text>`;}
  for(let i=0;i<5;i++) {const tt=t0+(t1-t0)*i/4;const label=new Date(tt).toLocaleDateString('en-GB',{day:'2-digit',month:'short',timeZone:'UTC'});ticks+=`<text x="${x(tt)}" y="${h-7}" text-anchor="middle" fill="${muted}" font-size="8">${esc(label)}</text>`;}
  const differences=times.slice(1).map((t,i)=>t-times[i]).filter(v=>v>0).sort((a,b)=>a-b), median=differences[Math.floor(differences.length/2)] || Infinity;
  let path='',area='',segment=[];
  const paths=[];
  const finish=()=>{if(!segment.length)return;const d=segment.map((p,i)=>!i?`M${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`:state.selectedChannel?.source_kind==='controller_setpoint'?`H${x(p.t).toFixed(1)} V${y(p.v).toFixed(1)}`:`L${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');paths.push(`<path d="${d}" fill="none" stroke="${colour}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>`);segment=[];};
  points.forEach((p,i)=>{if(i && (state.series?.gap_rule ? (state.series.gap_rule.enabled && p.gap_before_seconds>state.series.gap_rule.threshold_seconds) : (p.gap_before===true || p.gap_before_seconds>3600 || times[i]-times[i-1]>Math.max(median*3,3600000))))finish();segment.push({t:times[i],v:p.value});});finish();
  host.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(metricName(state.selectedChannel?.metric))} over the selected historical period"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="${colour}" stop-opacity=".09"/><stop offset="1" stop-color="${colour}" stop-opacity="0"/></linearGradient></defs>${ticks}${paths.join('')}<g id="chart-cursor-mark"></g><rect x="${margin.left}" y="${margin.top}" width="${w-margin.left-margin.right}" height="${h-margin.top-margin.bottom}" fill="transparent" id="chart-hit-area"/></svg>`;
  setText('reading-count',number(points.length));setText('reading-min',`${number(Math.min(...values),2)} ${unitName(state.selectedChannel?.unit)}`);setText('reading-max',`${number(Math.max(...values),2)} ${unitName(state.selectedChannel?.unit)}`);
  updateCursor(state.cursor);
}
function updateCursor(index) {
  if(!state.points.length)return;
  state.cursor=Math.max(0,Math.min(state.points.length-1,Number(index)));
  const p=state.points[state.cursor],scale=state.chartScale, unit=unitName(state.series?.unit || state.selectedChannel?.unit);
  setText('reading-value',number(p.value,2));setText('reading-unit',unit);setText('reading-time',`${isoDate(p.timestamp)} UTC${p.source_line ? ` · source line ${p.source_line}` : ''}`);setText('cursor-timestamp',`${isoDate(p.timestamp)} UTC`);
  $('chart-cursor').value=state.cursor;
  const marker=$('chart-cursor-mark');
  if(marker && scale) {const xx=scale.x(+new Date(p.timestamp)),yy=scale.y(p.value),colour=getComputedStyle(document.body).getPropertyValue('--accent').trim();marker.innerHTML=`<line x1="${xx}" x2="${xx}" y1="${scale.margin.top}" y2="${scale.h-scale.margin.bottom}" stroke="${colour}" stroke-opacity=".45" stroke-dasharray="3 3"/><circle cx="${xx}" cy="${yy}" r="4" fill="${colour}" stroke="var(--card)" stroke-width="2"/>`;}
}
function chartPointer(event) {if(!state.chartScale || !state.points.length)return;const rect=$('timeseries-chart').getBoundingClientRect(),s=state.chartScale,tx=s.t0+(event.clientX-rect.left-s.margin.left)/(s.w-s.margin.left-s.margin.right)*(s.t1-s.t0);let lo=0,hi=state.points.length-1;while(lo<hi){const mid=Math.floor((lo+hi)/2);if(+new Date(state.points[mid].timestamp)<tx)lo=mid+1;else hi=mid;}if(lo>0 && Math.abs(+new Date(state.points[lo-1].timestamp)-tx)<Math.abs(+new Date(state.points[lo].timestamp)-tx))lo--;updateCursor(lo);}
function setExportURL(){const link=$('download-series');link.setAttribute('aria-disabled',String(!state.selectedChannel));if(!state.selectedChannel){link.href='#';return;}const c=state.selectedChannel,params=new URLSearchParams();if($('series-start').value)params.set('start',new Date(`${$('series-start').value}:00Z`).toISOString());if($('series-end').value)params.set('end',new Date(`${$('series-end').value}:59Z`).toISOString());link.href=`/api/series/${encodeURIComponent(channelId(c))}/export.csv?${params}`;}
function downloadSeries(event){if(!state.selectedChannel || $('download-series').getAttribute('aria-disabled')==='true'){event.preventDefault();return;}setExportURL();}

let renderer,scene,camera,controls,modelGroup,grid,modelAnimation,resizeObserver;
const pickRay=new THREE.Raycaster(),pickPoint=new THREE.Vector2();
async function initialiseModel() {
  try {
    const geometry = await api('/api/geometry');state.geometry=geometry;
    const host=$('model-viewport');renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'high-performance'});renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));renderer.setSize(host.clientWidth,host.clientHeight);renderer.setClearColor(0xedf1eb);renderer.outputColorSpace=THREE.SRGBColorSpace;host.prepend(renderer.domElement);renderer.domElement.setAttribute('aria-label','Three-dimensional geometry extracted from the published IFC model');renderer.domElement.dataset.renderer='Three.js WebGLRenderer';renderer.domElement.dataset.webglContext=renderer.getContext().constructor.name;renderer.domElement.dataset.sourceModelSha256=geometry.source_ifc_sha256 || geometry.source?.ifc_sha256 || '';renderer.domElement.dataset.sourceSpaceMeshes=String((geometry.meshes || []).filter(m=>m.kind==='space').length);
    scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(38,host.clientWidth/host.clientHeight,.03,300);camera.up.set(0,0,1);controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.09;controls.maxPolarAngle=Math.PI*.49;controls.minDistance=2;controls.maxDistance=55;controls.enablePan=true;
    scene.add(new THREE.AmbientLight(0xffffff,2.4));const key=new THREE.DirectionalLight(0xfff8ec,2.5);key.position.set(4,-5,12);scene.add(key);const fill=new THREE.DirectionalLight(0xe5efdf,1.5);fill.position.set(-8,3,5);scene.add(fill);
    modelGroup=new THREE.Group();scene.add(modelGroup);
    const bounds=geometry.bounds || {min:[-5,-5,0],max:[5,5,4]};const centre=new THREE.Vector3((bounds.min[0]+bounds.max[0])/2,(bounds.min[1]+bounds.max[1])/2,bounds.min[2]);modelGroup.position.copy(centre.clone().multiplyScalar(-1));
    for(const mesh of geometry.meshes || []) {
      if(!mesh.vertices?.length || !mesh.faces?.length)continue;
      const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(mesh.vertices.flat(),3));g.setIndex(mesh.faces.flat());g.computeVertexNormals();g.computeBoundingSphere();
      const isSpace=mesh.kind==='space' || mesh.ifc_class==='IfcSpace';
      const material=new THREE.MeshStandardMaterial({color:isSpace?0x84a594:0xc9d0c1,roughness:.92,metalness:0,transparent:true,opacity:isSpace?.13:.27,depthWrite:false,side:THREE.DoubleSide});
      const item=new THREE.Mesh(g,material);item.userData={...mesh,isSpace};item.renderOrder=isSpace?2:1;modelGroup.add(item);
      const edge=new THREE.LineSegments(new THREE.EdgesGeometry(g,25),new THREE.LineBasicMaterial({color:isSpace?0x749584:0x9aa995,transparent:true,opacity:isSpace?.37:.21,depthWrite:false}));edge.renderOrder=3;item.add(edge);item.userData.edge=edge;
      if(isSpace)state.roomMeshes.set(mesh.guid,item);else state.physicalMeshes.push(item);
    }
    const width=bounds.max[0]-bounds.min[0],depth=bounds.max[1]-bounds.min[1];grid=new THREE.GridHelper(Math.max(30,width*2),30,0xbdcdb9,0xd7dfd2);grid.rotation.x=Math.PI/2;grid.position.z=-.03;grid.material.transparent=true;grid.material.opacity=.38;scene.add(grid);
    setText('model-file',geometry.source?.file || '04_Flat.ifc');setText('model-count',`${state.roomMeshes.size} spaces · ${state.physicalMeshes.length} elements`);
    resetModel();highlightRooms();$('model-loading').classList.add('hidden');
    renderer.domElement.addEventListener('pointerdown',e=>{state.pointerStart={x:e.clientX,y:e.clientY};});
    renderer.domElement.addEventListener('pointerup',e=>{if(!state.pointerStart || Math.hypot(e.clientX-state.pointerStart.x,e.clientY-state.pointerStart.y)>5)return;const hit=pickSpace(e);if(hit)selectRoom(hit.object.userData.guid);});
    renderer.domElement.addEventListener('pointermove',e=>{const hit=pickSpace(e),tip=$('model-tooltip');renderer.domElement.style.cursor=hit?'pointer':'grab';if(!hit){tip.classList.add('hidden');return;}const r=state.rooms.find(r=>roomGuid(r)===hit.object.userData.guid),rect=host.getBoundingClientRect();tip.innerHTML=`${esc(r?roomName(r):hit.object.userData.long_name || hit.object.userData.name)}<small>${r && monitored(r)?'Verified historical measurements':'IFC space · no verified measured series'}</small>`;tip.style.left=`${Math.min(e.clientX-rect.left+12,host.clientWidth-200)}px`;tip.style.top=`${Math.max(55,e.clientY-rect.top-42)}px`;tip.classList.remove('hidden');});
    renderer.domElement.addEventListener('pointerleave',()=>$('model-tooltip').classList.add('hidden'));
    resizeObserver=new ResizeObserver(()=>{if(!renderer)return;const w=host.clientWidth,h=host.clientHeight;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h);});resizeObserver.observe(host);
    const animate=()=>{modelAnimation=requestAnimationFrame(animate);controls.update();if(state.view==='twin' && !document.hidden)renderer.render(scene,camera);};animate();
    updateModelTheme();
  } catch(err) {$('model-loading').innerHTML=`<p class="error-hint">The IFC geometry viewer could not start.<br>${esc(err.message)}<br>The room register and source evidence remain available.</p>`;console.error(err);}
}
function pickSpace(event){if(!renderer)return null;const r=renderer.domElement.getBoundingClientRect();pickPoint.set((event.clientX-r.left)/r.width*2-1,-(event.clientY-r.top)/r.height*2+1);pickRay.setFromCamera(pickPoint,camera);return pickRay.intersectObjects([...state.roomMeshes.values()],false)[0] || null;}
function highlightRooms(){for(const [guid,mesh] of state.roomMeshes){const r=state.rooms.find(r=>roomGuid(r)===guid),selected=state.selectedRoom && roomGuid(state.selectedRoom)===guid,hasData=r && monitored(r);mesh.material.color.set(selected?0xb97043:hasData?0x6f9e8c:0xb1b4a8);mesh.material.opacity=selected?.36:hasData?.13:.06;mesh.userData.edge.material.color.set(selected?0xa76239:hasData?0x6c9282:0xa0a699);mesh.userData.edge.material.opacity=selected?.9:hasData?.34:.22;mesh.renderOrder=selected?5:2;}}
function resetModel(top=false){if(!camera || !state.geometry)return;const b=state.geometry.bounds,span=Math.max(b.max[0]-b.min[0],b.max[1]-b.min[1]),height=b.max[2]-b.min[2];controls.target.set(0,0,height*.3);camera.position.set(top?0:span*.95,top?-.001:-span*1.23,top?span*1.85:span*1.05);controls.update();$('view-perspective').classList.toggle('active',!top);$('view-top').classList.toggle('active',top);}
function updateModelTheme(){if(!renderer)return;const dark=document.body.classList.contains('theme-dark');renderer.setClearColor(dark?0x28392e:0xedf1eb);grid.material.color?.set?.(dark?0x4c6650:0xc5d3bd);}

const viewTitles={twin:['BIM + MEASURED ENVIRONMENT','A building you can interrogate.','Select a real IFC room. Follow its published measurements back to the source.'],mapping:['SPATIAL IDENTITY + PROVENANCE','Every connection, accounted for.','Inspect the identifiers and relationships behind the room-to-channel mapping.'],quality:['COVERAGE + DATA ASSURANCE','Useful evidence has limits.','Find gaps and inconsistent records before drawing conclusions.'],analytics:['MEASURED PATTERNS + VALIDATION','From observation to prediction.','Evaluate historical behaviour with explicit methods and honest comparisons.'],transport:['INGESTION + EVENT PROVENANCE','A working pipeline, made visible.','Historical records move through a real application data path.'],evidence:['SOURCE INTEGRITY + REPRODUCIBILITY','Nothing hidden behind the model.','Inspect the source files, permissions and limits of this demonstration.']};
async function switchView(view){if(!viewTitles[view])view='twin';state.view=view;document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id===`view-${view}`));document.querySelectorAll('.nav-item').forEach(btn=>{const active=btn.dataset.view===view;btn.classList.toggle('active',active);if(active)btn.setAttribute('aria-current','page');else btn.removeAttribute('aria-current');});const [eyebrow,title,subtitle]=viewTitles[view];setText('page-eyebrow',eyebrow);setText('page-title',title);setText('page-subtitle',subtitle);history.replaceState(null,'',`#${view}`);if(view==='twin'){requestAnimationFrame(()=>{renderChart();if(renderer){renderer.setSize($('model-viewport').clientWidth,$('model-viewport').clientHeight);camera.aspect=$('model-viewport').clientWidth/$('model-viewport').clientHeight;camera.updateProjectionMatrix();}});}if(!state.loadedViews.has(view)){if(view==='mapping')renderMapping();if(view==='quality')await loadQuality();if(view==='analytics')await loadAnalytics();if(view==='evidence')renderEvidence();if(view==='transport')await refreshReplay();state.loadedViews.add(view);}if(view==='transport' && !state.replayTimer)state.replayTimer=setInterval(refreshReplay,2000);if(view!=='transport' && state.replayTimer){clearInterval(state.replayTimer);state.replayTimer=null;}}
function renderMapping(){const verified=state.channels.filter(c=>c.mapping_verified===true),unknown=state.channels.filter(c=>c.mapping_verified===false);$('mapping-overview').innerHTML=`<div class="data-grid"><section class="card data-card"><span class="eyebrow">VERIFIED CHANNELS</span><strong class="big-number">${verified.length}</strong><p>Accepted source relationships. Measured channels and controller setpoints remain distinct.</p></section><section class="card data-card"><span class="eyebrow">MEASURED ROOM COVERAGE</span><strong class="big-number">${state.rooms.filter(monitored).length} of ${state.rooms.length}</strong><p>Rooms with verified measured sensor observations in the published records.</p></section><section class="card data-card"><span class="eyebrow">UNRESOLVED CHANNELS</span><strong class="big-number">${unknown.length}</strong><p>CSV-only candidates remain separate from verified source relationships.</p></section></div>`;$('mapping-register').innerHTML=`<div class="card-heading"><h3>IFC room mapping register</h3><span class="badge">${state.rooms.length} source spaces</span></div><table><thead><tr><th>IFC room</th><th>Persistent identifier</th><th>Measured channels</th><th>Controller channels</th><th>Evidence</th></tr></thead><tbody>${state.rooms.map(r=>`<tr><td><strong>${esc(roomName(r))}</strong><br><span class="muted">${esc(r.long_name || r.name || '')}</span></td><td><code>${esc(roomGuid(r))}</code><br><span class="muted">IFC #${esc(r.ifc_step_id || r.step_id || '')}</span></td><td>${measuredChannels(r).length ? measuredChannels(r).map(c=>`<span class="tag">${esc(metricName(c.metric))}</span>`).join(' ') : '<span class="tag unknown">Unmonitored</span>'}</td><td>${channelsFor(r).filter(c=>c.source_kind==='controller_setpoint').map(c=>esc(metricName(c.metric))).join(', ') || 'None'}</td><td><button class="table-action" data-evidence-guid="${esc(roomGuid(r))}">Inspect join ↗</button></td></tr>`).join('')}</tbody></table>`;if(unknown.length)$('mapping-register').insertAdjacentHTML('beforeend',`<div class="card-heading"><h3>Excluded or external source channels</h3></div><table><thead><tr><th>Channel</th><th>Source classification</th><th>Mapping status</th></tr></thead><tbody>${unknown.map(c=>`<tr><td><code>${esc(channelId(c))}</code></td><td>${esc(readable(c.source_kind))}</td><td>${esc(readable(c.mapping_status || 'No verified IFC relationship'))}</td></tr>`).join('')}</tbody></table>`);}
function jsonDetails(data,label='Inspect the complete API evidence'){return `<details class="json-toggle"><summary>${esc(label)}</summary><pre>${esc(JSON.stringify(data,null,2))}</pre></details>`;}
function renderValue(value){if(value===null || value===undefined)return '<span class="muted">Not provided</span>';if(typeof value==='boolean')return value?'Yes':'No';if(typeof value==='number')return number(value,3);if(typeof value==='object')return esc(JSON.stringify(value));return esc(value);}
function kvHTML(obj, omit=[]){return `<dl class="key-value-list">${Object.entries(obj || {}).filter(([k,v])=>!omit.includes(k) && v!==null && v!==undefined).map(([k,v])=>`<div><dt>${esc(readable(k))}</dt><dd>${renderValue(v)}</dd></div>`).join('')}</dl>`;}
async function inspectEvidence(guid){const room=state.rooms.find(r=>roomGuid(r)===guid),dialog=$('evidence-dialog');$('dialog-evidence-body').innerHTML='<p class="empty-state">Retrieving source mapping evidence…</p>';dialog.showModal();try{const evidence=await api(`/api/rooms/${encodeURIComponent(guid)}/evidence`);$('dialog-evidence-body').innerHTML=`<p class="eyebrow">${esc(room?roomName(room):'IFC room')}</p><h3>${esc(room?.long_name || room?.name || 'Spatial relationship evidence')}</h3><p class="subtle-note mono">${esc(guid)}</p><div class="evidence-callout">This connection is supported only at the resolution documented by the source. A room relationship does not establish a physical sensor coordinate.</div>${evidence.mapping_statement?`<p class="subtle-note">${esc(evidence.mapping_statement)}</p>`:''}${evidence.chain?`<div class="evidence-chain">${evidence.chain.map(step=>`<div><span>${esc(step.step)}</span><div><strong>${esc(step.relation)}</strong><small>${esc(step.object || step.source || '')}</small></div></div>`).join('')}</div>`:''}${evidence.explanation?`<p class="subtle-note">${esc(evidence.explanation)}</p>`:''}${kvHTML(evidence,['channels','room','evidence','assertions','relationships','chain','channel_chains','semantic_query','unverified_candidates'])}${evidence.room?`<h3>Source IFC room</h3>${kvHTML(evidence.room)}`:''}${Array.isArray(evidence.channels)?`<h3>Channel evidence</h3>${evidence.channels.map(c=>`<section class="source-card"><strong>${esc(channelId(c) || c.metric || 'Channel')}</strong><p>${esc(readable(c.source_kind))} · ${esc(metricName(c.metric))} (${esc(unitName(c.unit))})</p>${kvHTML({source_csv:c.source_csv,source_rdf:c.source_rdf,mapping_status:c.mapping_status,mapping_note:c.mapping_note,source_sha256:c.source_sha256})}<div class="source-links">${c.source_url?`<a class="chip-link" href="${esc(safeURL(c.source_url))}" target="_blank" rel="noopener">Source CSV ↗</a>`:''}${c.rdf_source_url?`<a class="chip-link" href="${esc(safeURL(c.rdf_source_url))}" target="_blank" rel="noopener">Source RDF ↗</a>`:''}</div>${jsonDetails(c,'Inspect full channel metadata')}</section>`).join('')}`:''}${jsonDetails(evidence,'Inspect the complete join evidence')}`;}catch(err){$('dialog-evidence-body').innerHTML=`<p class="notice error">The connection evidence could not be loaded. ${esc(err.message)}</p>`;}}

function listData(value,keys=['channels','rooms','results','checks','metrics','items']){return asArray(value,keys);}
function scalarCards(obj){const entries=Object.entries(obj || {}).filter(([k,v])=>typeof v==='number' || typeof v==='boolean').slice(0,8);return entries.length?`<div class="quality-topline">${entries.map(([k,v])=>`<section class="card data-card"><span class="eyebrow">${esc(readable(k))}</span><strong class="big-number">${typeof v==='boolean'?(v?'Yes':'No'):number(v,2)}</strong></section>`).join('')}</div>`:'';}
function simpleTable(rows,keys=null){if(!rows.length)return '';const columns=keys || [...new Set(rows.flatMap(r=>Object.keys(r)))].filter(k=>rows.some(r=>r[k]!==undefined && typeof r[k]!=='object')).slice(0,9);return `<div class="card table-card"><table><thead><tr>${columns.map(k=>`<th>${esc(readable(k))}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${columns.map(k=>`<td>${renderValue(r[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
async function loadQuality(){const host=$('quality-content');try{const q=await api('/api/quality');host.className='';const summaries=q.summary || q.totals || q.overview || q;let html=scalarCards(summaries);html+=`<section class="card source-card"><p class="eyebrow">INTERPRETATION BEFORE ANALYSIS</p><h3>Source qualifications</h3>${['handling','gap_rule','outdoor_data','calibration','sensor_locations'].filter(k=>q[k]).map(k=>`<p><strong>${esc(readable(k))}:</strong> ${esc(q[k])}</p>`).join('')}</section>`;if(q.limitations || q.warnings)html+=`<section class="card source-card"><h3>Interpretation limits</h3>${asArray(q.limitations || q.warnings).map(x=>`<p>${esc(typeof x==='string'?x:JSON.stringify(x))}</p>`).join('')}</section>`;const rows=listData(q);if(rows.length)html+=simpleTable(rows);for(const [key,val] of Object.entries(q)){if(Array.isArray(val)&&val.length && val!==rows){if(typeof val[0]==='object')html+=`<section class="card source-card"><h3>${esc(readable(key))}</h3>${simpleTable(val)}</section>`;else html+=`<section class="card source-card"><h3>${esc(readable(key))}</h3>${val.map(x=>`<p>${esc(x)}</p>`).join('')}</section>`;}}host.innerHTML=html+`<section class="card source-card"><h3>Assurance evidence</h3><p>Checks are calculated from the ingested records. Gaps do not establish occupancy, faults or structural condition.</p>${jsonDetails(q)}</section>`;}catch(err){host.innerHTML=`<div class="notice error">Data assurance is unavailable. ${esc(err.message)}</div>`;}}
function forecastChart(example){
  if(!Array.isArray(example)||!example.length)return '';
  const points=example.filter(p=>Number.isFinite(Number(p.actual))&&Number.isFinite(Number(p.predicted))&&Number.isFinite(Date.parse(p.timestamp))).sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
  if(!points.length)return '';
  const w=900,h=250,m={l:43,r:20,t:20,b:36},values=points.flatMap(p=>[+p.actual,+p.predicted,+p.baseline]).filter(Number.isFinite),lo=Math.min(...values)-.3,hi=Math.max(...values)+.3,t0=Date.parse(points[0].timestamp),t1=Date.parse(points.at(-1).timestamp),x=t=>m.l+(t-t0)/(t1-t0||1)*(w-m.l-m.r),y=v=>h-m.b-(v-lo)/(hi-lo||1)*(h-m.t-m.b);
  let grid='';
  for(let i=0;i<5;i++){const val=lo+(hi-lo)*i/4;grid+=`<line x1="${m.l}" x2="${w-m.r}" y1="${y(val)}" y2="${y(val)}" stroke="var(--line)"/><text x="${m.l-8}" y="${y(val)+3}" fill="var(--muted)" font-size="10" text-anchor="end">${number(val,1)}</text>`;}
  for(let i=0;i<5;i++){const t=t0+(t1-t0)*i/4;grid+=`<text x="${x(t)}" y="${h-8}" fill="var(--muted)" font-size="9" text-anchor="middle">${esc(new Date(t).toLocaleDateString('en-GB',{day:'2-digit',month:'short',timeZone:'UTC'}))}</text>`;}
  const markers=(key,colour)=>points.filter(p=>Number.isFinite(+p[key])).map(p=>`<circle cx="${x(Date.parse(p.timestamp))}" cy="${y(+p[key])}" r="2.2" fill="${colour}" fill-opacity=".76"><title>${esc(isoDate(p.timestamp))} UTC · ${esc(key)} ${number(p[key],2)} °C</title></circle>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Selected held-out measured observations, Ridge forecasts and persistence forecasts at actual UTC timestamps">${grid}${markers('baseline','#9b9d91')}${markers('predicted','#ba764d')}${markers('actual','#4d816b')}</svg><p class="subtle-note"><span style="color:#4d816b">●</span> Measured test observations &nbsp; <span style="color:#ba764d">●</span> Ridge forecast &nbsp; <span style="color:#9b9d91">●</span> Persistence forecast<br>Selected held-out examples, positioned by their actual UTC timestamps. Points are not joined across unsupplied observations.</p>`;
}
async function loadAnalytics(){
  const host=$('analytics-content');
  try {
    const a=await api('/api/analytics');const operations=await api('/api/operations').catch(()=>null);host.className='';
    const rooms=asArray(a,['rooms','results']);state.forecastRooms=rooms;
    const rejected=a.recommended_predictor==='persistence' || rooms.some(r=>r.recommended_predictor==='persistence') || String(a.model_assurance || '').toLowerCase().includes('reject');
    let html=rejected?`<section class="assurance-rejection"><p class="eyebrow">MODEL ASSURANCE GATE</p><h3>Keep the stronger baseline.</h3><p>The trained Ridge model did not outperform persistence in validation. The evidence supports retaining the persistence baseline for this historical experiment. This is a model assessment result, not validation for operating the building.</p></section>`:'';
    html+=`<div class="data-grid"><section class="card data-card"><span class="eyebrow">RECOMMENDED PREDICTOR</span><strong class="big-number">${esc(rejected?'Persistence':readable(a.recommended_predictor || a.status || 'Inspect results'))}</strong><p>${esc(a.model_assurance || 'Recommendation is based on the validation period. The held-out test is reported separately.')}</p></section><section class="card data-card"><span class="eyebrow">FORECAST HORIZON</span><strong class="big-number">${number(a.horizon_minutes ?? 60)} min</strong><p>Predictions are evaluated against observations from a later time period.</p></section><section class="card data-card"><span class="eyebrow">ASSESSED ROOMS</span><strong class="big-number">${rooms.length}</strong><p>Reported errors apply to these channels and this historical monitoring period.</p></section></div>`;
    if(rooms.length){
      html+=`<section class="card table-card"><div class="card-heading"><h3>Held-out test performance</h3><span class="badge">Errors in source temperature units</span></div><table class="metrics-table"><thead><tr><th>Room</th><th>Training records</th><th>Validation / test</th><th>Ridge MAE</th><th>Persistence MAE</th><th>Ridge change vs baseline</th></tr></thead><tbody>${rooms.map(r=>`<tr><td><strong>${esc(r.room || r.room_id || r.name || r.room_guid)}</strong></td><td>${number(r.train_count)}</td><td>${number(r.validation_count)} / ${number(r.test_count)}</td><td>${number(r.model?.mae,3)}</td><td>${number(r.baseline?.mae,3)}</td><td><span class="tag ${Number(r.improvement_percent)<0?'attention':''}">${number(r.improvement_percent,1)}%</span></td></tr>`).join('')}</tbody></table><p class="subtle-note" style="padding:13px 16px">Negative change means that Ridge has a larger mean absolute error than persistence. Model selection uses validation data; the later test data assess the frozen choice.</p></section>`;
      const examples=rooms.filter(r=>r.example?.length);if(examples.length)html+=`<section class="card source-card"><p class="eyebrow">SELECTED HELD-OUT FORECAST EXAMPLES</p><div class="graph-controls"><label>IFC room<select id="forecast-room-select">${examples.map((r,i)=>`<option value="${i}">${esc(r.room || r.room_id || r.name || r.room_guid)}</option>`).join('')}</select></label></div><div id="forecast-example">${forecastChart(examples[0].example)}</div></section>`;
    }
    if(operations)html+=operationsHTML(operations);
    if(a.limitations)html+=`<section class="card source-card"><h3>Methods & limits</h3>${asArray(a.limitations).map(l=>`<p>${esc(typeof l==='string'?l:JSON.stringify(l))}</p>`).join('')}</section>`;
    host.innerHTML=html+`<section class="card source-card"><h3>Reproducible analysis</h3><p>Inspect the chronological split, baseline comparison and result files. No claim of current physical monitoring or building control follows from this offline experiment.</p>${jsonDetails(a)}</section>`;
    if($('forecast-room-select'))$('forecast-room-select').addEventListener('change',e=>{const r=state.forecastRooms.filter(r=>r.example?.length)[Number(e.target.value)];$('forecast-example').innerHTML=forecastChart(r.example);});
  }catch(err){host.innerHTML=`<div class="notice error">The analysis report could not be loaded. ${esc(err.message)}</div>`;}
}
function operationsHTML(report){
  const rooms=asArray(report,['rooms']),threshold=report.threshold_c ?? 1;
  return `<section class="card source-card"><p class="eyebrow">DESCRIPTIVE OPERATIONAL ANALYSIS</p><h3>Heating setpoint tracking</h3><p>How does measured air temperature compare with the most recent recorded heating command? The comparison is observation-weighted and uses only records with an eligible command history.</p><p><strong>Method:</strong> ${esc(report.method || 'As-of recorded command, with a maximum age of 24 hours; no interpolation.')}</p>${simpleTable(rooms.map(r=>({Room:r.room,'Eligible observations':r.eligible_observations,'Coverage (%)':r.observation_coverage_percent,'Mean air minus command (°C)':r.mean_air_minus_setpoint_c,'Mean absolute difference (°C)':r.mean_absolute_difference_c,[`Below command by >${threshold} °C (%)`]:r.fraction_more_than_1c_below==null?null:r.fraction_more_than_1c_below*100,[`Above command by >${threshold} °C (%)`]:r.fraction_more_than_1c_above==null?null:r.fraction_more_than_1c_above*100})))}<p class="subtle-note">Temperature differences are in °C. Positive mean differences indicate air temperature above the recorded command. The fractions describe eligible observations, not duration-weighted exposure.</p>${asArray(report.limitations).map(l=>`<p>${esc(l)}</p>`).join('')}<div class="source-links"><a class="chip-link" href="/api/operations" target="_blank">Inspect operational calculations ↗</a></div>${jsonDetails(report,'Inspect the complete setpoint-tracking calculation')}</section>`;
}

function publicReplayContext(status = state.replayStatus) {
  if(status?.public_demo && typeof status.public_demo === 'object') return status.public_demo;
  if(state.session?.public_demo === true) return state.session;
  return null;
}
function replayActive(status = state.replayStatus) {
  return Boolean(status?.running ?? status?.active ?? (status?.replay === 'running' || status?.status === 'running'));
}
function replayParameters(context = publicReplayContext()) {
  const speed=Number($('replay-speed').value),limit=Number($('replay-limit').value),caps=context?.capabilities;
  if(!Number.isFinite(speed) || speed<=0 || !Number.isInteger(limit) || limit<1) return {valid:false,message:'Choose a positive replay speed and a whole number of events.'};
  if(caps && (limit>caps.max_events || speed>caps.max_events_per_second || limit/speed>caps.max_duration_seconds)) return {valid:false,message:`Choose at most ${number(Math.min(caps.max_events,Math.floor(speed*caps.max_duration_seconds)))} events at this speed. Shared replay runs are limited to ${number(caps.max_duration_seconds)} seconds.`};
  return {valid:true,speed,limit,message:''};
}
function updateReplayControls() {
  const status=state.replayStatus,context=publicReplayContext(),active=replayActive(status),ready=Boolean(status || state.session);
  const controls=context?.controls;
  const allowed=context ? {
    start:context.session_ready === true && controls?.can_start === true,
    stop:context.session_ready === true && controls?.can_stop === true,
    reconnect:context.session_ready === true && controls?.can_restart_broker === true,
  } : {start:ready && !active,stop:ready && active,reconnect:ready && !active && status?.broker === 'running'};
  const params=replayParameters(context);
  for(const action of ['start','stop','reconnect']) $('replay-'+action).disabled=state.replayPending || Boolean(state.sessionError) || !allowed[action] || (action==='start' && !params.valid);
  $('replay-speed').disabled=state.replayPending || active;
  $('replay-limit').disabled=state.replayPending || active;
  if(context?.capabilities) $('replay-limit').max=String(Math.min(context.capabilities.max_events,Math.floor(Number($('replay-speed').value)*context.capabilities.max_duration_seconds)));
  else $('replay-limit').max='2000';
  setText('replay-validation',params.message);
  $('replay-validation').classList.toggle('hidden',!params.message);
}
function renderRuntimeProfile(context = publicReplayContext()) {
  const publicMode=Boolean(context),banner=$('runtime-profile');
  banner.classList.toggle('hidden',!publicMode);
  if(!publicMode) return;
  setText('runtime-profile-title','Full application · shared public demonstration');
  setText('runtime-profile-description','The Python services run on the server. Measurements remain historical. Free hosting can sleep when idle; the first request may take longer. Replay history can reset when the runtime restarts. The published source records are preserved.');
  setText('replay-description','Send original historical records through the server’s authenticated MQTT broker at QoS 1. One visitor controls replay at a time. The source observations remain from 2017.');
  setText('pipeline-retention-title','Recorded receipts');
  setText('pipeline-retention-description','Queryable events with limited runtime retention');
  const caps=context.capabilities || {};
  setText('replay-limits',`Shared replay: up to ${number(caps.max_events)} events, ${number(caps.max_duration_seconds)} seconds and ${number(caps.max_events_per_second)} events/s. Repeat a batch to inspect duplicate delivery handling.`);
  $('replay-limits').classList.remove('hidden');
}
async function initialiseSession() {
  try {
    state.session=await api('/api/session');state.sessionError=null;
  } catch(error) {
    if(error.status===404) {state.session={public_demo:false,session_ready:true};state.sessionError=null;}
    else {state.sessionError=error.message;setText('replay-status','Replay controls are unavailable because the browser session could not be established. Reload to try again.');}
  }
  renderRuntimeProfile();updateReplayControls();
}
async function refreshReplay(){
  const request=++state.replayRequest;
  try{
    const [status,payload]=await Promise.all([api('/api/replay/status'),api('/api/replay/events')]);
    if(request!==state.replayRequest) return;
    state.replayStatus=status;
    const active=replayActive(status),context=publicReplayContext(status),lease=context?.lease;
    renderRuntimeProfile(context);updateReplayControls();
    const otherVisitor=Boolean(lease?.active && !lease.owned_by_you);
    const heading=otherVisitor?'Replay reserved by another visitor':active?'Historical replay running':readable(status.replay || status.status || 'Ready');
    let leaseMessage=context ? otherVisitor?`Another visitor holds the shared replay controls. Try again when the lease expires in approximately ${number(lease.expires_in_seconds)} seconds.`:lease?.owned_by_you?`You hold the replay controls for approximately ${number(lease.expires_in_seconds)} more seconds.`:'No visitor currently holds the replay controls. Starting a run acquires the available lease.' : '';
    if(context && !otherVisitor) {if(context.session_ready!==true) leaseMessage='The browser session is not ready. Reload this page to initialise replay controls.';else {if(context.cooldowns?.start>0) leaseMessage+=` The next replay can start in approximately ${number(context.cooldowns.start)} seconds.`;if(lease?.owned_by_you && context.cooldowns?.restart>0) leaseMessage+=` Broker restart is available in approximately ${number(context.cooldowns.restart)} seconds.`;}}
    const scope=context ? 'The broker and both clients run inside this server. Controls and recent receipts are shared between visitors. Run counters describe the shared current run.' : 'The broker and both clients run inside this application host. Run counters describe the current replay.';
    const retention=context ? context.retention?.description || 'Replay history is limited and can reset when this runtime restarts.' : 'Persisted totals span retained runs.';
    $('replay-status').innerHTML=`<div class="status-line"><span class="small-dot" style="background:${active?'#7a9e6a':'#aaa'}"></span><strong>${esc(heading)}</strong></div>${context?`<p class="lease-message">${esc(leaseMessage)}</p>`:''}${kvHTML({broker:status.broker,subscriber:status.subscriber,run_published:status.current_run?.published ?? status.published,run_newly_accepted:status.current_run?.accepted ?? status.accepted,run_duplicate_deliveries:status.current_run?.duplicates ?? status.duplicates,run_rejected:status.current_run?.rejected ?? status.rejected,retained_unique_events:status.persistent_unique_events,retained_delivery_count:status.persistent_delivery_count,cumulative_deliveries_this_runtime:status.total_delivery_count,broker_restarts:status.broker_restarts || 0,last_error:status.last_error})}<p class="subtle-note" style="margin-top:12px">${esc(status.protocol || 'MQTT')} · QoS ${esc(status.qos ?? 1)}<br>Broker authentication is enabled. ${esc(scope)} ${esc(retention)}</p>`;
    if(state.sessionError) $('replay-status').innerHTML+=`<p class="error-hint">${esc(state.sessionError)} Reload to establish a browser session before using replay controls.</p>`;
    const events=asArray(payload,['events','items','data']);setText('event-count',`${number(events.length)} recent events`);
    const display=events.slice(0,60).map(e=>({channel:e.channel_id,observed_historical_utc:typeof e.observed_at==='number'?new Date(e.observed_at*1000).toISOString():e.observed_at,received_utc:e.received_at,value:e.value,unit:unitName(e.unit),ifc_space_guid:e.room_guid,event_id:e.event_id?`${e.event_id.slice(0,12)}…`:e.id}));
    $('replay-events').innerHTML=display.length?simpleTable(display):'<p class="empty-state">No retained transport events are recorded. Start a bounded historical replay to inspect the pipeline.</p>';
  }catch(err){
    if(request!==state.replayRequest) return;
    for(const action of ['start','stop','reconnect']) $('replay-'+action).disabled=true;
    $('replay-status').innerHTML=`<span class="error-hint">Transport status unavailable. ${esc(err.message)}</span>`;
  }
}
async function testReconnection(){
  if($('replay-reconnect').disabled || state.replayPending) return;
  state.replayPending=true;updateReplayControls();setText('replay-status','Restarting the MQTT broker and waiting for the authenticated subscriber to reconnect…');
  try{await api('/api/replay/restart-broker',{method:'POST',body:JSON.stringify({})});notice('The MQTT broker restarted and the subscriber reconnected. Source observations remain historical.');}
  catch(err){notice(`The reconnection test could not complete. ${err.message}`,true);}
  finally{state.replayPending=false;await refreshReplay();}
}
async function replayAction(action){
  if(!['start','stop'].includes(action) || $('replay-'+action).disabled || state.replayPending) return;
  const params=replayParameters();
  if(action==='start' && !params.valid) {notice(params.message,true);updateReplayControls();return;}
  state.replayPending=true;updateReplayControls();
  try{await api(`/api/replay/${action}`,{method:'POST',body:JSON.stringify(action==='start'?{speed:params.speed,limit:params.limit}:{})});notice('');}
  catch(err){notice(`The historical replay could not ${action}. ${err.message}`,true);}
  finally{state.replayPending=false;await refreshReplay();}
}
function sourceLinks(obj){const urls=new Map();function walk(v,path=''){if(typeof v==='string' && /^https?:\/\//.test(v))urls.set(v,path);else if(v && typeof v==='object')for(const [k,x] of Object.entries(v))walk(x,k);}walk(obj);return [...urls].map(([url,label])=>`<a class="chip-link" href="${esc(safeURL(url))}" target="_blank" rel="noopener">${esc(readable(label) || new URL(url).hostname)} ↗</a>`).join('');}
async function renderEvidence(){
  const s=state.summary;
  const [provenanceResult,graphResult]=await Promise.allSettled([api('/api/provenance'),api('/api/graph')]);
  const provenance=provenanceResult.status==='fulfilled'?provenanceResult.value:{};
  const graph=graphResult.status==='fulfilled'?graphResult.value:null;
  const source={title:s.source_title || 'Open Smart Home Data Set',licence:s.licence,source_repository:s.source_url,source_release_doi:provenance.source_release_doi,source_commit:s.source_commit,model_sha256:s.model_sha256,timestamp_basis:s.timestamp_basis,mapping_scope:s.mapping_scope};
  let html=`<section class="card source-card"><p class="eyebrow">PUBLISHED DATASET</p><h3>${esc(source.title)}</h3><p>The publisher provides an apartment dataset combining IFC spaces, semantic metadata and historical sensor observations. The application checks source consistency; it does not independently establish as-built accuracy. This workbench preserves the source identifiers and file hashes.</p><div class="source-links">${sourceLinks(source)}<a class="chip-link" href="${esc(safeURL(provenance.licence_url || 'https://creativecommons.org/licenses/by-sa/4.0/'))}" target="_blank" rel="noopener">${esc(s.licence || 'Source licence')} ↗</a><a class="chip-link" href="/api/model/source.ifc" target="_blank">Download source IFC ↗</a><a class="chip-link" href="/api/provenance" target="_blank">Complete provenance ↗</a></div>${kvHTML(source,['source_repository','source_release_doi','title'])}</section>`;
  html+=`<section class="card source-card"><h3>Spatial and measurement integrity</h3><div class="data-grid" style="margin-top:18px"><div><p class="eyebrow">GEOMETRY</p><p>${state.geometry?.counts?.space_meshes || state.roomMeshes.size} source IFC spaces. ${state.physicalMeshes.length} physical building element meshes. Geometry is displayed in metres.</p></div><div><p class="eyebrow">SOURCE TYPES</p><p>${number(s.channel_kinds?.measured_sensor)} sensor channels, ${number(s.channel_kinds?.controller_setpoint)} controller event channels and ${number(s.channel_kinds?.external_virtual_weather)} repeated external weather files are kept distinct.</p></div><div><p class="eyebrow">MAPPING</p><p>Only verified source relationships connect channels to rooms. One CSV-only thermostat channel remains unresolved. Sensor coordinates are unavailable.</p></div></div>${state.geometry?.source?kvHTML(state.geometry.source):''}${jsonDetails(s,'Inspect project metadata and source references')}${jsonDetails(provenance,'Inspect the complete upstream provenance')}</section>`;
  if(graph){state.graphTemplates=graph.templates;html+=`<section class="card source-card" id="graph-workbench"><p class="eyebrow">PERSISTENT RDF / NOSQL DATABASE</p><h3>Query the semantic connection.</h3><p>Run a fixed, read-only SPARQL query over the original source graphs and the separate derived IFC identity graph.</p>${kvHTML(graph.summary || {},['named_graphs','source_commit','assertion_policy'])}<div class="graph-controls"><label>Evidence query<select id="graph-template">${Object.keys(graph.templates || {}).map(k=>`<option value="${esc(k)}">${esc(readable(k))}</option>`).join('')}</select></label><button class="button" id="graph-run">Run SPARQL query</button></div><div id="graph-result"><p class="subtle-note">Choose a query to inspect the stored graph relationships.</p></div></section>`;}
  $('evidence-content').className='';$('evidence-content').innerHTML=html;
  if(graph){$('graph-run').addEventListener('click',runGraphQuery);await runGraphQuery();}
}
async function runGraphQuery(){
  const host=$('graph-result'),button=$('graph-run');if(!host)return;button.disabled=true;host.innerHTML='<p class="subtle-note">Running the fixed, read-only graph query…</p>';
  try{const result=await api(`/api/graph/query?template=${encodeURIComponent($('graph-template').value)}`);host.innerHTML=`<p class="subtle-note">${number(result.rows?.length)} result row${result.rows?.length===1?'':'s'} · ${esc(result.engine || 'Persistent RDF graph database')}</p>${simpleTable(result.rows || [],result.columns)}<details class="json-toggle"><summary>Inspect the executed SPARQL query</summary><pre>${esc(result.sparql)}</pre></details>`;}catch(err){host.innerHTML=`<div class="notice error">The graph query could not be run. ${esc(err.message)}</div>`;}finally{button.disabled=false;}
}

function setupEvents(){document.querySelectorAll('.nav-item').forEach(btn=>btn.addEventListener('click',()=>switchView(btn.dataset.view)));document.querySelector('.brand').addEventListener('click',()=>switchView('twin'));$('room-list').addEventListener('click',e=>{const btn=e.target.closest('[data-room-guid]');if(btn)selectRoom(btn.dataset.roomGuid);});$('mapping-register').addEventListener('click',e=>{const btn=e.target.closest('[data-evidence-guid]');if(btn)inspectEvidence(btn.dataset.evidenceGuid);});$('channel-select').addEventListener('change',e=>selectChannel(e.target.value));$('apply-range').addEventListener('click',loadSeries);$('download-series').addEventListener('click',downloadSeries);$('chart-cursor').addEventListener('input',e=>updateCursor(e.target.value));$('timeseries-chart').addEventListener('pointermove',chartPointer);$('timeseries-chart').addEventListener('click',chartPointer);$('timeseries-chart').addEventListener('keydown',e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();updateCursor(state.cursor+(e.key==='ArrowLeft'?-1:1));}});$('model-reset').addEventListener('click',()=>resetModel());$('view-perspective').addEventListener('click',()=>resetModel());$('view-top').addEventListener('click',()=>resetModel(true));$('model-shell').addEventListener('click',()=>{state.shellVisible=!state.shellVisible;state.physicalMeshes.forEach(mesh=>mesh.visible=state.shellVisible);$('model-shell').setAttribute('aria-pressed',String(state.shellVisible));});$('room-evidence-button').addEventListener('click',()=>state.selectedRoom && inspectEvidence(roomGuid(state.selectedRoom)));$('close-evidence').addEventListener('click',()=>$('evidence-dialog').close());$('evidence-dialog').addEventListener('click',e=>{if(e.target===$('evidence-dialog')){const b=$('evidence-dialog').getBoundingClientRect();if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom)$('evidence-dialog').close();}});$('replay-speed').addEventListener('change',updateReplayControls);$('replay-limit').addEventListener('input',updateReplayControls);$('replay-start').addEventListener('click',()=>replayAction('start'));$('replay-stop').addEventListener('click',()=>replayAction('stop'));$('replay-reconnect').addEventListener('click',testReconnection);$('theme-toggle').addEventListener('click',()=>{document.body.classList.toggle('theme-dark');localStorage.setItem('connected-building-theme',document.body.classList.contains('theme-dark')?'dark':'light');updateModelTheme();renderChart();});window.addEventListener('resize',()=>{if(state.view==='twin')renderChart();});window.addEventListener('hashchange',()=>switchView(location.hash.slice(1)));}
async function main(){if(localStorage.getItem('connected-building-theme')==='dark')document.body.classList.add('theme-dark');setupEvents();await initialiseSession();try{const results=await Promise.allSettled([api('/api/summary'),api('/api/rooms'),api('/api/channels?include_unverified=true')]);if(results[0].status==='fulfilled')state.summary=results[0].value;if(results[1].status==='fulfilled')state.rooms=asArray(results[1].value,['rooms','spaces','items','data']);else throw results[1].reason;if(results[2].status==='fulfilled')state.channels=asArray(results[2].value,['channels','items','data']);else throw results[2].reason;renderStats();renderRooms();initialiseModel();const first=state.rooms.find(r=>monitored(r)) || state.rooms[0];if(first)await selectRoom(roomGuid(first));await switchView(location.hash.slice(1) || 'twin');if(results[0].status==='rejected')notice('The source summary could not be loaded. Room and measurement data remain available.',true);}catch(err){notice(`The building service is not available. ${err.message}`,true);$('room-list').innerHTML='<p class="empty-state">The verified room register could not be loaded.</p>';$('model-loading').innerHTML='<p>The building service is not available.<br>Please retry after the service starts, or inspect its deployment status.</p>';}}
main();
