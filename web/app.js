import {render} from './lib/markdown.mjs';
import {capture, locate} from './lib/anchor.mjs';
import {buildSeries, sourceLabel} from './lib/viz.mjs';
import {parseRoute, routeFor, relativeTime, citationTarget} from './lib/dashboard.mjs';
import {layoutCards, summarize} from './lib/comments.mjs';

const $ = selector => document.querySelector(selector);
const api = async (path, method = 'GET', body) => {
  const response = await fetch(path, {method, headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) {let message; try {message = (await response.json()).error} catch {} throw new Error(message || `${response.status} ${response.statusText}`)}
  return response.status === 204 ? null : response.json();
};
const url = suffix => `/api/topics/${encodeURIComponent(topicId)}${suffix}`;
const toast = message => {$('#toast').textContent = message; setTimeout(() => {if ($('#toast').textContent === message) $('#toast').textContent = ''}, 5000)};
let topicId = null, tab = 'overview', overview = null, docVersion = 0, threads = [], selected = null, activeId = null, draft = false, lastRefresh = 0, lastPoll = 0, activeRuns = false;
const expanded = new Set(JSON.parse(localStorage.getItem('expanded-comments') || '[]'));
const saveExpanded = () => localStorage.setItem('expanded-comments', JSON.stringify([...expanded]));
function element(tag, className, text) {const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node}
function setFocus(on) {document.body.classList.toggle('focus', on); $('#back-to-topics').hidden = !on}
async function refreshTopics() {
  const list = $('#topic-list'); list.replaceChildren();
  for (const topic of await api('/api/topics')) {
    const row = element('div', 'topic-row'), open = element('button', 'topic', topic.title);
    open.onclick = () => navigate(topic.id, 'overview');
    const meta = element('small', '', `${topic.backend} · ${topic.note_count} threads · ${new Date(topic.created * 1000).toLocaleDateString()}`);
    const del = element('button', 'topic-delete', 'Delete'); del.onclick = async () => {if (!confirm(`Delete ${topic.title} and its workspace?`)) return; await api(`/api/topics/${topic.id}`, 'DELETE'); if (topicId === topic.id) leaveTopic(); await refreshTopics()};
    row.append(open, meta, del); list.append(row);
  }
}
function leaveTopic() {topicId = null; overview = null; $('#topic-title').textContent = 'Topics'; $('#tab-bar').replaceChildren(); setFocus(false); if (location.hash) history.pushState(null, '', location.pathname)}
function navigate(id, nextTab = 'overview') {const hash = routeFor(id, nextTab); if (location.hash !== hash) history.pushState(null, '', hash); route().catch(error => toast(error.message))}
async function route() {
  const route = parseRoute(location.hash);
  if (!route.topicId) {leaveTopic(); return}
  const changed = route.topicId !== topicId;
  topicId = route.topicId; tab = route.tab;
  if (changed) {const data = await api(url('')); docVersion = data.doc_version; $('#instruct-form').elements.backend.value = data.topic.backend; threads = await api(url('/threads'))}
  overview = await api(url('/overview'));
  if (!['overview', 'assets', 'runs', ...overview.sections.map(section => section.id)].includes(tab)) tab = 'overview';
  setFocus(true); $('#topic-title').textContent = overview.topic.title; $('#workspace').textContent = overview.workspace;
  renderTabs(); await showView(); await showRuns(); markRefreshed();
}
function markRefreshed() {lastRefresh = Date.now(); $('#refresh-label').textContent = `refreshed ${new Date(lastRefresh).toLocaleTimeString()} · auto-refresh ${activeRuns ? 2 : 10}s`}
function renderTabs() {
  const bar = $('#tab-bar'); bar.replaceChildren();
  for (const item of [{id: 'overview', title: 'Overview'}, ...overview.sections, {id: 'assets', title: 'Assets'}, {id: 'runs', title: 'Runs'}]) {
    const button = element('button', item.id === tab ? 'active' : '', item.title); button.setAttribute('role', 'tab'); button.setAttribute('aria-selected', String(item.id === tab)); button.onclick = () => navigate(topicId, item.id); bar.append(button);
  }
}
async function showView() {
  const section = overview.sections.find(item => item.id === tab), content = $('#content');
  $('#instruct-form').hidden = tab !== 'overview'; $('#section-layout').classList.toggle('with-comments', !!section);
  $('#comment-margin').hidden = !section; content.replaceChildren(); selected = null; draft = false; $('#comment-button').hidden = true; $('#draft-slot').replaceChildren();
  if (tab === 'overview') return renderOverview(content);
  if (tab === 'assets') return renderAssets(content);
  if (tab === 'runs') return renderRuns(content);
  content.innerHTML = render(section.markdown, {topicId});
  content.className = 'document';
  await renderCharts(content); await renderMermaid(content); renderComments();
}
function renderOverview(content) {
  content.className = 'overview'; const stats = element('div', 'stats');
  for (const stat of overview.stats) {const tile = element('div', 'stat'); tile.append(element('strong', '', String(stat.value)), element('small', '', stat.label)); stats.append(tile)}
  content.append(stats, element('h2', 'tracks-heading', `Tracks ${overview.tracks_done}/${overview.tracks_total} done`));
  const grid = element('div', 'track-grid');
  for (const track of overview.tracks) {
    const card = element('article', 'track-card'), head = element('div', 'track-head');
    head.append(element('h3', '', track.title), element('span', `pill ${track.status}`, track.status)); card.append(head);
    card.append(element('small', 'muted', `${track.owner || 'you'} · updated ${relativeTime(track.updated)}`));
    if (track.summary) card.append(element('p', '', track.summary));
    const files = element('div', 'track-files'); for (const path of track.files || []) {const link = element('button', 'file-link', path); link.onclick = () => {navigate(topicId, 'assets'); setTimeout(() => document.querySelector(`[data-file="${CSS.escape(path)}"]`)?.scrollIntoView(), 100)}; files.append(link)} card.append(files);
    if (track.log?.length) {const details = element('details'); details.append(element('summary', '', 'Latest log lines'), element('pre', '', track.log.join('\n'))); card.append(details)}
    grid.append(card);
  }
  content.append(grid);
}
async function renderAssets(content) {
  content.className = 'document'; content.append(element('h2', '', 'Assets'));
  for (const asset of await api(url('/assets'))) {
    const row = element('p', 'asset-row'); row.dataset.file = asset.path;
    const link = element('a', '', asset.path); link.href = url('/files/' + asset.path.split('/').map(encodeURIComponent).join('/')); link.target = '_blank'; link.rel = 'noopener noreferrer';
    row.append(link, document.createTextNode(` · ${asset.type} · ${asset.size} bytes · ${asset.run_id || 'added manually'}`)); content.append(row);
    if (asset.type.startsWith('image/')) {const image = element('img', 'asset-preview'); image.src = link.href; image.alt = asset.path; content.append(image)}
  }
}
async function renderRuns(content) {content.className = 'document'; content.append(element('h2', '', 'Runs')); for (const run of await api(url('/runs'))) content.append(element('p', 'run-row', `${run.kind} · ${run.backend} · ${run.status} · ${run.instruction || ''} · ${Object.values(run.files_changed || {}).flat().join(', ')}`))}
async function showRuns() {
  const runs = await api(url('/runs')), active = runs.filter(run => ['queued', 'running'].includes(run.status)); activeRuns = active.length > 0;
  const box = $('#run-status'); box.replaceChildren();
  for (const run of active) {const item = element('div', 'run-progress'); item.append(element('span', '', `${run.backend}: ${run.status} · ${run.progress?.at(-1)?.text || run.instruction || ''}`)); const cancel = element('button', '', 'Cancel'); cancel.onclick = async () => {await api(`/api/runs/${run.id}/cancel`, 'POST', {}); await showRuns()}; item.append(cancel); box.append(item)}
  const failed = runs.find(run => run.status === 'error'); if (!active.length && failed) box.append(element('p', '', `${failed.backend} error: ${failed.error || failed.error_kind}`));
  markRefreshed();
}
function textOffset(root, node, offset) {const range = document.createRange(); range.selectNodeContents(root); range.setEnd(node, offset); return range.toString().length}
function highlight(root, start, end, id) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let node, at = 0, pieces = [];
  while ((node = walker.nextNode())) {const next = at + node.length; if (next > start && at < end) pieces.push([node, Math.max(0, start - at), Math.min(node.length, end - at)]); at = next}
  for (const [target, a, b] of pieces.reverse()) {const range = document.createRange(); range.setStart(target, a); range.setEnd(target, b); const mark = element('mark'); mark.dataset.thread = id; range.surroundContents(mark); mark.onclick = () => activate(id, false)}
}
function activate(id, scrollHighlight = true) {
  activeId = id; expanded.add(id); saveExpanded(); renderComments();
  if (scrollHighlight) $('#content').querySelector(`mark[data-thread="${CSS.escape(id)}"]`)?.scrollIntoView({behavior: 'smooth', block: 'center'});
  document.getElementById(`thread-${id}`)?.classList.add('active');
}
function renderComments() {
  const content = $('#content'), root = $('#thread-list'), orphans = $('#orphan-list'), resolved = $('#resolved-list'); root.replaceChildren(); orphans.replaceChildren(); resolved.replaceChildren();
  // Re-anchoring after a resize must start from plain rendered text, not nested marks.
  for (const mark of content.querySelectorAll('mark[data-thread]')) mark.replaceWith(...mark.childNodes);
  content.normalize();
  const text = content.textContent;
  // A comment shows on the tab whose text contains its passage. It is orphaned only when the
  // passage is gone from the whole document, not merely from this tab. Threads created before
  // section_id existed (or imported ones) are placed by searching the text.
  const plainDoc = (overview?.sections || []).map(section => section.markdown).join('\n').replace(/```[\s\S]*?```/g, ' ').replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`#>|]/g, ' ');
  const placement = thread => {
    if (thread.section_id) return thread.section_id !== tab ? 'elsewhere' : (locate(text, thread) ? 'here' : 'orphan');
    if (locate(text, thread)) return 'here';
    return locate(plainDoc, thread) ? 'elsewhere' : 'orphan';
  };
  const located = threads.filter(thread => placement(thread) !== 'elsewhere').map(thread => ({thread, position: locate(text, thread)})).filter(item => !item.thread.resolved).sort((a,b) => (a.position?.start ?? Infinity) - (b.position?.start ?? Infinity));
  for (const {thread, position} of located) if (position) highlight(content, position.start, position.end, thread.id);
  const cards = [];
  for (const {thread, position} of located) {
    const card = makeCard(thread, !position); (position ? root : orphans).append(card);
    if (position) {const mark = content.querySelector(`mark[data-thread="${CSS.escape(thread.id)}"]`); cards.push({id: thread.id, anchorTop: mark.getBoundingClientRect().top - content.getBoundingClientRect().top, height: card.offsetHeight})}
  }
  const tops = layoutCards(cards, {activeId});
  for (const card of cards) document.getElementById(`thread-${card.id}`).style.top = `${tops[card.id]}px`;
  root.style.minHeight = `${Math.max(content.offsetHeight, ...cards.map(card => tops[card.id] + card.height), 0)}px`;
  if (orphans.children.length) orphans.prepend(element('h3', '', 'Orphaned comments — passage removed'));
  const closed = threads.filter(thread => thread.resolved && placement(thread) !== 'elsewhere');
  if (closed.length) {const details = element('details'); details.append(element('summary', '', `Resolved (${closed.length})`)); for (const thread of closed) details.append(makeCard(thread, false, true)); resolved.append(details)}
}
function makeCard(thread, orphaned = false, resolved = false) {
  const state = summarize(thread), card = element('article', `comment-card${activeId === thread.id ? ' active' : ''}`); card.id = `thread-${thread.id}`;
  if (!resolved && !orphaned) card.classList.add('positioned');
  const head = element('button', 'comment-summary'); head.type = 'button'; head.append(element('strong', '', state.quote), element('span', '', state.question), element('small', '', `${state.replies} ${state.replies === 1 ? 'reply' : 'replies'} · ${state.status}${orphaned ? ' · orphaned' : ''}`));
  head.onclick = () => {activeId = thread.id; if (expanded.has(thread.id)) expanded.delete(thread.id); else expanded.add(thread.id); saveExpanded(); renderComments(); if (!orphaned && !resolved) $('#content').querySelector(`mark[data-thread="${CSS.escape(thread.id)}"]`)?.scrollIntoView({behavior:'smooth',block:'center'})}; card.append(head);
  if (!expanded.has(thread.id) || resolved) {if (resolved) addResolveButton(card, thread, false); return card}
  for (const turn of thread.turns) {const div = element('div', 'turn'); div.append(element('small', '', `${turn.role} · ${turn.backend || ''}${turn.model ? ' · ' + turn.model : ''}${turn.status ? ' · ' + turn.status : ''}`)); const body = element('div'); body.innerHTML = render(turn.text || turn.error || '', {topicId}); div.append(body); card.append(div)}
  const form = element('form', 'reply-form'), question = element('textarea'); question.required = true; question.placeholder = 'Reply'; const backend = backendSelect(thread.turns.at(-1)?.backend || overview.topic.backend), submit = element('button', '', 'Reply'); form.append(question, backend, submit);
  form.onsubmit = async event => {event.preventDefault(); try {await api(url(`/threads/${thread.id}/turns`), 'POST', {question: question.value, backend: backend.value}); await refreshThreads()} catch(error) {toast(error.message)}}; card.append(form);
  addResolveButton(card, thread, true);
  if (state.status === 'error') {const retry = element('button', '', 'Retry'); retry.onclick = async () => {try {await api(url(`/threads/${thread.id}/retry`), 'POST', {}); await refreshThreads()} catch(error) {toast(error.message)}}; card.append(retry)}
  const del = element('button', 'danger', 'Delete'); del.onclick = async () => {if (!confirm('Delete this thread?')) return; await api(url(`/threads/${thread.id}`), 'DELETE'); await refreshThreads()}; card.append(del);
  return card;
}
function addResolveButton(card, thread, value) {const button = element('button', '', value ? 'Resolve' : 'Reopen'); button.onclick = async () => {try {await api(url(`/threads/${thread.id}/resolve`), 'POST', {resolved: value}); await refreshThreads()} catch(error) {toast(error.message)}}; card.append(button)}
function backendSelect(value) {const select = element('select'); for (const name of ['claude', 'codex']) {const option = element('option', '', name); option.value = name; select.append(option)} select.value = value; return select}
async function refreshThreads() {threads = await api(url('/threads')); if (overview.sections.some(section => section.id === tab)) {$('#content').innerHTML = render(overview.sections.find(section => section.id === tab).markdown, {topicId}); await renderCharts($('#content')); await renderMermaid($('#content')); renderComments()}}
function draftCard() {
  draft = true; const slot = $('#draft-slot'); slot.replaceChildren(); const card = element('form', 'comment-card draft'); card.append(element('small', '', selected.quote)); const question = element('textarea'); question.value = 'What does this mean?'; question.required = true; const backend = backendSelect(overview.topic.backend); const submit = element('button', '', 'Comment'), cancel = element('button', '', 'Cancel'); cancel.type = 'button'; cancel.onclick = () => {draft = false; slot.replaceChildren()}; card.append(question, backend, submit, cancel);
  card.onsubmit = async event => {event.preventDefault(); try {const thread = await api(url('/threads'), 'POST', {...selected, section_id: tab, question: question.value, backend: backend.value}); expanded.add(thread.id); activeId = thread.id; saveExpanded(); draft = false; slot.replaceChildren(); await refreshThreads()} catch(error) {toast(error.message)}};
  slot.append(card); slot.style.top = $('#comment-button').style.top; $('#comment-button').hidden = true; question.focus();
}
async function renderCharts(root) {
  for (const node of root.querySelectorAll('.viz')) {
    let spec; try {spec = JSON.parse(node.dataset.spec)} catch {node.textContent = 'Invalid chart spec'; continue}
    const chart = element('div', 'chart'); node.replaceWith(chart); chart.append(element('h3', '', spec.title || 'Chart'));
    try {const table = await api(url(`/table?path=${encodeURIComponent(spec.data)}`)), data = buildSeries(spec, table);
      if (spec.type === 'table') {const grid = element('table'), head = element('tr'); for (const column of table.columns) head.append(element('th', '', column)); grid.append(head); for (const row of table.rows) {const tr = element('tr'); for (const value of row) tr.append(element('td', '', value == null ? '' : String(value))); grid.append(tr)} chart.append(grid)}
      else drawSVG(chart, data, spec);
    } catch(error) {chart.append(element('p', '', error.message))}
    chart.append(element('p', 'source', sourceLabel(spec)));
  }
}
function drawSVG(chart, data, spec) {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', '0 0 700 320'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', spec.title || 'Chart'); chart.append(svg);
  const make = (tag, attrs) => {const node = document.createElementNS(ns, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); svg.append(node); return node};
  const values = data.series.flatMap(series => series.values).filter(value => typeof value === 'number' && Number.isFinite(value)), min = Math.min(0, ...values), max = Math.max(1, ...values);
  const x = index => 55 + index * 600 / Math.max(1, data.x.length - 1), y = value => 275 - (value - min) * 230 / (max - min), colors = ['#1c8d90', '#bb7254', '#8255af', '#d0a22e', '#4f86c6'];
  make('line', {x1: 55, y1: 275, x2: 655, y2: 275, stroke: 'currentColor'}); make('line', {x1: 55, y1: 45, x2: 55, y2: 275, stroke: 'currentColor'});
  const tip = element('div', 'tip'); chart.append(tip);
  // Axis labels: every ~8th x value along the bottom, and the y range on the left.
  data.x.forEach((value, i) => {if (i % Math.ceil(data.x.length / 8) !== 0) return; make('text', {x: x(i), y: 300, 'text-anchor': 'middle', fill: 'currentColor', 'font-size': 12}).textContent = value});
  for (const [value, at] of [[max, 45], [min, 275]]) make('text', {x: 48, y: at + 4, 'text-anchor': 'end', fill: 'currentColor', 'font-size': 12}).textContent = Number(value.toFixed(2));
  data.series.forEach((series, j) => {const reference = /^threshold/i.test(series.name), color = colors[j % colors.length]; if (spec.type !== 'bar' || reference) make('polyline', {points: series.values.map((value, i) => typeof value === 'number' ? `${x(i)},${y(value)}` : '').join(' '), fill: 'none', stroke: color, 'stroke-width': reference ? 2 : 3, ...(reference ? {'stroke-dasharray': '6 4'} : {})}); if (reference) return; series.values.forEach((value, i) => {if (typeof value !== 'number') return; const point = spec.type === 'bar' ? make('rect', {x: x(i) - 15 + j * 12, y: y(value), width: 11, height: 275 - y(value), fill: color}) : make('circle', {cx: x(i), cy: y(value), r: 5, fill: color}); const title = document.createElementNS(ns, 'title'); title.textContent = `${data.x[i]} · ${series.name}: ${value}`; point.append(title); point.onmouseenter = () => tip.textContent = title.textContent; point.onmouseleave = () => tip.textContent = ''})});
  // Legend above the plot with a swatch per series, so lines are identified by name, not colour alone.
  if (data.series.length > 1) {const legend = element('div', 'legend'); data.series.forEach((series, j) => {const item = element('span', ''), swatch = element('i', '');
    swatch.style.borderTop = `3px ${/^threshold/i.test(series.name) ? 'dashed' : 'solid'} ${colors[j % colors.length]}`; item.append(swatch, document.createTextNode(series.name)); legend.append(item)}); svg.before(legend)}
}
async function renderMermaid(root) {if (!root.querySelector('.mermaid')) return; try {const mermaid = (await import('https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs')).default; mermaid.initialize({startOnLoad: false, securityLevel: 'strict'}); await mermaid.run({nodes: root.querySelectorAll('.mermaid')})} catch(error) {toast(`Diagram unavailable: ${error.message}`)}}

$('#new-topic').onsubmit = async event => {event.preventDefault(); const form = event.currentTarget; try {const topic = await api('/api/topics', 'POST', {title: form.elements.title.value, focus: form.elements.focus.value, backend: form.elements.backend.value}); form.reset(); await refreshTopics(); navigate(topic.id)} catch(error) {toast(error.message)}};
$('#instruct-form').onsubmit = async event => {event.preventDefault(); const form = event.currentTarget; try {await api(url('/instruct'), 'POST', {instruction: form.elements.instruction.value, backend: form.elements.backend.value}); form.elements.instruction.value = ''; await showRuns(); overview = await api(url('/overview')); await showView()} catch(error) {toast(error.message)}};
$('#back-to-topics').onclick = () => {leaveTopic(); refreshTopics().catch(error => toast(error.message))};
$('#content').addEventListener('mouseup', () => {if (!overview?.sections.some(section => section.id === tab)) return; const selection = window.getSelection(), root = $('#content'); if (!selection || selection.isCollapsed || !root.contains(selection.anchorNode) || !root.contains(selection.focusNode)) return; const text = root.textContent, start = textOffset(root, selection.anchorNode, selection.anchorOffset), end = textOffset(root, selection.focusNode, selection.focusOffset); selected = capture(text, Math.min(start, end), Math.max(start, end)); if (!selected.quote.trim()) return; const rect = selection.getRangeAt(0).getBoundingClientRect(), container = root.getBoundingClientRect(); const button = $('#comment-button'); button.style.top = `${Math.max(0, rect.top - container.top)}px`; button.hidden = false});
$('#comment-button').onclick = draftCard;
document.addEventListener('click', event => {const citation = event.target.closest('a[href^="#src-"]'); if (!citation || !overview) return; const target = citationTarget(citation.getAttribute('href').slice(5), overview.sections); if (target) {event.preventDefault(); navigate(topicId, target.tab); setTimeout(() => document.getElementById(target.anchor)?.scrollIntoView({behavior: 'smooth'}), 100)}});
addEventListener('popstate', () => route().catch(error => toast(error.message)));
addEventListener('resize', () => {if (topicId && overview?.sections.some(section => section.id === tab)) renderComments()});
async function poll() {if (topicId && (activeRuns || Date.now() - lastPoll >= 10000)) try {lastPoll = Date.now(); const version = await api(url('/version')); if (version.doc_version !== docVersion || version.active_runs || activeRuns) {const data = await api(url('')); docVersion = data.doc_version; overview = await api(url('/overview')); threads = await api(url('/threads')); renderTabs(); await showView()} await showRuns()} catch(error) {toast(error.message)} setTimeout(poll, 2000)}
setFocus(false); refreshTopics().then(() => route()).catch(error => toast(error.message)); setTimeout(poll, 2000);
