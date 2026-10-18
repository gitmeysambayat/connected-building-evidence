import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

const root=new URL('../',import.meta.url);
const app=(await readFile(new URL('static/app.js',root),'utf8')).replace(/^import .*\r?\n/gm,'').replace(/\nmain\(\);\s*$/,'\n');
const rooms=JSON.parse(await readFile(new URL('pages/seed/rooms.json',root),'utf8'));
const channels=JSON.parse(await readFile(new URL('pages/seed/channels.json',root),'utf8'));
const json=(payload,status=200)=>new Response(JSON.stringify(payload),{status,headers:{'Content-Type':'application/json'}});
const session=(options={})=>({public_demo:true,session_ready:true,
 capabilities:{max_events:300,max_duration_seconds:30,max_events_per_second:100,lease_seconds:45,start_cooldown_seconds:20},
 lease:{active:false,owned_by_you:false,expires_in_seconds:0,replay_running:false},
 controls:{can_start:true,can_stop:false,can_restart_broker:false},
 retention:{delivery_rows:10000,state_persistence:'ephemeral',description:'Only the newest 10,000 delivery rows are retained; accepted-event deduplication lasts until this runtime is reset.'},...options});
const status=metadata=>({broker:'running',subscriber:'connected',replay:'idle',current_run:{published:0,accepted:0,duplicates:0,rejected:0},persistent_unique_events:0,persistent_delivery_count:0,...(metadata?{public_demo:metadata}:{})});
function harness(request){
 const elements=new Map(),requests=[];
 const element=id=>{
  if(!elements.has(id)){
   const classes=new Set(),attributes=new Map();let html='',text='';
   elements.set(id,{value:'',disabled:false,title:'',href:'',clientWidth:900,clientHeight:227,
    get textContent(){return text;},set textContent(value){text=String(value);html=text;},
    get innerHTML(){return html;},set innerHTML(value){html=String(value);text=html.replace(/<[^>]*>/g,'');},
    classList:{add(name){classes.add(name);},remove(name){classes.delete(name);},toggle(name,on){const include=on===undefined?!classes.has(name):on;include?classes.add(name):classes.delete(name);},contains(name){return classes.has(name);}},
    setAttribute(name,value){attributes.set(name,String(value));},getAttribute(name){return attributes.get(name)??null;},removeAttribute(name){attributes.delete(name);},scrollIntoView(){},
   });
  }
  return elements.get(id);
 };
 element('replay-speed').value='20';element('replay-limit').value='100';
 const context={THREE:{Raycaster:class{},Vector2:class{}},OrbitControls:class{},URL,URLSearchParams,
  document:{getElementById:element,body:element('body')},location:{href:'https://building.example/'},
  getComputedStyle:()=>({getPropertyValue:()=>'#56785a'}),console,
  fetch:async(path,options)=>{requests.push({path,options});return request(path,options);},
 };
 runInNewContext(`${app}\nglobalThis.ui={state,initialiseSession,refreshReplay,replayAction,testReconnection,selectRoom,downloadSeries,updateReplayControls};`,context);
 context.ui.state.rooms=rooms;context.ui.state.channels=channels;
 return {...context.ui,element,requests};
}

test('A shared replay lease disables another visitor controls and explains runtime retention',async()=>{
 const occupied=session({lease:{active:true,owned_by_you:false,expires_in_seconds:24},controls:{can_start:false,can_stop:false,can_restart_broker:false}});
 const h=harness(path=>json(path==='/api/session'?occupied:path==='/api/replay/status'?{...status(occupied),persistent_delivery_count:10000,total_delivery_count:12000}:{events:[]}));
 await h.initialiseSession();await h.refreshReplay();
 for(const action of ['start','stop','reconnect'])assert.equal(h.element('replay-'+action).disabled,true);
 assert.match(h.element('replay-status').textContent,/another visitor/);assert.match(h.element('replay-status').textContent,/24 seconds/);
 assert.match(h.element('replay-status').textContent,/10,000 delivery rows/);assert.match(h.element('replay-status').textContent,/retained delivery count/);assert.match(h.element('replay-status').textContent,/cumulative deliveries this runtime/);assert.match(h.element('replay-status').textContent,/12,000/);assert.equal((h.element('replay-status').textContent.match(/deduplication lasts/g)||[]).length,1);assert.match(h.element('runtime-profile-description').textContent,/sleep when idle/);assert.match(h.element('runtime-profile-description').textContent,/history can reset/);
 assert.match(h.element('replay-limits').textContent,/300 events, 30 seconds and 100 events\/s/);
 await h.replayAction('start');await h.testReconnection();assert.ok(h.requests.every(r=>!r.options.method));
 assert.ok(h.requests.every(r=>r.options.credentials==='same-origin'));
});

