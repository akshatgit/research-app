import {render} from './lib/markdown.mjs';
import {capture, locate} from './lib/anchor.mjs';
import {buildSeries, sourceLabel} from './lib/viz.mjs';
const $ = s => document.querySelector(s);
let topicId = null, docVersion = 0, currentDoc = '', threads = [], view = 'document', selected = null;
const api = async (path, method='GET', body) => {
  const r = await fetch(path, {method, headers: body !== undefined ? {'Content-Type':'application/json'} : {}, body: body !== undefined ? JSON.stringify(body) : undefined});
  if (!r.ok) {let err; try {err = (await r.json()).error} catch {} throw new Error(err || `${r.status} ${r.statusText}`)}
  return r.status === 204 ? null : r.json();
};
const url = suffix => `/api/topics/${encodeURIComponent(topicId)}${suffix}`;
function toast(message) {$('#toast').textContent = message; setTimeout(() => {if ($('#toast').textContent === message) $('#toast').textContent = ''}, 5000)}
async function refreshTopics() {
  const list = await api('/api/topics'); $('#topic-list').replaceChildren();
  for (const t of list) {const b = document.createElement('button'); b.className = 'topic' + (t.id === topicId ? ' active' : '');
    b.dataset.id = t.id; b.innerHTML = `<b></b><small></small>`; b.querySelector('b').textContent = t.title;
    b.querySelector('small').textContent = `${t.backend} · ${t.note_count} threads · ${new Date(t.created*1000).toLocaleDateString()}`;
    b.onclick = () => openTopic(t.id); $('#topic-list').append(b); const del = document.createElement('button'); del.className='topic-delete'; del.textContent='Delete'; del.title=`Delete ${t.title}`; del.onclick=async()=>{if(!confirm(`Delete ${t.title} and its workspace?`))return; await api(`/api/topics/${encodeURIComponent(t.id)}`,'DELETE'); if(topicId===t.id){leaveTopic();return} await refreshTopics()}; $('#topic-list').append(del)}
}
// Focus mode: an open topic gets the whole screen; the topic list is its own page.
function setFocus(on) {document.body.classList.toggle('focus', on); $('#back-to-topics').hidden = !on}
function leaveTopic() {topicId = null; currentDoc = ''; threads = []; $('#content').replaceChildren(); $('#thread-list').replaceChildren();
  $('#run-status').replaceChildren(); $('#topic-title').textContent = 'Topics'; setFocus(false);
  if (location.hash) history.pushState(null, '', location.pathname); refreshTopics().catch(err => toast(err.message))}
