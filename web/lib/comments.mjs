export function layoutCards(cards, {gap = 8, activeId = null} = {}) {
  const ordered = [...cards].sort((a, b) => a.anchorTop - b.anchorTop);
  const tops = {};
  let floor = 0;
  for (const card of ordered) {
    tops[card.id] = Math.max(floor, card.anchorTop);
    floor = tops[card.id] + card.height + gap;
  }
  const active = ordered.findIndex(card => card.id === activeId);
  if (active >= 0) {
    let top = Math.max(0, ordered[active].anchorTop);
    for (let i = active - 1; i >= 0; i--) {
      top -= ordered[i].height + gap;
      if (top < 0) { top = 0; break; }
    }
    if (top === 0) {
      let needed = 0;
      for (let i = 0; i < active; i++) needed += ordered[i].height + gap;
      tops[ordered[active].id] = Math.max(ordered[active].anchorTop, needed);
    } else tops[ordered[active].id] = ordered[active].anchorTop;
    for (let i = active - 1; i >= 0; i--)
      tops[ordered[i].id] = Math.max(0, tops[ordered[i + 1].id] - ordered[i].height - gap);
    for (let i = active + 1; i < ordered.length; i++)
      tops[ordered[i].id] = Math.max(ordered[i].anchorTop, tops[ordered[i - 1].id] + ordered[i - 1].height + gap);
  }
  return tops;
}

export function summarize(thread) {
  const turns = thread.turns || [];
  const quote = thread.quote || '';
  const last = turns.at(-1);
  return {quote: quote.length > 80 ? quote.slice(0, 80) + '…' : quote,
    question: turns.find(turn => turn.role === 'user')?.text || '',
    replies: Math.max(0, turns.length - 1),
    status: !last || last.role === 'user' || ['pending', 'queued', 'running'].includes(last.status) ? 'answering' : last.status === 'error' ? 'error' : 'done'};
}
