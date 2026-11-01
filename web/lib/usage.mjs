export function formatUsage(usage, costUsd) {
  if (!usage && costUsd == null) return '';
  const count = value => {
    const n = Number(value) || 0;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
  };
  const parts = [];
  if (usage?.cached) parts.push(`${count(usage.cached)} cached`);
  if (usage?.cache_write) parts.push(`${count(usage.cache_write)} written`);
  if (usage) parts.push(`${count(usage.input)} in`, `${count(usage.output)} out`);
  if (typeof costUsd === 'number') parts.push(costUsd < 0.01 ? '<$0.01' : `$${costUsd.toFixed(2)}`);
  return parts.join(' · ');
}