async function openTopic(id) {
  topicId = id; view = 'document'; setFocus(true);
  if (location.hash !== `#${id}`) history.pushState(null, '', `#${id}`);
  const data = await api(url('')); currentDoc = data.doc; docVersion = data.doc_version;
  $('#topic-title').textContent = data.topic.title; $('#instruct-form').elements.backend.value = data.topic.backend;
  $('#ask-form').elements.backend.value = data.topic.backend;
  threads = await api(url('/threads')); await showView(); await refreshTopicsLight(); await showRuns();
}
async function refreshTopicsLight() {for (const b of document.querySelectorAll('.topic')) b.classList.toggle('active', b.dataset.id === topicId)}
async function showView() {
  if (!topicId) return; const content = $('#content');
  if (view === 'document') {content.className = 'document'; content.innerHTML = render(currentDoc, {topicId});
    await renderCharts(content); await renderMermaid(content); renderThreads();}
  else if (view === 'assets') {content.className = 'document'; const assets = await api(url('/assets')); content.replaceChildren();
    const h = document.createElement('h2'); h.textContent = 'Assets'; content.append(h);
    for (const a of assets) {const p = document.createElement('p'), link = document.createElement('a');
      link.href = url('/files/' + a.path.split('/').map(encodeURIComponent).join('/')); link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = a.path;
      p.append(link, document.createTextNode(` · ${a.type} · ${a.size} bytes · ${a.run_id || 'added manually'}`)); content.append(p); if(a.type.startsWith('image/')) {const image=document.createElement('img'); image.src=link.href; image.alt=a.path; image.className='asset-preview'; content.append(image)}}}
  else {content.className = 'document'; content.replaceChildren(); const h = document.createElement('h2'); h.textContent = 'Runs'; content.append(h);
    for (const r of await api(url('/runs'))) {const p = document.createElement('p'); p.textContent = `${r.kind} · ${r.backend} · ${r.status} · ${r.instruction || ''} · ${Object.values(r.files_changed || {}).flat().join(', ')}`; content.append(p)}}
}
async function showRuns() {
  if (!topicId) return;
  const runs = await api(url('/runs')); const active = runs.filter(r => ['queued','running'].includes(r.status)); const box = $('#run-status'); box.replaceChildren();
  for (const r of active) {const p = document.createElement('p'); p.textContent = `${r.backend}: ${r.status} · ${r.progress?.slice(-1)[0]?.text || r.instruction || ''}`;
    const cancel = document.createElement('button'); cancel.textContent = 'Cancel'; cancel.onclick = async () => {await api(`/api/runs/${r.id}/cancel`, 'POST', {}); await showRuns()}; box.append(p, cancel)}
  const failed = runs.find(r => r.status === 'error'); if (failed && !active.length) {const p = document.createElement('p'); p.textContent = `${failed.backend} error: ${failed.error || failed.error_kind}`; box.append(p)}
}
function textOffset(root, node, offset) {const range = document.createRange(); range.selectNodeContents(root); range.setEnd(node, offset); return range.toString().length}
function highlight(root, start, end, id) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let node, pos = 0, pieces = [];
  while ((node = walker.nextNode())) {const next = pos + node.length; if (next > start && pos < end) pieces.push([node, Math.max(0,start-pos),Math.min(node.length,end-pos)]); pos = next}
  for (const [n,a,b] of pieces.reverse()) {const r = document.createRange(); r.setStart(n,a); r.setEnd(n,b); const mark = document.createElement('mark'); mark.dataset.thread = id; r.surroundContents(mark); mark.onclick = () => document.getElementById(`thread-${id}`)?.scrollIntoView({behavior:'smooth'})}
}
function renderThreads() {
  const root = $('#content'), text = root.textContent, list = $('#thread-list'); list.replaceChildren();
  const located = threads.map(t => ({t, position: locate(text,t)})).sort((a,b) => (a.position?.start ?? Infinity)-(b.position?.start ?? Infinity));
  for (const {t, position} of located) {if (position) highlight(root, position.start, position.end, t.id);
    const card = document.createElement('article'); card.className = 'thread'; card.id = `thread-${t.id}`;
    const quote = document.createElement('blockquote'); quote.textContent = `${position ? '' : '[Orphaned] '}${t.quote}`; card.append(quote);
    for (const turn of t.turns) {const div = document.createElement('div'); div.className = 'turn'; const label = document.createElement('small');
      label.textContent = `${turn.role} · ${turn.backend}${turn.model ? ' · ' + turn.model : ''}${turn.status ? ' · ' + turn.status : ''}`;
      const body = document.createElement('div'); body.innerHTML = render(turn.text || turn.error || '', {topicId}); div.append(label,body); card.append(div)}
    const form = document.createElement('form'); const field = document.createElement('textarea'); field.placeholder = 'Follow up'; field.required = true;
    const backend = document.createElement('select'); for (const name of ['claude','codex']) {const o = document.createElement('option'); o.textContent = name; backend.append(o)} backend.value = t.turns.at(-1).backend;
    const submit = document.createElement('button'); submit.textContent = 'Follow up'; form.append(field,backend,submit);
    form.onsubmit = async e => {e.preventDefault(); try {await api(url(`/threads/${t.id}/turns`),'POST',{question:field.value,backend:backend.value}); field.value=''; await reloadThreads()} catch(err) {toast(err.message)}};
    const retry = document.createElement('button'); retry.textContent = 'Retry'; retry.onclick = async () => {try {await api(url(`/threads/${t.id}/retry`),'POST',{}); await reloadThreads()} catch(err) {toast(err.message)}};
    const del = document.createElement('button'); del.textContent = 'Delete'; del.onclick = async () => {if (confirm('Delete this thread?')) {await api(url(`/threads/${t.id}`),'DELETE'); await reloadThreads()}};
    card.append(form,retry,del); list.append(card)}
}
async function reloadThreads() {threads = await api(url('/threads')); if (view === 'document') await showView(); else renderThreads()}
async function renderCharts(root) {
  for (const node of root.querySelectorAll('.viz')) {let spec; try {spec = JSON.parse(node.dataset.spec)} catch {node.textContent = 'Invalid chart spec'; continue}
    const chart = document.createElement('div'); chart.className = 'chart'; node.replaceWith(chart);
    const title = document.createElement('h3'); title.textContent = spec.title || 'Chart'; chart.append(title);
    try {const table = await api(url(`/table?path=${encodeURIComponent(spec.data)}`)); const data = buildSeries(spec,table);
      if (spec.type === 'table') {const t = document.createElement('table'); t.innerHTML = '<thead><tr></tr></thead><tbody></tbody>'; for (const name of table.columns) {const th=document.createElement('th');th.textContent=name;t.querySelector('tr').append(th)}
        for (const row of table.rows) {const tr=document.createElement('tr');for (const value of row) {const td=document.createElement('td');td.textContent=value ?? '';tr.append(td)}t.querySelector('tbody').append(tr)} chart.append(t)}
      else drawSVG(chart,data,spec);
    } catch(err) {const p=document.createElement('p');p.textContent=err.message;chart.append(p)}
    const label=document.createElement('p'); label.className='source'; label.textContent=sourceLabel(spec); chart.append(label)}
}
function drawSVG(chart,data,spec) {
  const NS='http://www.w3.org/2000/svg', svg=document.createElementNS(NS,'svg'); svg.setAttribute('viewBox','0 0 700 320'); svg.setAttribute('role','img'); svg.setAttribute('aria-label',spec.title || 'Chart'); chart.append(svg);
  const make=(tag,attrs={})=>{const el=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,String(v));svg.append(el);return el};
  const values=data.series.flatMap(s=>s.values).filter(x=>typeof x==='number'&&Number.isFinite(x)); const min=Math.min(0,...values), max=Math.max(1,...values), n=data.x.length;
  const X=i=>55+i*600/Math.max(1,n-1), Y=v=>275-(v-min)*230/(max-min);
  make('line',{x1:55,y1:275,x2:655,y2:275,stroke:'currentColor'});make('line',{x1:55,y1:45,x2:55,y2:275,stroke:'currentColor'});
  const colors=['#1c8d90','#bb7254','#8255af','#d0a22e','#4f86c6'];
  // Series named "threshold ..." are reference lines: dashed, no points.
  const isRef=s=>/^threshold/i.test(s.name);const tip=document.createElement('div');tip.className='tip';chart.append(tip);
  data.series.forEach((s,j)=>{const points=s.values.map((v,i)=>typeof v==='number'?`${X(i)},${Y(v)}`:null).filter(Boolean);
    if (spec.type==='line'||spec.type==='area'||isRef(s)) make('polyline',{points:points.join(' '),fill:'none',stroke:colors[j%colors.length],'stroke-width':isRef(s)?2:3,...(isRef(s)?{'stroke-dasharray':'6 4'}:{})});
    if (isRef(s)) return;
    s.values.forEach((v,i)=>{if(typeof v!=='number')return;let el;
      if(spec.type==='bar') el=make('rect',{x:X(i)-15+j*12,y:Y(v),width:11,height:275-Y(v),fill:colors[j%colors.length]});
      else el=make('circle',{cx:X(i),cy:Y(v),r:5,fill:colors[j%colors.length]});
      const title=document.createElementNS(NS,'title');title.textContent=`${data.x[i]} · ${s.name}: ${v}`;el.append(title);el.addEventListener('mouseenter',()=>tip.textContent=title.textContent);el.addEventListener('mouseleave',()=>tip.textContent='')})});
  data.x.forEach((x,i)=>{if(i%Math.ceil(n/8)!==0)return;const label=make('text',{x:X(i),y:300,'text-anchor':'middle',fill:'currentColor','font-size':12});label.textContent=x});
  for(const [v,y] of [[max,45],[min,275]]) {const label=make('text',{x:48,y,'text-anchor':'end',fill:'currentColor','font-size':12});label.textContent=Number(v.toFixed(2))}
  // A legend whenever there is more than one series, so lines are identified by name, not colour alone.
  if (data.series.length>1) {const legend=document.createElement('div');legend.className='legend';
    data.series.forEach((s,j)=>{const item=document.createElement('span');const sw=document.createElement('i');
      sw.style.borderTop=`3px ${isRef(s)?'dashed':'solid'} ${colors[j%colors.length]}`;item.append(sw,document.createTextNode(s.name));legend.append(item)});
    svg.before(legend)}
}
async function renderMermaid(root) {if(!root.querySelector('.mermaid'))return;try {const mermaid=(await import('https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs')).default;mermaid.initialize({startOnLoad:false,securityLevel:'strict'});await mermaid.run({nodes:root.querySelectorAll('.mermaid')})}catch(err){toast(`Diagram unavailable: ${err.message}`)}}
$('#new-topic').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{const t=await api('/api/topics','POST',{title:f.elements.title.value,focus:f.elements.focus.value,backend:f.elements.backend.value});f.reset();await openTopic(t.id);await refreshTopics()}catch(err){toast(err.message)}};
$('#instruct-form').onsubmit=async e=>{e.preventDefault();if(!topicId)return;const f=e.currentTarget;try{await api(url('/instruct'),'POST',{instruction:f.elements.instruction.value,backend:f.elements.backend.value});f.elements.instruction.value='';await showRuns()}catch(err){toast(err.message)}};
for(const [id,name] of [['document-tab','document'],['assets-tab','assets'],['runs-tab','runs']]) $(`#${id}`).onclick=()=>{view=name;showView()};
$('#content').addEventListener('mouseup',()=>{const selection=window.getSelection();if(!selection||selection.isCollapsed||!$('#content').contains(selection.anchorNode)||!$('#content').contains(selection.focusNode))return;
  const text=$('#content').textContent, start=textOffset($('#content'),selection.anchorNode,selection.anchorOffset),end=textOffset($('#content'),selection.focusNode,selection.focusOffset);
  selected=capture(text,Math.min(start,end),Math.max(start,end));$('#selection-actions').hidden=!selected.quote.trim()});
