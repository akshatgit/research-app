export function capture(text, start, end, ctx = 40) {
  return { quote: text.slice(start, end), prefix: text.slice(Math.max(0, start - ctx), start),
    suffix: text.slice(end, end + ctx), start };
}

export function locate(text, anchor) {
  if (!anchor.quote) return null;
  const norm = s => s.replace(/\s+/g, ' ').trim();
  const words = anchor.quote.trim().split(/\s+/).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const pattern = new RegExp(words.join('\\s+'), 'g');
  let matches = [], m;
  while ((m = pattern.exec(text))) {
    const start = m.index, end = start + m[0].length;
    const before = norm(text.slice(Math.max(0, start - anchor.prefix.length - 15), start));
    const after = norm(text.slice(end, end + anchor.suffix.length + 15));
    const prefix = norm(anchor.prefix), suffix = norm(anchor.suffix);
    let score = -Math.abs(start - (anchor.start || 0)) / 1000;
    if (prefix && before.endsWith(prefix)) score += 100;
    if (suffix && after.startsWith(suffix)) score += 100;
    matches.push({start, end, score});
  }
  matches.sort((a, b) => b.score - a.score);
  return matches.length ? {start: matches[0].start, end: matches[0].end} : null;
}
