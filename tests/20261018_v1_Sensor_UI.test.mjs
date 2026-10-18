import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

const app = (await readFile(new URL('../static/app.js', import.meta.url), 'utf8')).replace(/^import .*\r?\n/gm, '').replace(/\nmain\(\);\s*$/, '\n');
const now = Date.parse('2026-10-18T12:00:00Z');
const reading = (extra = {}) => ({receipt_id:1, observed_at:new Date(now).toISOString(), received_at:new Date(now + 1000).toISOString(), temperature_c:21.45, relative_humidity_pct:47.2, source:'authenticated_device_report', ...extra});
const status = (latest = null, extra = {}) => ({enabled:true, state:latest ? 'reporting' : 'awaiting_reading', latest, count:latest ? 1 : 0, station_label:'Environmental station', model_assignment:'unassigned', server_time:new Date(now + 2000).toISOString(), stale_after_seconds:180, state_persistence:'ephemeral', retention:{max_rows:10080,max_age_days:7}, ...extra});
const json = (value, code = 200) => new Response(JSON.stringify(value), {status:code,headers:{'Content-Type':'application/json'}});

function harness(request) {
  const elements = new Map(), requests = [], timers = new Map(), intervals = new Map();
  let serial = 0, elapsed = 0;
  const element = id => {
    if (!elements.has(id)) {
      const classes = new Set(), attributes = new Map();
      elements.set(id, {textContent:'',innerHTML:'',className:'',disabled:false,href:'',
        classList:{toggle(name, on) {const active = on === undefined ? !classes.has(name) : on;active ? classes.add(name) : classes.delete(name);},contains:name=>classes.has(name)},
        setAttribute:(key,value)=>attributes.set(key,String(value)),getAttribute:key=>attributes.get(key),removeAttribute:key=>attributes.delete(key),
      });
    }
    return elements.get(id);
  };
  const document = {hidden:false,getElementById:element,querySelectorAll:()=>[],body:element('body')};
  const context = {THREE:{Raycaster:class{},Vector2:class{}},OrbitControls:class{},document,URL,URLSearchParams,AbortController,
    performance:{now:()=>elapsed},location:{href:'https://building.example/'},history:{replaceState(){}},requestAnimationFrame(){},console,
    setTimeout(callback,delay){const id=++serial;timers.set(id,{callback,delay});return id;},clearTimeout:id=>timers.delete(id),
    setInterval(callback,delay){const id=++serial;intervals.set(id,{callback,delay});return id;},clearInterval:id=>intervals.delete(id),
    fetch:async(path, options) => {requests.push({path,options});return request(path,options);},
  };
  runInNewContext(`${app}\nglobalThis.ui={state,sensorState,refreshSensor,startSensorPolling,stopSensorPolling,switchView,renderSensorStatus,safeStationReadings,stationFreshness,exportSensorReadings};`, context);
  return {...context.ui,element,requests,document,timers,intervals,advance:milliseconds=>{elapsed+=milliseconds;}};
}

test('The station is dormant outside its selected visible view and empty responses create no readings', async () => {
  const h = harness(path => json(path.endsWith('/status') ? status(null,{enabled:false,state:'disabled'}) : {readings:[]}));
  h.startSensorPolling();await h.refreshSensor();assert.equal(h.requests.length,0);
  h.state.view='sensor';h.document.hidden=true;h.startSensorPolling();await h.refreshSensor();assert.equal(h.requests.length,0);
  h.document.hidden=false;await h.refreshSensor();
  assert.equal(h.element('sensor-status-title').textContent,'Awaiting first device reading');
  assert.equal(h.element('sensor-temperature').textContent,'·');assert.equal(h.element('sensor-humidity').textContent,'·');
  assert.equal(h.element('sensor-export').getAttribute('aria-disabled'),'true');
  assert.match(h.element('sensor-readings').innerHTML,/Awaiting first device reading/);
  assert.equal(h.state.rooms.length,0);assert.equal(Object.keys(h.state.summary).length,0);
  assert.equal(h.requests.length,2);assert.ok(h.requests.every(item=>!item.options.method && !item.options.headers.Authorization));
  assert.ok([...h.timers.values()].some(timer=>timer.delay===60000));
});

test('Freshness advances from the server clock and becomes stale even after a failed refresh', async () => {
  let fail=false;
  const row=reading();
  const h=harness(path=>fail?json({detail:'Unavailable'},503):json(path.endsWith('/status')?status(row):{readings:[row]}));
  h.state.view='sensor';await h.refreshSensor();
  assert.equal(h.element('sensor-freshness').textContent,'Recent report');
  assert.equal(h.element('sensor-temperature').textContent,'21.45');
  assert.equal(h.element('sensor-received').textContent,'2026-10-18 12:00:01');
  assert.equal(h.element('sensor-observed').textContent,'2026-10-18 12:00:00');
  assert.equal(h.element('sensor-age').textContent,'2 s');
  assert.equal(h.element('sensor-export').href,'/api/live-sensor/export.csv');
  assert.match(h.element('sensor-retention').textContent,/clears station history/);
  h.advance(181000);fail=true;await h.refreshSensor();
  assert.equal(h.element('sensor-status-title').textContent,'Status check unavailable');
  assert.equal(h.element('sensor-freshness').textContent,'Stale report');
  assert.equal(h.element('sensor-temperature').textContent,'21.45');
  assert.equal(h.element('sensor-age').textContent,'3 min 3 s');
  assert.equal(h.element('sensor-checked').textContent,'2026-10-18 12:00:02');
  assert.equal(h.sensorState.readings.length,1);
  assert.ok([...h.timers.values()].some(timer=>timer.delay===120000));
});

