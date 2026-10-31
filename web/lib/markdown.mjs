const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = url => /^https?:\/\//i.test(url) ? url : null;
const slug = text => text.toLowerCase().replace(/[^a-z0-9 ]/g, '').trim().replace(/\s+/g, '-');

export function render(md, {topicId = ''} = {}) {
  const lines = md.replace(/\r\n?/g, '\n').split('\n');
  let out = [], i = 0, viz = 0, sources = false;
  const inline = raw => {
    let s = esc(raw);
    const tokens = [];
    const hold = html => `\u0001${tokens.push(html) - 1}\u0002`;
    s = s.replace(/`([^`]+)`/g, (_, code) => hold(`<code>${code}</code>`));
    s = s.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, url) => {
      const clean = url.replace(/&amp;/g, '&');
      if (/^https?:\/\//i.test(clean)) return hold(`<a href="${esc(clean)}" target="_blank" rel="noopener noreferrer">${alt}</a>`);
      if (/^(?!\/|\.|.*\.\.)[a-zA-Z0-9_/-]+\.(?:svg|png|jpg|jpeg|gif|webp)$/.test(clean))
        return hold(`<img src="/api/topics/${encodeURIComponent(topicId)}/files/${clean.split('/').map(encodeURIComponent).join('/')}" alt="${alt}">`);
      return alt;
    });
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, url) => {
      const clean = url.replace(/&amp;/g, '&');
      return hold(safeUrl(clean) ? `<a href="${esc(clean)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label);
    });
    s = s.replace(/\[(\d+)\]/g, (_, n) => hold(`<a class="cite" href="#src-${n}">[${n}]</a>`));
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return s.replace(/\u0001(\d+)\u0002/g, (_, n) => tokens[Number(n)]);
  };
  while (i < lines.length) {
    let line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      i++; let body = '';
      while (i < lines.length && !/^```\s*$/.test(lines[i])) body += lines[i++] + '\n';
      i++;
      if (fence[1] === 'viz') out.push(`<div class="viz" data-index="${viz++}" data-spec="${esc(body.trim())}"></div>`);
      else if (fence[1] === 'mermaid') out.push(`<pre class="mermaid">${esc(body)}</pre>`);
      else out.push(`<pre><code class="language-${esc(fence[1])}">${esc(body)}</code></pre>`);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      if (heading[2].trim().toLowerCase() === 'sources') sources = true;
      out.push(`<h${level} id="${slug(heading[2])}">${inline(heading[2])}</h${level}>`); i++; continue;
    }
    if (/^\|.*\|$/.test(line) && /^\|[\s:|-]+\|$/.test(lines[i+1] || '')) {
      const cells = s => s.slice(1,-1).split('|').map(x => x.trim());
      const headers = cells(line); i += 2;
      let html = '<table><thead><tr>' + headers.map(x => `<th>${inline(x)}</th>`).join('') + '</tr></thead><tbody>';
      while (i < lines.length && /^\|.*\|$/.test(lines[i])) html += '<tr>' + cells(lines[i++]).map(x => `<td>${inline(x)}</td>`).join('') + '</tr>';
      out.push(html + '</tbody></table>'); continue;
    }
    const bullet = line.match(/^(-|\d+\.)\s+/);
    if (bullet) {
      const ordered = bullet[1] !== '-', tag = ordered ? 'ol' : 'ul';
      let html = `<${tag}>`;
      while (i < lines.length && (ordered ? /^\d+\.\s+/.test(lines[i]) : /^-\s+/.test(lines[i]))) {
        const m = lines[i++].match(/^(-|\d+\.)\s+(.*)$/);
        const num = ordered && sources ? m[1].slice(0,-1) : null;
        html += `<li${num ? ` id="src-${num}"` : ''}>${inline(m[2])}</li>`;
      }
      out.push(html + `</${tag}>`); continue;
    }
    let para = [line]; i++;
    while (i < lines.length && lines[i].trim() && !/^(#|```|- |\d+\. |\|)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inline(para.join(' '))}</p>`);
  }
  return out.join('\n');
}
