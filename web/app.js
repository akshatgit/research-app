import {render} from './lib/markdown.mjs';
import {capture, locate} from './lib/anchor.mjs';
import {buildSeries, sourceLabel} from './lib/viz.mjs';
import {parseRoute, routeFor, relativeTime, citationTarget} from './lib/dashboard.mjs';
import {layoutCards, summarize} from './lib/comments.mjs';
import {formatUsage} from './lib/usage.mjs';

const $ = selector => document.querySelector(selector);
const api = async (path, method = 'GET', body) => {
  const response = await fetch(path, {method, headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) {let message; try {message = (await response.json()).error} catch {} throw new Error(message || `${response.status} ${response.statusText}`)}
  return response.status === 204 ? null : response.json();
};
const url = suffix => `/api/topics/${encodeURIComponent(topicId)}${suffix}`;
const toast = message => {$('#toast').textContent = message; setTimeout(() => {if ($('#toast').textContent === message) $('#toast').textContent = ''}, 5000)};
let topicId = null, tab = 'overview', overview = null, docVersion = 0, docMarkdown = '', threads = [], selected = null, activeId = null, draft = false, lastRefresh = 0, lastPoll = 0, activeRuns = false;
let openThreadId = null;
const expanded = new Set(JSON.parse(localStorage.getItem('expanded-comments') || '[]'));
const saveExpanded = () => localStorage.setItem('expanded-comments', JSON.stringify([...expanded]));
// Comments live in the pop-up; the document shows only highlights (no margin to hide).
let commentsHidden = false; try {localStorage.removeItem('comments-hidden')} catch {}
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
  if (changed) {const data = await api(url('')); docVersion = data.doc_version; docMarkdown = data.doc; $('#instruct-form').elements.backend.value = data.topic.backend; threads = await api(url('/threads'))}
  overview = await api(url('/overview'));
  if (!overview.tabs.some(item => item.id === tab)) tab = 'overview';
  setFocus(true); $('#topic-title').textContent = overview.topic.title; $('#workspace').textContent = overview.workspace;
  renderTabs(); await showView(); await showRuns(); markRefreshed();
}
function markRefreshed() {lastRefresh = Date.now(); $('#refresh-label').textContent = `refreshed ${new Date(lastRefresh).toLocaleTimeString()} · auto-refresh ${activeRuns ? 2 : 10}s`}
function renderTabs() {
  const bar = $('#tab-bar'); bar.replaceChildren();
  for (const item of overview.tabs) {
    const button = element('button', item.id === tab ? 'active' : '', item.title); button.setAttribute('role', 'tab'); button.setAttribute('aria-selected', String(item.id === tab)); button.onclick = () => navigate(topicId, item.id); bar.append(button);
  }
}
async function showView() {
  const section = overview.sections.find(item => item.id === tab), hasComments = tab === 'document' || !!section, content = $('#content');
  $('#instruct-form').hidden = tab !== 'overview'; $('#section-layout').classList.toggle('with-comments', hasComments && !commentsHidden);
  $('#comment-margin').hidden = true; $('#show-comments').hidden = true;
  $('#comments-list').hidden = !hasComments; $('#comments-list').textContent = `Comments (${threads.length})`;
  $('#show-comments').textContent = `Show comments (${threads.filter(thread => !thread.resolved).length})`;
  content.replaceChildren(); selected = null; draft = false; $('#comment-button').hidden = true; $('#draft-slot').replaceChildren();
  if (tab === 'overview') return renderOverview(content);
  if (tab === 'assets') return renderAssets(content);
  if (tab === 'runs') return renderRuns(content);
  content.innerHTML = render(tab === 'document' ? docMarkdown : section.markdown, {topicId});
  content.className = 'document';
  await renderCharts(content); await renderMermaid(content); renderComments();
}
function renderOverview(content) {
  content.className = 'overview'; const stats = element('div', 'stats');
  for (const stat of overview.stats) {const tile = element('div', 'stat'); tile.append(element('strong', '', String(stat.value)), element('small', '', stat.label)); stats.append(tile)}
  content.append(stats, element('h2', 'tracks-heading', `Tracks ${overview.tracks_done}/${overview.tracks_total} done`));
  const grid = element('div', 'track-grid');
  for (const track of overview.tracks) {
    const card = element('article', 'track-card'), head = element('div', 'track-head'); card.dataset.track = track.id;
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
async function renderRuns(content) {content.className = 'document'; content.append(element('h2', '', 'Runs')); for (const run of await api(url('/runs'))) {const row = element('div', 'run-row'); row.append(element('p', '', `${run.kind} · ${run.backend} · ${run.status} · ${run.instruction || ''} · ${Object.values(run.files_changed || {}).flat().join(', ')}`)); const usage = formatUsage(run.usage, run.cost_usd); if (usage) row.append(element('small', 'muted', usage)); content.append(row)}}
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
  for (const [target, a, b] of pieces.reverse()) {const range = document.createRange(); range.setStart(target, a); range.setEnd(target, b); const mark = element('mark'); mark.dataset.thread = id; range.surroundContents(mark); mark.onclick = () => openThread(id)}
}
// Clicking an open comment's highlight again closes it, like Google Docs.
function collapse(id) {expanded.delete(id); saveExpanded(); if (activeId === id) activeId = null; renderComments()}
function activate(id, scrollHighlight = true) {
  activeId = id; expanded.add(id); saveExpanded(); renderComments();
  if (scrollHighlight) $('#content').querySelector(`mark[data-thread="${CSS.escape(id)}"]`)?.scrollIntoView({behavior: 'smooth', block: 'center'});
  document.getElementById(`thread-${id}`)?.classList.add('active');
}
function renderComments() {
  if (commentsHidden) return;
  if (localStorage.getItem('expanded-comments') === null) {for (const thread of threads) expanded.add(thread.id); saveExpanded()}
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
    if (thread.section_id && tab !== 'document') return thread.section_id !== tab ? 'elsewhere' : (locate(text, thread) ? 'here' : 'orphan');
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
// TL;DRs are Markdown too (bold, code, citations): render them inline with the same safe
// renderer as answers, so `**x**` and backticks don't show up raw.
function inline(md, tag = 'span', cls = '') {const node = element(tag, cls); node.innerHTML = render(md || '', {topicId}).replace(/^<p>|<\/p>\s*$/g, ''); return node}
function makeCard(thread, orphaned = false, resolved = false) {
  const state = summarize(thread), card = element('article', `comment-card${activeId === thread.id ? ' active' : ''}`); card.id = `thread-${thread.id}`;
  if (!resolved && !orphaned) card.classList.add('positioned');
  const heading = element('div', 'comment-heading'), chevron = element('button', 'comment-chevron', expanded.has(thread.id) ? '⌄' : '›');
  chevron.type = 'button'; chevron.setAttribute('aria-label', expanded.has(thread.id) ? 'Collapse comment' : 'Expand comment');
  chevron.onclick = () => {activeId = thread.id; if (expanded.has(thread.id)) expanded.delete(thread.id); else expanded.add(thread.id); saveExpanded(); renderComments()};
  const head = element('button', 'comment-summary'); head.type = 'button'; head.append(element('strong', '', state.quote), element('span', '', state.question)); head.onclick = () => openThread(thread.id);
  heading.append(chevron, head); card.append(heading);
  if (expanded.has(thread.id)) {
    const pairs = []; for (let i = 1; i < thread.turns.length - 1; i += 2) if (thread.turns[i]?.role === 'assistant') pairs.push([thread.turns[i - 1], thread.turns[i]]);
    for (const [question, answer] of pairs.slice(1, -1)) {const line = element('div', 'exchange-line', `${question.text} → `); line.append(inline(answer.tldr || answer.text || '…')); card.append(line)}
    const latest = [...thread.turns].reverse().find(turn => turn.role === 'assistant');
    if (latest) {const box = element('div', 'card-tldr'); box.append(element('small', 'intent-label', latest.intent || 'explain'), inline(latest.tldr || latest.text || latest.status || 'Waiting for answer…', 'p')); card.append(box)}
    const read = element('button', 'read-answer', 'Read full answer'); read.onclick = () => openThread(thread.id); card.append(read);
  }
  const count = thread.turns.filter(turn => turn.role === 'assistant').length;
  card.append(element('small', 'card-status', `${count} ${count === 1 ? 'answer' : 'answers'} · ${state.status}${orphaned ? ' · orphaned' : ''}`));
  return card;
}
function closeThread() {openThreadId = null; $('#thread-dialog').close()}
function openThread(id) {openThreadId = id; activeId = id; renderDialog(); if (!$('#thread-dialog').open) $('#thread-dialog').showModal()}
function renderDialog() {
  const thread = threads.find(item => item.id === openThreadId); if (!thread) {if ($('#thread-dialog').open) closeThread(); return}
  const body = $('#dialog-body'), footer = $('#dialog-footer'), oldScroll = body.scrollTop, typed = footer.querySelector('textarea')?.value, oldIntent = footer.querySelector('select[name="intent"]')?.value, oldBackend = footer.querySelector('select[name="backend"]')?.value;
  $('#dialog-quote').textContent = thread.quote; body.replaceChildren(); footer.replaceChildren();
  thread.turns.forEach((turn, index) => {
    const item = element('div', turn.role === 'user' ? 'dialog-user' : 'dialog-answer');
    if (turn.role === 'user') {item.append(element('small', '', `You · ${turn.intent || 'auto'}`), element('p', '', turn.text)); body.append(item); return}
    item.append(element('small', 'answer-meta', `${turn.backend || ''}${turn.model ? ' · ' + turn.model : ''} · ${turn.intent || 'explain'}`));
    if (turn.tldr) {const summary = element('div', 'answer-tldr'); summary.append(element('strong', '', 'TL;DR '), inline(turn.tldr)); item.append(summary)}
    const content = element('div', 'answer-body'); content.innerHTML = render(turn.text || turn.error || '', {topicId}); item.append(content);
    if (turn.intent === 'update') {item.append(element('small', 'muted', turn.doc_diff ? 'Updated the document' : 'No change')); if (turn.doc_diff) {const details = element('details'); details.append(element('summary', '', 'Document diff'), element('pre', 'doc-diff', turn.doc_diff)); item.append(details)} const undo = element('button', '', turn.undone ? 'Undone' : 'Undo'); undo.disabled = turn.undone || turn.status !== 'done' || !turn.doc_diff; undo.onclick = async () => {try {await api(url(`/threads/${thread.id}/turns/${index + 1}/undo`), 'POST', {}); await reloadTopic(); renderDialog()} catch(error) {toast(error.message)}}; item.append(undo)}
    if (turn.intent === 'task') {item.append(element('span', `pill ${{queued:'pending', error:'failed', cancelled:'failed'}[turn.status] || turn.status || 'pending'}`, turn.status || 'queued')); const open = element('button', 'open-track', 'Open track'); open.onclick = () => {closeThread(); navigate(topicId, 'overview'); setTimeout(() => document.querySelector(`[data-track="run-${CSS.escape(turn.task_run_id)}"]`)?.scrollIntoView({behavior:'smooth', block:'center'}), 150)}; item.append(open)}
    if (turn.passage_changed && ['update', 'task'].includes(turn.intent)) item.append(element('p', 'passage-changed', 'The highlighted passage was changed by this edit.'));
    const usage = formatUsage(turn.usage, turn.cost_usd); if (usage) item.append(element('small', 'muted', usage)); body.append(item);
  });
  const form = intentComposer(thread.turns.at(-1)?.backend || overview.topic.backend, 'Reply', async (question, backend, intent) => {await api(url(`/threads/${thread.id}/turns`), 'POST', {question, backend, intent}); await refreshThreads()}); form.classList.add('dialog-composer'); footer.append(form);
  if (typed) form.querySelector('textarea').value = typed; if (oldIntent) form.querySelector('select[name="intent"]').value = oldIntent; if (oldBackend) form.querySelector('select[name="backend"]').value = oldBackend;
  const actions = element('div', 'dialog-actions'), resolve = element('button', '', thread.resolved ? 'Reopen' : 'Resolve'); resolve.onclick = async () => {await api(url(`/threads/${thread.id}/resolve`), 'POST', {resolved: !thread.resolved}); await refreshThreads()}; actions.append(resolve);
  if (thread.turns.at(-1)?.status === 'error') {const retry = element('button', '', 'Retry'); retry.onclick = async () => {await api(url(`/threads/${thread.id}/retry`), 'POST', {}); await refreshThreads()}; actions.append(retry)}
  const del = element('button', 'danger', 'Delete'); del.onclick = async () => {if (!confirm('Delete this thread?')) return; await api(url(`/threads/${thread.id}`), 'DELETE'); closeThread(); await refreshThreads()}; actions.append(del); footer.append(actions); body.scrollTop = oldScroll;
}
function backendSelect(value) {const select = element('select'); for (const name of ['claude', 'codex']) {const option = element('option', '', name); option.value = name; select.append(option)} select.value = value; return select}
function intentComposer(defaultBackend, placeholder, send) {
  const form = element('form', 'intent-form'), question = element('textarea'), backend = backendSelect(defaultBackend), actions = element('div', 'intent-actions'), intent = element('select');
  question.required = true; question.placeholder = placeholder;
  intent.name = 'intent'; backend.name = 'backend';
  for (const [value, label] of [['auto', 'Auto'], ['explain', 'Explain'], ['update', 'Update doc'], ['task', 'Task']]) {const option = element('option', '', label); option.value = value; intent.append(option)}
  const button = element('button', '', 'Send'); button.type = 'submit'; actions.append(intent, backend, button);
  form.append(question, actions);
  form.onsubmit = async event => {event.preventDefault(); const message = question.value; question.value = ''; try {await send(message, backend.value, intent.value)} catch(error) {question.value = message; toast(error.message)}};
  return form;
}
async function reloadTopic() {const data = await api(url('')); docVersion = data.doc_version; docMarkdown = data.doc; overview = await api(url('/overview')); threads = await api(url('/threads')); renderTabs(); await showView()}
async function refreshThreads() {threads = await api(url('/threads')); if (tab === 'document' || overview.sections.some(section => section.id === tab)) {$('#content').innerHTML = render(tab === 'document' ? docMarkdown : overview.sections.find(section => section.id === tab).markdown, {topicId}); await renderCharts($('#content')); await renderMermaid($('#content')); renderComments()} if (openThreadId) renderDialog()}
// A new comment opens straight in the pop-up, quote on top and the composer below.
function openDraft() {
  if (!selected?.quote?.trim()) return;
  const quote = selected; $('#comment-button').hidden = true; openThreadId = null;
  $('#dialog-quote').textContent = quote.quote; $('#dialog-body').replaceChildren(element('p', 'muted', 'Ask anything about this passage. The reply explains, edits the document, or starts a task, depending on what you write.'));
  const footer = $('#dialog-footer'); footer.replaceChildren();
  const form = intentComposer(overview.topic.backend, 'What does this mean?', async (question, backend, intent) => {const thread = await api(url('/threads'), 'POST', {...quote, section_id: tab === 'document' ? null : tab, question, backend, intent}); await refreshThreads(); openThread(thread.id)});
  footer.append(form); if (!$('#thread-dialog').open) $('#thread-dialog').showModal(); form.querySelector('textarea').focus();
}
// Every comment in one list, including resolved ones and ones whose passage is gone.
function openCommentList() {
  openThreadId = null; $('#dialog-quote').textContent = `${threads.length} comment${threads.length === 1 ? '' : 's'}`;
  const body = $('#dialog-body'); body.replaceChildren(); $('#dialog-footer').replaceChildren();
  for (const thread of threads) {
    const latest = [...thread.turns].reverse().find(turn => turn.role === 'assistant'), item = element('button', 'comment-list-item'); item.type = 'button';
    const flags = [thread.resolved ? 'resolved' : '', !thread.resolved && !document.querySelector(`mark[data-thread="${CSS.escape(thread.id)}"]`) ? 'no highlight here' : ''].filter(Boolean).join(' · ');
    item.append(element('strong', '', thread.quote), inline(latest?.tldr || thread.turns[0]?.text || '', 'span'), element('small', 'muted', `${((n) => `${n} answer${n === 1 ? '' : 's'}`)(thread.turns.filter(turn => turn.role === 'assistant').length)}${flags ? ' · ' + flags : ''}`));
    item.onclick = () => openThread(thread.id); body.append(item);
  }
  if (!threads.length) body.append(element('p', 'muted', 'No comments yet. Select text in the document to add one.'));
  if (!$('#thread-dialog').open) $('#thread-dialog').showModal();
}
function draftCard() {
  draft = true; const slot = $('#draft-slot'); slot.replaceChildren(); const card = element('div', 'comment-card draft'); card.append(element('small', '', selected.quote));
  const form = intentComposer(overview.topic.backend, 'What does this mean?', async (question, backend, intent) => {const thread = await api(url('/threads'), 'POST', {...selected, section_id: tab === 'document' ? null : tab, question, backend, intent}); expanded.add(thread.id); activeId = thread.id; saveExpanded(); draft = false; slot.replaceChildren(); await refreshThreads(); openThread(thread.id)});
  const cancel = element('button', '', 'Cancel'); cancel.type = 'button'; cancel.onclick = () => {draft = false; slot.replaceChildren()}; card.append(form, cancel);
  slot.append(card); slot.style.top = $('#comment-button').style.top; $('#comment-button').hidden = true; form.querySelector('textarea').focus();
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

$('#new-topic').onsubmit = async event => {event.preventDefault(); const form = event.currentTarget; try {const topic = await api('/api/topics', 'POST', {title: form.elements.title.value, statement: form.elements.statement.value, backend: form.elements.backend.value}); form.reset(); await refreshTopics(); navigate(topic.id)} catch(error) {toast(error.message)}};
$('#dialog-close').onclick = closeThread;
$('#thread-dialog').addEventListener('click', event => {if (event.target === $('#thread-dialog')) closeThread()});
$('#thread-dialog').addEventListener('close', () => {openThreadId = null});
$('#instruct-form').onsubmit = async event => {event.preventDefault(); const form = event.currentTarget; try {await api(url('/instruct'), 'POST', {instruction: form.elements.instruction.value, backend: form.elements.backend.value}); form.elements.instruction.value = ''; await showRuns(); overview = await api(url('/overview')); await showView()} catch(error) {toast(error.message)}};
$('#back-to-topics').onclick = () => {leaveTopic(); refreshTopics().catch(error => toast(error.message))};
$('#content').addEventListener('mouseup', () => {if (!(tab === 'document' || overview?.sections.some(section => section.id === tab))) return; const selection = window.getSelection(), root = $('#content'); if (!selection || selection.isCollapsed || !root.contains(selection.anchorNode) || !root.contains(selection.focusNode)) return; const text = root.textContent, start = textOffset(root, selection.anchorNode, selection.anchorOffset), end = textOffset(root, selection.focusNode, selection.focusOffset); selected = capture(text, Math.min(start, end), Math.max(start, end)); if (!selected.quote.trim()) return; const rect = selection.getRangeAt(0).getBoundingClientRect(), container = root.getBoundingClientRect(); const button = $('#comment-button'); // Float the button just above the end of the selection (below it near the top), kept inside the window.
  button.style.top = `${rect.top > 44 ? rect.top - 36 : Math.min(innerHeight - 40, rect.bottom + 6)}px`; button.style.left = `${Math.min(innerWidth - 110, Math.max(8, rect.right - 40))}px`; button.hidden = false});
document.addEventListener('selectionchange', () => {if (window.getSelection()?.isCollapsed) setTimeout(() => {if (window.getSelection()?.isCollapsed) $('#comment-button').hidden = true}, 150)});
$('#comment-button').onclick = openDraft;
$('#comments-list').onclick = openCommentList;
$('#collapse-all').onclick = () => {expanded.clear(); activeId = null; saveExpanded(); renderComments()};
$('#hide-comments').onclick = () => {commentsHidden = true; localStorage.setItem('comments-hidden', 'true'); $('#comment-margin').hidden = true; $('#section-layout').classList.remove('with-comments'); $('#show-comments').hidden = false; $('#show-comments').textContent = `Show comments (${threads.filter(thread => !thread.resolved).length})`};
$('#show-comments').onclick = () => {commentsHidden = false; localStorage.setItem('comments-hidden', 'false'); $('#comment-margin').hidden = false; $('#section-layout').classList.add('with-comments'); $('#show-comments').hidden = true; renderComments()};
document.addEventListener('click', event => {const citation = event.target.closest('a[href^="#src-"]'); if (!citation || !overview) return; const target = citationTarget(citation.getAttribute('href').slice(5), overview.tabs); if (target) {event.preventDefault(); navigate(topicId, target.tab); setTimeout(() => document.getElementById(target.anchor)?.scrollIntoView({behavior: 'smooth'}), 100)}});
addEventListener('popstate', () => route().catch(error => toast(error.message)));
addEventListener('resize', () => {if (topicId && (tab === 'document' || overview?.sections.some(section => section.id === tab))) renderComments()});
async function poll() {if (topicId && (activeRuns || Date.now() - lastPoll >= 10000)) try {lastPoll = Date.now(); const version = await api(url('/version')); if (version.doc_version !== docVersion || version.active_runs || activeRuns) {const data = await api(url('')); docVersion = data.doc_version; docMarkdown = data.doc; overview = await api(url('/overview')); threads = await api(url('/threads')); renderTabs(); await showView(); if (openThreadId) renderDialog()} await showRuns()} catch(error) {toast(error.message)} setTimeout(poll, 2000)}
setFocus(false); refreshTopics().then(() => route()).catch(error => toast(error.message)); setTimeout(poll, 2000);
