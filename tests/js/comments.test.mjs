// H11, H13: Google Docs-style comment margin.
// web/lib/comments.mjs exports:
//   layoutCards(cards, {gap = 8, activeId = null}) -> {[id]: top}
//       cards: [{id, anchorTop, height}] (any order). Each card wants to sit at its anchorTop.
//       Cards keep document order, never overlap (gap px between them), and the active card
//       sits exactly at its anchorTop: cards above it move up as needed (never above 0, in
//       which case the active card moves down just enough), cards below move down.
//   summarize(thread) -> {quote, question, replies, status}
//       quote: first 80 chars of the passage (with "…" if cut); question: first user turn;
//       replies: number of turns after the first; status: "answering" if the last turn is
//       pending/running/queued (or the last turn is the user's), "error" if it errored, else "done".
import { test } from "node:test";
import assert from "node:assert/strict";
import { layoutCards, summarize } from "../../web/lib/comments.mjs";

const noOverlap = (cards, tops, gap) => {
  const sorted = [...cards].sort((a, b) => tops[a.id] - tops[b.id]);
  for (let i = 1; i < sorted.length; i++) {
    assert.ok(tops[sorted[i].id] >= tops[sorted[i - 1].id] + sorted[i - 1].height + gap,
      `${sorted[i].id} overlaps ${sorted[i - 1].id}`);
  }
};

test("cards with room sit beside their passages", () => {
  const cards = [{ id: "a", anchorTop: 100, height: 50 }, { id: "b", anchorTop: 400, height: 50 }];
  assert.deepEqual(layoutCards(cards, { gap: 8 }), { a: 100, b: 400 });
});

test("colliding cards are pushed down in document order", () => {
  const cards = [{ id: "b", anchorTop: 120, height: 60 }, { id: "a", anchorTop: 100, height: 80 },
    { id: "c", anchorTop: 130, height: 40 }];
  const tops = layoutCards(cards, { gap: 8 });
  assert.equal(tops.a, 100);
  assert.equal(tops.b, 188);   // 100 + 80 + 8
  assert.equal(tops.c, 256);   // 188 + 60 + 8
  noOverlap(cards, tops, 8);
});

test("active card sits at its anchor; cards above move up", () => {
  const cards = [{ id: "a", anchorTop: 100, height: 80 }, { id: "b", anchorTop: 120, height: 60 }];
  const tops = layoutCards(cards, { gap: 8, activeId: "b" });
  assert.equal(tops.b, 120);
  assert.equal(tops.a, 32);    // 120 - 8 - 80
  noOverlap(cards, tops, 8);
});

test("active card moves down only if the cards above would go above 0", () => {
  const cards = [{ id: "a", anchorTop: 10, height: 80 }, { id: "b", anchorTop: 20, height: 60 }];
  const tops = layoutCards(cards, { gap: 8, activeId: "b" });
  assert.equal(tops.a, 0);
  assert.equal(tops.b, 88);
  noOverlap(cards, tops, 8);
});

test("empty input", () => {
  assert.deepEqual(layoutCards([], {}), {});
});

const turn = (role, text, status) => ({ role, text, status, backend: "claude" });

test("summarize a finished thread", () => {
  const th = { quote: "x".repeat(100), turns: [turn("user", "What does this mean?"),
    turn("assistant", "It means...", "done"), turn("user", "And then?"), turn("assistant", "Then...", "done")] };
  const s = summarize(th);
  assert.equal(s.quote, "x".repeat(80) + "…");
  assert.equal(s.question, "What does this mean?");
  assert.equal(s.replies, 3);
  assert.equal(s.status, "done");
});

test("summarize statuses", () => {
  assert.equal(summarize({ quote: "q", turns: [turn("user", "?")] }).status, "answering");
  assert.equal(summarize({ quote: "q", turns: [turn("user", "?"), turn("assistant", "", "running")] }).status, "answering");
  assert.equal(summarize({ quote: "q", turns: [turn("user", "?"), turn("assistant", "", "error")] }).status, "error");
  assert.equal(summarize({ quote: "short", turns: [turn("user", "?")] }).quote, "short");
});
