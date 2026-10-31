export function buildSeries(spec, table) {
  if (spec.type === 'table') return {x: table.rows.map((_, i) => i),
    series: table.columns.map((name, i) => ({name, values: table.rows.map(row => row[i])}))};
  const index = name => {
    const i = table.columns.indexOf(name);
    if (i < 0) throw new Error(`Missing column: ${name}`);
    return i;
  };
  const xi = index(spec.x);
  const ys = Array.isArray(spec.y) ? spec.y : [spec.y];
  return {x: table.rows.map(row => row[xi]),
    series: ys.map(name => ({name, values: table.rows.map(row => row[index(name)] )}))};
}

export function sourceLabel(spec) {
  return `Data: ${spec.data}` + (spec.source ? ` · Source: ${spec.source}` : '');
}
