import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const pages=new URL('../',import.meta.url);
const requested=[];
globalThis.fetch=async(input,options)=>{
 const url=new URL(input);requested.push({url,options});
 assert.equal(url.protocol,'file:');assert.ok(url.href.startsWith(pages.href),'Every asset stays beneath the project path');
 try{return new Response(await readFile(fileURLToPath(url)),{status:200});}catch{return new Response('missing',{status:404});}
};
const adapter=await import('../static/data-adapter.js');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const readJSON=async path=>JSON.parse(await readFile(new URL(path,pages),'utf8'));

test('Pinned published manifest verifies all 62 preserved source and export bytes',async()=>{
 const manifest=await readJSON('evidence/20261018_v1_Pages_Asset_Manifest.json');
 assert.equal(manifest.files.length,62);
 for(const item of manifest.files){const bytes=await readFile(new URL(item.path,pages));assert.equal(bytes.length,item.bytes,item.path);assert.equal(hash(bytes),item.sha256,item.path);}
 const previous=await readJSON('seed/seed-export-manifest.json');
 for(const item of previous.files)assert.equal(hash(await readFile(new URL(`seed/${item.path}`,pages))),item.sha256,`Exact original export: ${item.path}`);
});
test('All 37 original channels and source IFC pass browser-adapter hash and row checks',async()=>{
 const result=await adapter.verifyAllSources();assert.equal(result.channels,37);assert.equal(result.observations,285954);assert.equal(result.ifc_verified,true);assert.equal(result.verified_channels,37);
});
test('Catalogue preserves nine source spaces, six monitored rooms and one excluded thermostat',async()=>{
 const {rooms,channels}=await adapter.catalogue();assert.equal(rooms.length,9);assert.equal(channels.length,37);assert.equal(channels.filter(c=>c.mapping_verified).length,36);
 assert.equal(new Set(channels.filter(c=>c.mapping_verified && c.source_kind==='measured_sensor').map(c=>c.room_guid)).size,6);
 const excluded=channels.find(c=>!c.mapping_verified);assert.equal(excluded.id,'Toilet_ThermostatTemperature');assert.equal(excluded.room_guid,null);assert.ok(excluded.candidate_room_guid);
 assert.equal(channels.filter(c=>c.source_kind==='controller_setpoint').length,6);assert.equal(channels.filter(c=>c.source_kind==='external_virtual_weather').length,6);
});
test('Series rows preserve source values, epochs and one-based source lines',async()=>{
 const source=(await readFile(new URL('seed/Measurements/Room2_Temperature.csv',pages),'utf8')).trim().split(/\r?\n/).map(line=>line.split('\t').map(Number));
 const result=await adapter.series('Room2_Temperature',new URLSearchParams({limit:'1600'}));assert.equal(result.total,source.length);assert.equal(result.returned,1600);assert.equal(result.readings[0].source_line,1);assert.equal(result.readings.at(-1).source_line,source.length);
 let previousIndex=null;
 for(const row of result.readings){const index=row.source_line-1;assert.equal(row.epoch,source[index][0]);assert.equal(row.value,source[index][1]);assert.equal(Date.parse(row.timestamp)/1000,row.epoch);if(previousIndex!==null){let maximum=0;for(let i=previousIndex+1;i<=index;i++)maximum=Math.max(maximum,source[i][0]-source[i-1][0]);assert.equal(row.gap_before_seconds,maximum);}previousIndex=index;}
});
test('Display gap metadata describes original gaps, rather than sampled-row distances',()=>{
 const channel={id:'test',source_kind:'measured_sensor'};
 const rows=[0,600,1200,1800,2400,3000,9000,9600,10200,10800,11400].map((epoch,i)=>({epoch,value:i,source_line:i+1,timestamp:new Date(epoch*1000).toISOString()}));
 const result=adapter.selectRows(rows,channel,new URLSearchParams({limit:'3'}));assert.deepEqual(result.readings.map(r=>r.gap_before_seconds),[null,600,6000]);
 const commands=adapter.selectRows(rows,{...channel,source_kind:'controller_setpoint'},new URLSearchParams({limit:'3'}));assert.equal(commands.gap_rule.enabled,false);assert.ok(commands.readings.every(r=>r.gap_before_seconds===null));
});
test('UTC range filtering is inclusive and exact, complete CSV contains every original row',async()=>{
 const all=await adapter.series('Room1_Temperature',new URLSearchParams({limit:'1'}));assert.equal(all.returned,1);assert.equal(all.readings[0].source_line,1);
 const selected=await adapter.series('Room1_Temperature',new URLSearchParams({start:'2017-04-01T00:00:00Z',end:'2017-04-02T23:59:59Z',limit:'10000'}));
 const source=(await readFile(new URL('seed/Measurements/Room1_Temperature.csv',pages),'utf8')).trim().split(/\r?\n/).map((line,i)=>({values:line.split('\t').map(Number),line:i+1})).filter(r=>r.values[0]>=Date.parse('2017-04-01T00:00:00Z')/1000 && r.values[0]<=Date.parse('2017-04-02T23:59:59Z')/1000);
 assert.equal(selected.total,source.length);assert.deepEqual(selected.readings.map(r=>r.source_line),source.map(r=>r.line));
 const csv=await adapter.exportCSV('Room1_Temperature');assert.equal(csv.total,10598);const lines=csv.csv.trimEnd().split('\r\n');assert.equal(lines.length,10599);assert.equal(lines[0],'timestamp,epoch,value,unit,channel_id,room_guid,source_line,source_sha256,model_sha256,source_kind');
 assert.ok(lines[1].includes('"°C"'));assert.ok(lines[1].includes('"05i4VutGDCsQKCrT6CQvhu"'));assert.ok(lines.at(-1).includes('"10598"'));
});
test('Bounds, mutations and invented identifiers are rejected',async()=>{
 for(const params of [{start:'2017-04-02T00:00:00Z',end:'2017-04-01T00:00:00Z'},{limit:'0'},{limit:'1.5'},{limit:'10001'},{start:'2017-04-01T00:00:00'}])await assert.rejects(adapter.series('Room1_Temperature',new URLSearchParams(params)));
 await assert.rejects(adapter.series('Invented_Temperature'));await assert.rejects(adapter.dataset('/api/replay/start',{method:'POST'}));await assert.rejects(adapter.dataset('/graphql'));
 assert.throws(()=>adapter.sourceURL('../secret'));assert.throws(()=>adapter.sourceURL('/static/elsewhere'));
});
test('Archived SPARQL results and geometry are served with their original provenance',async()=>{
 const geometry=await adapter.dataset('/api/geometry');assert.equal(geometry.source.ifc_sha256,'42ffba5ac38db6def62df2c1ea000dce1d2139dc0100e16e1ede8ee0562fef03');assert.equal(geometry.meshes.filter(m=>m.kind==='space').length,9);assert.equal(geometry.meshes.filter(m=>m.kind!=='space').length,38);
 for(const [template,count] of [['room_alignment',9],['verified_channels',36],['unverified_channels',1]])assert.equal((await adapter.dataset(`/api/graph/query?template=${template}`)).rows.length,count);
 const a=await adapter.dataset('/api/analytics');assert.equal(a.rooms.length,6);assert.ok(a.rooms.every(r=>r.improvement_percent<0));
});
test('Web assets have no server API links, cloud ingestion controls or absolute-root assets',async()=>{
 const html=await readFile(new URL('index.html',pages),'utf8');const js=await readFile(new URL('static/app.js',pages),'utf8');
 assert.match(html,/READ-ONLY PUBLISHED DATA EXPLORER/);assert.doesNotMatch(html,/href="\/|src="\/|view-transport|replay-start|chatgpt\.site/i);
 assert.doesNotMatch(js,/href="\/api|fetch\(|replayAction|Cloud HTTPS|D1 for|R2 for/);
 for(const {url,options} of requested){assert.equal(options.credentials,'omit');assert.ok(!url.pathname.includes('/api/'));}
});

test('A modified source file is rejected before rows can be used',async()=>{
 const originalFetch=globalThis.fetch;
 const isolated=await import('../static/data-adapter.js?tampered-source');
 await isolated.catalogue();
 globalThis.fetch=async(input,options)=>{
  const response=await originalFetch(input,options);
  if(new URL(input).pathname.endsWith('/Measurements/Room1_Temperature.csv')){const bytes=new Uint8Array(await response.arrayBuffer());bytes[0]=bytes[0]===49?50:49;return new Response(bytes,{status:200});}
  return response;
 };
 try{await assert.rejects(isolated.series('Room1_Temperature'),/SHA-256 does not match/);}finally{globalThis.fetch=originalFetch;}
});
test('A modified published manifest is rejected before catalogue access',async()=>{
 const originalFetch=globalThis.fetch;
 const isolated=await import('../static/data-adapter.js?tampered-manifest');
 globalThis.fetch=async(input,options)=>{
  const response=await originalFetch(input,options);
  if(new URL(input).pathname.endsWith('/20261018_v1_Pages_Asset_Manifest.json')){const bytes=new Uint8Array(await response.arrayBuffer());bytes[0]=32;return new Response(bytes,{status:200});}
  return response;
 };
 try{await assert.rejects(isolated.catalogue(),/SHA-256 does not match/);}finally{globalThis.fetch=originalFetch;}
});
