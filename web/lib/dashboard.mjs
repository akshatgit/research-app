export function parseRoute(hash) {
  const parts = String(hash || '').replace(/^#/, '').split('/');
  if (!parts[0]) return {topicId: null, tab: null};
  return {topicId: decodeURIComponent(parts[0]), tab: decodeURIComponent(parts[1] || 'overview')};
}

export function routeFor(topicId, tab = 'overview') {
  return `#${encodeURIComponent(topicId)}/${encodeURIComponent(tab)}`;
}

export function relativeTime(ts, now = Date.now() / 1000) {
  if (ts == null) return '—';
  const seconds = Math.floor(now - ts);
  if (seconds < 0) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function citationTarget(n, tabs) {
  const tab = tabs.some(item => item.id === 'sources') ? 'sources' :
    tabs.some(item => item.id === 'document') ? 'document' : null;
  return tab ? {tab, anchor: `src-${n}`} : null;
}