test('A rejected stop cannot re-enable controls after ownership moves to another visitor',async()=>{
 const owned=session({lease:{active:true,owned_by_you:true,expires_in_seconds:30},controls:{can_start:false,can_stop:true,can_restart_broker:false}});
 const foreign=session({lease:{active:true,owned_by_you:false,expires_in_seconds:20},controls:{can_start:false,can_stop:false,can_restart_broker:false}});
 let metadata=owned;
 const h=harness((path,options)=>{
  if(path==='/api/session')return json(owned);
  if(options.method==='POST'){metadata=foreign;return json({detail:'Replay belongs to another visitor'},403);}
  return json(path==='/api/replay/status'?{...status(metadata),replay:'running'}:{events:[]});
 });
 await h.initialiseSession();await h.refreshReplay();assert.equal(h.element('replay-stop').disabled,false);
 await h.replayAction('stop');
 for(const action of ['start','stop','reconnect'])assert.equal(h.element('replay-'+action).disabled,true);
 assert.match(h.element('global-notice').textContent,/could not stop/);assert.match(h.element('replay-status').textContent,/another visitor/);
 const post=h.requests.find(r=>r.options.method==='POST');assert.equal(post.path,'/api/replay/stop');assert.equal(post.options.body,'{}');assert.equal(post.options.credentials,'same-origin');assert.equal(post.options.headers['Content-Type'],'application/json');assert.equal(post.options.headers.Authorization,undefined);assert.equal(post.options.headers.Origin,undefined,'The browser supplies Origin; JavaScript does not forge it');
});

test('Replay count and speed obey the server-advertised duration and batch limits',async()=>{
 const available=session();const h=harness(path=>json(path==='/api/session'?available:path==='/api/replay/status'?status(available):{events:[]}));
 await h.initialiseSession();await h.refreshReplay();assert.equal(h.element('replay-start').disabled,false);
 h.element('replay-speed').value='1';h.updateReplayControls();assert.equal(h.element('replay-limit').max,'30');assert.equal(h.element('replay-start').disabled,true);assert.match(h.element('replay-validation').textContent,/at most 30 events/);
 h.element('replay-limit').value='30';h.updateReplayControls();assert.equal(h.element('replay-start').disabled,false);
 h.element('replay-speed').value='100';h.element('replay-limit').value='300';h.updateReplayControls();assert.equal(h.element('replay-start').disabled,false);
 h.element('replay-limit').value='301';h.updateReplayControls();assert.equal(h.element('replay-start').disabled,true);
 h.element('replay-limit').value='1.5';h.updateReplayControls();assert.equal(h.element('replay-start').disabled,true);
});

test('Server cooldown and session readiness explain disabled controls without inferring ownership',async()=>{
 const cooling=session({lease:{active:true,owned_by_you:true,expires_in_seconds:12},controls:{can_start:false,can_stop:false,can_restart_broker:false},cooldowns:{start:8,restart:40}});
 let metadata=cooling;
 const h=harness(path=>json(path==='/api/session'?cooling:path==='/api/replay/status'?status(metadata):{events:[]}));
 await h.initialiseSession();await h.refreshReplay();assert.match(h.element('replay-status').textContent,/next replay can start in approximately 8 seconds/);assert.match(h.element('replay-status').textContent,/Broker restart is available in approximately 40 seconds/);assert.equal(h.element('replay-start').disabled,true);
 metadata=session({session_ready:false});await h.refreshReplay();assert.match(h.element('replay-status').textContent,/browser session is not ready/);assert.equal(h.element('replay-start').disabled,true);
});

