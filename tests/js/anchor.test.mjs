// H6: highlights re-anchor by quote + surrounding text as the document grows.
// web/lib/anchor.mjs exports:
//   capture(text, start, end, ctx = 40) -> {quote, prefix, suffix, start}
//   locate(text, anchor) -> {start, end} | null      (null = orphaned)
import { test } from "node:test";
import assert from "node:assert/strict";
import { capture, locate } from "../../web/lib/anchor.mjs";

const DOC = "Each rule fires once the incident has used factor x long window of budget. " +
  "A faster burn just gets there sooner. The burn rate cancels out of the formula.";

test("capture records quote, context and offset", () => {
  const start = DOC.indexOf("burn rate cancels");
  const a = capture(DOC, start, start + "burn rate cancels".length, 10);
  assert.equal(a.quote, "burn rate cancels");
  assert.equal(a.start, start);
  assert.equal(a.prefix, DOC.slice(start - 10, start));
  assert.equal(a.suffix, DOC.slice(start + 17, start + 27));
});

test("capture clamps context at document edges", () => {
  const a = capture(DOC, 0, 4, 40);
  assert.equal(a.prefix, "");
  assert.equal(a.quote, "Each");
});

test("locate finds an unchanged anchor at its offset", () => {
  const start = DOC.indexOf("faster burn");
  const a = capture(DOC, start, start + 11);
  assert.deepEqual(locate(DOC, a), { start, end: start + 11 });
});

test("locate follows text that moved because content was added before it", () => {
  const start = DOC.indexOf("faster burn");
  const a = capture(DOC, start, start + 11);
  const grown = "## New section\n\nLots of new research text here.\n\n" + DOC;
  const r = locate(grown, a);
  assert.equal(grown.slice(r.start, r.end), "faster burn");
});

test("locate picks the right duplicate using context", () => {
  const text = "The 1h page fires at 2%. Later: the 6h page fires at 5%. And the 1h page clears.";
  const second = text.lastIndexOf("1h page");
  const a = capture(text, second, second + 7, 12);
  // shift everything so the stored offset is stale
  const shifted = "Intro. " + text;
  const r = locate(shifted, a);
  assert.equal(r.start, second + "Intro. ".length);
});

test("locate tolerates whitespace differences (selection across line breaks)", () => {
  const text = "factor x long\nwindow of budget";
  const a = { quote: "long window of", prefix: "factor x ", suffix: " budget", start: 9 };
  const r = locate(text, a);
  assert.ok(r, "should match despite newline vs space");
  assert.equal(text.slice(r.start, r.end).replace(/\s+/g, " "), "long window of");
});

test("locate returns null when the passage was removed (orphaned)", () => {
  const start = DOC.indexOf("faster burn");
  const a = capture(DOC, start, start + 11);
  assert.equal(locate(DOC.replace("A faster burn just gets there sooner. ", ""), a), null);
});

test("locate never matches an empty quote", () => {
  assert.equal(locate(DOC, { quote: "", prefix: "", suffix: "", start: 0 }), null);
});
