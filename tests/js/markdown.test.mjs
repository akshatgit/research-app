// B4/D3/D5/N6: rendering the running document safely.
// web/lib/markdown.mjs exports:
//   render(md, {topicId}) -> HTML string
// Model output is untrusted: no raw HTML passes through, links are made safe, remote images
// are not loaded, and viz/mermaid fences become placeholders the page fills in.
import { test } from "node:test";
import assert from "node:assert/strict";
import { render } from "../../web/lib/markdown.mjs";

const opts = { topicId: "t1" };

test("headings get ids for linking", () => {
  const h = render("# Formal verification\n\n## Burn rate & budget", opts);
  assert.match(h, /<h1 id="formal-verification">Formal verification<\/h1>/);
  assert.match(h, /<h2 id="burn-rate-budget">Burn rate &amp; budget<\/h2>/);
});

test("paragraphs, emphasis, inline code", () => {
  const h = render("Plain **bold** and *it* and `x < y`.", opts);
  assert.match(h, /<p>Plain <strong>bold<\/strong> and <em>it<\/em> and <code>x &lt; y<\/code>\.<\/p>/);
});

test("lists and tables", () => {
  const h = render("- a\n- b\n\n1. one\n2. two\n\n| k | v |\n|---|---|\n| 1h | 2% |", opts);
  assert.match(h, /<ul>\s*<li>a<\/li>\s*<li>b<\/li>\s*<\/ul>/);
  assert.match(h, /<ol>\s*<li>one<\/li>\s*<li>two<\/li>\s*<\/ol>/);
  assert.match(h, /<table>[\s\S]*<th>k<\/th>[\s\S]*<td>2%<\/td>[\s\S]*<\/table>/);
});

test("code fences are escaped and not further processed", () => {
  const h = render("```python\nprint('<b>[3]</b>')\n```", opts);
  // a single quote may stay as-is or be escaped as &#39; / &#x27;
  assert.match(h, /<pre><code class="language-python">print\((?:'|&#39;|&#x27;)&lt;b&gt;\[3\]&lt;\/b&gt;(?:'|&#39;|&#x27;)\)\n?<\/code><\/pre>/);
  assert.doesNotMatch(h, /class="cite"/);
});

test("raw HTML from the model is escaped", () => {
  const h = render("Hi <script>alert(1)</script> <img src=x onerror=alert(1)>", opts);
  assert.doesNotMatch(h, /<script/i);
  assert.doesNotMatch(h, /<img/i);
  assert.match(h, /&lt;script&gt;/);
});

test("links open in a new tab and javascript: URLs are neutralised", () => {
  const h = render("[ok](https://example.com) [bad](javascript:alert(1)) [data](data:text/html,x)", opts);
  assert.match(h, /<a href="https:\/\/example\.com" target="_blank" rel="noopener noreferrer">ok<\/a>/);
  assert.doesNotMatch(h, /href="javascript:/i);
  assert.doesNotMatch(h, /href="data:/i);
});

test("citations link to the sources list, which gets anchors", () => {
  const h = render("AWS uses TLA+ [1][2].\n\n## Sources\n\n1. [A](https://a.example)\n2. [B](https://b.example)", opts);
  assert.match(h, /<a class="cite" href="#src-1">\[1\]<\/a>/);
  assert.match(h, /<a class="cite" href="#src-2">\[2\]<\/a>/);
  assert.match(h, /<li id="src-1">/);
  assert.match(h, /<li id="src-2">/);
});

test("markdown link text like [1](url) is a link, not a citation", () => {
  const h = render("See [1](https://x.example).", opts);
  assert.doesNotMatch(h, /class="cite"/);
});

test("viz fences become indexed placeholders carrying the spec", () => {
  const spec = { type: "line", data: "assets/t.csv", x: "year", y: ["n"], title: "N", source: "s" };
  const h = render("Intro\n\n```viz\n" + JSON.stringify(spec) + "\n```\n\n```viz\n{\"type\":\"bar\"}\n```", opts);
  const m = [...h.matchAll(/<div class="viz" data-index="(\d)" data-spec="([^"]*)"><\/div>/g)];
  assert.equal(m.length, 2);
  assert.equal(m[0][1], "0");
  const decoded = m[0][2].replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  assert.deepEqual(JSON.parse(decoded), spec);
});

test("mermaid fences become mermaid blocks with escaped source", () => {
  const h = render("```mermaid\ngraph TD; A-->B<script>\n```", opts);
  assert.match(h, /<pre class="mermaid">graph TD; A--&gt;B&lt;script&gt;\n?<\/pre>/);
});

test("workspace images are served through the files API; remote images are not loaded", () => {
  const h = render("![chart](assets/c.svg)\n\n![tracker](https://evil.example/p.gif)\n\n![up](../x.png)", opts);
  assert.match(h, /<img src="\/api\/topics\/t1\/files\/assets\/c\.svg" alt="chart"/);
  assert.doesNotMatch(h, /<img[^>]*evil\.example/);
  assert.match(h, /<a href="https:\/\/evil\.example\/p\.gif"[^>]*>tracker<\/a>/);
  assert.doesNotMatch(h, /<img[^>]*\.\.\/x\.png/);
});