test('Leaving the station aborts both read requests and ignores their late responses', async () => {
  const pending=[];
  const h=harness((path,options)=>new Promise(resolve=>pending.push({resolve,path,signal:options.signal})));
  h.state.view='sensor';const refresh=h.refreshSensor();
  assert.equal(pending.length,2);h.stopSensorPolling();h.state.view='quality';
  assert.ok(pending.every(item=>item.signal.aborted));
  for(const item of pending)item.resolve(json(item.path.endsWith('/status')?status(reading()):{readings:[reading()]}));
  await refresh;
  assert.equal(h.sensorState.status,null);assert.equal(h.sensorState.readings.length,0);
  assert.equal(h.sensorState.timer,null);assert.equal(h.timers.size,0);
});

test('A failed status response aborts a still-pending history request before retrying', async () => {
  let historyAborted=false;
  const h=harness((path,options)=>path.endsWith('/status') ? json({detail:'Unavailable'},503) : new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>{historyAborted=true;reject(new DOMException('Request aborted','AbortError'));},{once:true});
  }));
  h.state.view='sensor';await h.refreshSensor();
  assert.equal(historyAborted,true);
  assert.ok(h.requests.every(request=>request.options.signal.aborted));
  assert.equal(h.sensorState.controller,null);assert.equal(h.sensorState.loading,false);
  assert.equal(h.element('sensor-status-title').textContent,'Status check unavailable');
  assert.equal(h.sensorState.status,null);assert.equal(h.sensorState.readings.length,0);
  assert.equal(h.timers.size,1);assert.ok([...h.timers.values()].every(timer=>timer.delay===120000));
});

test('An unsuccessful initial request never paints a live measurement or exposes a raw server error', async () => {
  const h=harness(()=>json({detail:'Private configuration details'},503));
  h.state.view='sensor';await h.refreshSensor();
  assert.equal(h.element('sensor-status-title').textContent,'Status check unavailable');
  assert.equal(h.element('sensor-freshness').textContent,'Status unavailable');
  assert.equal(h.element('sensor-temperature').textContent,'·');
  assert.doesNotMatch(h.element('sensor-notice').textContent,/Private configuration/);
  let cancelled=false;h.exportSensorReadings({preventDefault(){cancelled=true;}});assert.equal(cancelled,true);
  for(let i=0;i<5;i++)await h.refreshSensor();
  assert.ok([...h.timers.values()].some(timer=>timer.delay===300000));
});

test('Malformed or non-device records cannot become station history', async () => {
  const h=harness(()=>json({}));
  const result=h.safeStationReadings({readings:[reading(),reading({source:'historical_replay'}),reading({temperature_c:null}),reading({relative_humidity_pct:'47'}),reading({observed_at:'not a time'})]});
  assert.equal(result.length,1);
  const safe=h.safeStationReadings({readings:[reading({device_id:'private',boot_id:'private',location:'private',token:'secret'})]});
  assert.ok(!Object.keys(safe[0]).some(key=>['device_id','boot_id','location','token'].includes(key)));
  assert.equal(h.stationFreshness(status(reading({observed_at:'bad'})),now),'stale');
  assert.equal(h.stationFreshness(status(reading({observed_at:new Date(now+121000).toISOString()})),now),'stale');
  assert.throws(()=>h.safeStationReadings({}),/incomplete/);
});

test('Station navigation starts a single clock and switching away stops all station work', async () => {
  const h=harness(path=>json(path.endsWith('/status')?status():{readings:[]}));
  await h.switchView('sensor');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.intervals.size,1);assert.equal(h.requests.length,2);
  assert.match(h.element('data-scope').innerHTML,/Independent station/);
  assert.equal(h.element('historical-profile').classList.contains('hidden'),true);
  await h.switchView('sensor');assert.equal(h.intervals.size,1);assert.equal(h.requests.length,2);
  await h.switchView('quality');
  assert.equal(h.intervals.size,0);assert.equal(h.sensorState.timer,null);
  assert.equal(h.element('historical-profile').classList.contains('hidden'),false);
  assert.match(h.element('data-scope').innerHTML,/Historical data/);
});

test('Public station markup separates hardware reports from source rooms and retains data attribution', async () => {
  const html=await readFile(new URL('../static/index.html',import.meta.url),'utf8');
  assert.match(html,/id="view-sensor"/);assert.match(html,/data-view="sensor"/);
  assert.match(html,/No IFC room is assigned to this station/);
  assert.match(html,/Download retained readings/);
  assert.match(html,/10\.5281\/zenodo\.1244602/);assert.match(html,/creativecommons\.org\/licenses\/by-sa\/4\.0/);
  assert.doesNotMatch(html,/<input[^>]*(?:token|device.?id|location)/i);
});