test('Local servers without the new metadata retain normal replay and recovery controls',async()=>{
 let running=false;const h=harness(path=>path==='/api/session'?json({detail:'Not Found'},404):json(path==='/api/replay/status'?{...status(),replay:running?'running':'idle'}:{events:[]}));
 await h.initialiseSession();await h.refreshReplay();assert.equal(h.element('runtime-profile').classList.contains('hidden'),true);assert.equal(h.element('replay-start').disabled,false);assert.equal(h.element('replay-reconnect').disabled,false);assert.equal(h.element('replay-stop').disabled,true);assert.equal(h.element('replay-limit').max,'2000');
 running=true;await h.refreshReplay();assert.equal(h.element('replay-start').disabled,true);assert.equal(h.element('replay-stop').disabled,false);assert.equal(h.element('replay-reconnect').disabled,true);
});

test('Failed session establishment or failed status refresh leaves mutation controls disabled',async()=>{
 let failStatus=false;const h=harness(path=>path==='/api/session'?json({detail:'Unavailable'},503):path==='/api/replay/status'&&failStatus?json({detail:'Unavailable'},503):json(path==='/api/replay/status'?status(session()):{events:[]}));
 await h.initialiseSession();await h.refreshReplay();for(const action of ['start','stop','reconnect'])assert.equal(h.element('replay-'+action).disabled,true);assert.match(h.element('replay-status').textContent,/Reload to establish a browser session/);
 h.state.sessionError=null;h.state.session=session();failStatus=true;await h.refreshReplay();for(const action of ['start','stop','reconnect'])assert.equal(h.element('replay-'+action).disabled,true);assert.match(h.element('replay-status').textContent,/Transport status unavailable/);
});

const selectionIds=['chart-channel','chart-resolution','series-start','series-end','reading-label','reading-value','reading-unit','reading-time','reading-timezone','reading-count','plot-method','series-provenance','timeseries-chart','global-notice'];
const visible=h=>JSON.stringify(selectionIds.map(id=>{const el=h.element(id);return {id,text:el.textContent,value:el.value,disabled:el.disabled,title:el.title};}));
function assertUnmonitored(h){assert.equal(h.state.selectedRoom.room_id,'Staircase');assert.equal(h.state.selectedChannel,null);assert.equal(h.state.points.length,0);assert.equal(h.element('chart-resolution').textContent,'No verified observations');assert.equal(h.element('series-start').value,'');assert.equal(h.element('series-end').value,'');assert.equal(h.element('series-start').disabled,true);assert.equal(h.element('reading-timezone').textContent,'Not applicable');assert.equal(h.element('reading-unit').textContent,'');assert.equal(h.element('download-series').href,'#');assert.equal(h.element('download-series').getAttribute('aria-disabled'),'true');}

test('Late success and failure cannot attach a previous room series to an unmonitored space',async()=>{
 const csv=(await readFile(new URL('data/source/Measurements/Room1_Temperature.csv',root),'utf8')).trim().split(/\r?\n/).slice(0,2).map(line=>line.split('\t').map(Number));
 const payload={total:10598,returned:2,unit:'°C',readings:csv.map(([epoch,value],i)=>({timestamp:new Date(epoch*1000).toISOString(),value,source_line:i+1})),gap_rule:{enabled:true,threshold_seconds:3600}};
 for(const outcome of ['success','failure']){
  let finish,fail;const waiting=new Promise((resolve,reject)=>{finish=resolve;fail=reject;});
  const h=harness(()=>waiting);const room=h.state.rooms.find(r=>r.room_id==='Room1'),stair=h.state.rooms.find(r=>r.room_id==='Staircase');
  const pending=h.selectRoom(room.guid);await h.selectRoom(stair.guid);assertUnmonitored(h);const before=visible(h);
  if(outcome==='success')finish(json(payload));else fail(new Error('Delayed original room response failed'));
  await pending;assertUnmonitored(h);assert.equal(visible(h),before);let cancelled=false;h.downloadSeries({preventDefault(){cancelled=true;}});assert.equal(cancelled,true,'An unavailable source export must not start an anchor download');
 }
});

test('Full UI keeps genuine service routes and uses a versioned module with initially disabled replay buttons',async()=>{
 const html=await readFile(new URL('static/index.html',root),'utf8');assert.match(html,/\/static\/app\.js\?v=20261018-cloud-1/);
 for(const id of ['replay-start','replay-stop','replay-reconnect'])assert.match(html,new RegExp(`id="${id}" disabled`));
 assert.match(app,/\/api\/graph\/query\?template=/);assert.match(app,/\/api\/replay\/restart-broker/);assert.doesNotMatch(app,/chatgpt\.site|data-adapter\.js/);
 assert.doesNotMatch(html,/This local transport is not a cloud deployment/);
});