function openAsk(question='What does this mean?'){if(!selected)return;$('#ask-quote').textContent=selected.quote;$('#ask-form').elements.question.value=question;$('#ask-dialog').showModal();$('#selection-actions').hidden=true}
$('#ask-button').onclick=()=>openAsk();document.querySelectorAll('[data-quick]').forEach(b=>b.onclick=()=>openAsk(b.dataset.quick));
$('#ask-close').onclick=()=>$('#ask-dialog').close();$('#ask-form').onsubmit=async e=>{e.preventDefault();try{await api(url('/threads'),'POST',{...selected,question:e.currentTarget.elements.question.value,backend:e.currentTarget.elements.backend.value});$('#ask-dialog').close();await reloadThreads()}catch(err){toast(err.message)}};
setInterval(async()=>{if(!topicId)return;try{const version=await api(url('/version'));if(version.doc_version!==docVersion){const data=await api(url(''));currentDoc=data.doc;docVersion=data.doc_version;if(view==='document')await showView()}if(version.active_runs||threads.some(t=>['queued','running'].includes(t.turns.at(-1)?.status))){await showRuns();await reloadThreads()}else await showRuns()}catch(err){toast(err.message)}},2000);
$('#back-to-topics').onclick = leaveTopic;
// The URL hash keeps you in a topic across reloads; Back/Forward move between list and topic.
async function route() {const id = location.hash.slice(1); if (id && id !== topicId) {try {await openTopic(id)} catch {leaveTopic()}} else if (!id && topicId) leaveTopic()}
addEventListener('popstate', () => route().catch(err => toast(err.message)));
setFocus(false); refreshTopics().then(route).catch(err=>toast(err.message));
