/** Static, read-only access to the pinned public dataset. MIT for this code.
 * Source observations and data-derived exports retain CC BY-SA 4.0.
 * No request is sent to a server API or simulated ingestion endpoint.
 */
const ROOT = new URL('../', import.meta.url);
const MANIFEST_PATH = 'evidence/20261018_v1_Pages_Asset_Manifest.json';
const MANIFEST_SHA256 = '7efdb8ac8153b75d10e8977477c9fccf81784a1a9f9d23114a03aaa883d82c0d';
const MODEL_SHA256 = '42ffba5ac38db6def62df2c1ea000dce1d2139dc0100e16e1ede8ee0562fef03';
const CACHE = new Map();
const verified = new Set();
const textDecoder = new TextDecoder('utf-8', {fatal:true});
const iso = epoch => new Date(epoch * 1000).toISOString().replace('.000Z','Z');
function assert(condition, message) { if (!condition) throw new Error(message); }
export function sourceURL(path) {
  assert(typeof path === 'string' && !path.includes('..') && !path.startsWith('/') && !path.includes('\\') && /^[A-Za-z0-9_./-]+$/.test(path), 'Invalid source path.');
  return new URL(`seed/${path}`, ROOT).href;
}
export async function sha256(bytes) {
  assert(globalThis.crypto?.subtle, 'SHA-256 verification requires HTTPS or localhost.');
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
}
async function readBytes(path, expectedHash, expectedBytes) {
  const response = await fetch(new URL(path, ROOT), {cache:'no-cache', credentials:'omit'});
  assert(response.ok, `Published file unavailable (${response.status}): ${path}`);
  const bytes = await response.arrayBuffer();
  assert(expectedBytes === undefined || bytes.byteLength === expectedBytes, `Byte count does not match: ${path}`);
  assert(await sha256(bytes) === expectedHash, `SHA-256 does not match: ${path}`);
  verified.add(path);
  return bytes;
}
function cached(key, loader) {if(!CACHE.has(key)) CACHE.set(key,loader()); return CACHE.get(key);}
async function manifest() {
  return cached('@manifest',async()=> {
    const result=JSON.parse(textDecoder.decode(await readBytes(MANIFEST_PATH, MANIFEST_SHA256)));
    assert(result.source_ifc_sha256===MODEL_SHA256, 'Manifest model identity does not match.');
    return result;
  });
}
async function asset(path, asBytes=false) {
  const result=await cached(path,async()=> {
    const entry=(await manifest()).files.find(item=>item.path===path);
    assert(entry, `File absent from the pinned manifest: ${path}`);
    return readBytes(path,entry.sha256,entry.bytes);
  });
  return asBytes?result:JSON.parse(textDecoder.decode(result));
}
const snapshot = name => asset(`seed/${name}.json`);
export async function catalogue() {
  return cached('@catalogue',async()=>{
    const [rooms,channels]=await Promise.all([snapshot('rooms'),snapshot('channels')]);
    assert(rooms.length===9 && channels.length===37,'Unexpected source catalogue size.');
    const roomIds=new Set(rooms.map(r=>r.guid));
    assert(roomIds.size===9 && rooms.every(r=>r.model_sha256===MODEL_SHA256),'IFC space identities do not match the model.');
    assert(new Set(channels.map(c=>c.id)).size===37,'Duplicate channel identifiers.');
    for(const c of channels){
      assert(c.model_sha256===MODEL_SHA256,`Channel model identity does not match: ${c.id}`);
      assert(/^Measurements\/[A-Za-z0-9_]+\.csv$/.test(c.source_csv),'Invalid measurement path.');
      const entry=(await manifest()).files.find(f=>f.path===`seed/${c.source_csv}`);
      assert(entry?.sha256===c.source_sha256,`Channel source hash differs from manifest: ${c.id}`);
      assert(c.mapping_verified?roomIds.has(c.room_guid):c.room_guid===null,`Unverified spatial association: ${c.id}`);
    }
    assert(channels.filter(c=>c.mapping_verified).length===36,'Unexpected verified mapping count.');
    return {rooms,channels};
  });
}
export function parseRows(text, channel) {
  assert(typeof text==='string','Source text is required.');
  const lines=text.split(/\r?\n/);
  const rows=[];
  for(let index=0;index<lines.length;index++){
    const line=lines[index];if(index===lines.length-1 && line==='')continue;
    const fields=line.split('\t');
    assert(fields.length===2 && fields.every(f=>f.trim()!==''),`Malformed source row ${index+1}: ${channel.id}`);
    const epoch=Number(fields[0]),value=Number(fields[1]);
    assert(Number.isSafeInteger(epoch) && Number.isFinite(value),`Invalid source value at row ${index+1}: ${channel.id}`);
    rows.push({epoch,value,source_line:index+1,timestamp:iso(epoch)});
  }
  assert(rows.length===channel.source_rows,`Source row count differs: ${channel.id}`);
  const times=rows.map(r=>r.epoch), first=Math.min(...times),last=Math.max(...times);
  assert(iso(first)===channel.first_timestamp && iso(last)===channel.last_timestamp,`Source time bounds differ: ${channel.id}`);
  return rows.sort((a,b)=>a.epoch-b.epoch || a.source_line-b.source_line);
}
async function sourceRows(channel){
  return cached(`@rows/${channel.id}`,async()=>parseRows(textDecoder.decode(await asset(`seed/${channel.source_csv}`,true)),channel));
}
export function parseBound(value) {
  if(value===null || value===undefined || value==='')return null;
  assert(typeof value==='string' || typeof value==='number','Time bounds must be scalar timestamps.');
  if(typeof value==='number' || /^\d+$/.test(value)){
    const result=Number(value);assert(Number.isSafeInteger(result),'Invalid Unix time bound.');return result;
  }
  assert(/(?:Z|[+-]\d\d:\d\d)$/.test(value),'A UTC or explicit-offset timestamp is required.');
  const result=Date.parse(value)/1000;
  assert(Number.isFinite(result),'Invalid timestamp bound.');return result;
}
export function selectRows(rows,channel,params=new URLSearchParams(),maxLimit=10000) {
  const start=parseBound(params.get('start')),end=parseBound(params.get('end'));
  assert(start===null || end===null || start<=end,'The start must not follow the end.');
  const rawLimit=params.get('limit')??'1000';
  assert(/^\d+$/.test(rawLimit),'Display limit must be an integer.');
  const limit=Number(rawLimit);assert(Number.isSafeInteger(limit) && limit>=1 && limit<=maxLimit,`Display limit must be between 1 and ${maxLimit}.`);
  const original=rows.filter(row=>(start===null || row.epoch>=start) && (end===null || row.epoch<=end));
  const total=original.length;
  const indices=total<=limit?original.map((_,i)=>i):Array.from({length:limit},(_,i)=>limit===1?0:Math.floor(i*(total-1)/(limit-1)));
  const gapEnabled=channel.source_kind!=='controller_setpoint';
  let previous=null;
  const readings=indices.map(index=>{
    let maximum=null;
    if(gapEnabled && previous!==null){maximum=0;for(let i=previous+1;i<=index;i++)maximum=Math.max(maximum,original[i].epoch-original[i-1].epoch);}
    previous=index;return {...original[index],gap_before_seconds:maximum};
  });
  return {channel,channel_id:channel.id,metric:channel.metric,unit:channel.unit,total,returned:readings.length,
    sampling:total<=limit?'All original observations in the selected interval.':'Evenly spaced source-row selection including the first and last rows; no values are averaged or invented.',
    timestamp_basis:'Source Unix seconds converted to UTC; original files are hash-verified in this browser.',start:start===null?null:iso(start),end:end===null?null:iso(end),
    gap_rule:{enabled:gapEnabled,threshold_seconds:3600,definition:'Maximum consecutive original source-epoch step between returned rows; first point is null. Controller setpoint events are excluded from the sensor cadence rule.'},
    historical_measurements:true,profile:'Read-only published source files',readings};
}
export async function series(channelId,params=new URLSearchParams()){
  const channel=(await catalogue()).channels.find(c=>c.id===channelId);
  assert(channel,`Unknown source channel: ${channelId}`);
  return selectRows(await sourceRows(channel),channel,params);
}
export async function exportCSV(channelId,params=new URLSearchParams()){
  const selection=new URLSearchParams(params);selection.set('limit','50000');
  const channel=(await catalogue()).channels.find(c=>c.id===channelId);
  assert(channel,`Unknown source channel: ${channelId}`);
  const result=selectRows(await sourceRows(channel),channel,selection,50000);
  assert(result.total===result.returned,'CSV export must contain every selected source record.');
  const columns=['timestamp','epoch','value','unit','channel_id','room_guid','source_line','source_sha256','model_sha256','source_kind'];
  const quote=v=>`"${String(v??'').replaceAll('"','""')}"`;
  const csv=[columns.join(','),...result.readings.map(row=>columns.map(key=>quote(({...channel,...row,channel_id:channel.id})[key])).join(','))].join('\r\n')+'\r\n';
  return {csv,total:result.total};
}
export function verificationStatus(){
  const sources=[...verified].filter(path=>path.startsWith('seed/Measurements/')).length;
  return {verified_assets:verified.size,verified_channels:sources,ifc_verified:verified.has('seed/04_Flat.ifc'),description:`This browser has verified ${sources} of 37 original channel files${verified.has('seed/04_Flat.ifc')?' and the source IFC':''}. Unopened source files are checked when selected, or verify every channel below.`};
}
export async function verifyAllSources(){
  const {channels}=await catalogue();
  await asset('seed/04_Flat.ifc',true);
  let observations=0;
  for(const channel of channels)observations+=(await sourceRows(channel)).length;
  assert(observations===285954,'Unexpected total source record count.');
  return {...verificationStatus(),channels:channels.length,observations};
}
/** Internal view data selector. Paths are logical names, never HTTP API URLs. */
export async function dataset(path, options={}){
  assert(!options.method || options.method==='GET','This published viewer is read-only.');
  const url=new URL(path,'https://static-selector.invalid/');
  const pathname=url.pathname;
  if(pathname==='/api/rooms')return (await catalogue()).rooms;
  if(pathname==='/api/channels')return (await catalogue()).channels.filter(c=>url.searchParams.get('include_unverified')==='true' || c.mapping_verified);
  const room=pathname.match(/^\/api\/rooms\/([^/]+)\/evidence$/);
  if(room){const evidence=await snapshot('room-evidence');const guid=decodeURIComponent(room[1]);assert(Object.hasOwn(evidence,guid),'Unknown IFC space.');return evidence[guid];}
  const channel=pathname.match(/^\/api\/series\/([^/]+)$/);
  if(channel)return series(decodeURIComponent(channel[1]),url.searchParams);
  if(pathname==='/api/geometry'){
    await asset('seed/04_Flat.ifc',true);
    const geometry=await snapshot('geometry');assert((geometry.source_ifc_sha256 || geometry.source?.ifc_sha256)===MODEL_SHA256,'Geometry belongs to a different IFC model.');return geometry;
  }
  if(pathname==='/api/graph/query'){
    const name=url.searchParams.get('template');assert(['room_alignment','verified_channels','unverified_channels'].includes(name),'Unknown archived SPARQL query.');return snapshot(`graph-${name}`);
  }
  const name=pathname.match(/^\/api\/(summary|quality|analytics|operations|provenance|graph)$/)?.[1];
  assert(name,'No server API exists in the read-only published viewer.');return snapshot(name);
}
