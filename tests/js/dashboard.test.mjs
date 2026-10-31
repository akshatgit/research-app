// TD2, TD4, TD6: pure helpers for the topic dashboard.
// web/lib/dashboard.mjs exports:
//   parseRoute(hash) -> {topicId, tab}          ("" or "#" -> {topicId: null, tab: null})
//   routeFor(topicId, tab = "overview") -> "#<topicId>/<tab>"
//   relativeTime(ts, now) -> "7s ago" | "52m ago" | "3h ago" | "2d ago" | "—" (ts null)
//   citationTarget(n, sections) -> {tab: "sources", anchor: "src-<n>"} | null (no Sources tab)
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRoute, routeFor, relativeTime, citationTarget } from "../../web/lib/dashboard.mjs";

test("parseRoute", () => {
  assert.deepEqual(parseRoute("#abc123/overview"), { topicId: "abc123", tab: "overview" });
  assert.deepEqual(parseRoute("#abc123/live-experiments-2"), { topicId: "abc123", tab: "live-experiments-2" });
  assert.deepEqual(parseRoute("#abc123"), { topicId: "abc123", tab: "overview" });  // bare topic -> Overview
  assert.deepEqual(parseRoute(""), { topicId: null, tab: null });
  assert.deepEqual(parseRoute("#"), { topicId: null, tab: null });
});

test("routeFor round-trips", () => {
  assert.equal(routeFor("abc123"), "#abc123/overview");
  assert.equal(routeFor("abc123", "sources"), "#abc123/sources");
  assert.deepEqual(parseRoute(routeFor("t1", "assets")), { topicId: "t1", tab: "assets" });
});

test("relativeTime", () => {
  const now = 1_000_000;
  assert.equal(relativeTime(now - 7, now), "7s ago");
  assert.equal(relativeTime(now - 52 * 60, now), "52m ago");
  assert.equal(relativeTime(now - 3 * 3600 - 5, now), "3h ago");
  assert.equal(relativeTime(now - 2 * 86400, now), "2d ago");
  assert.equal(relativeTime(now + 3, now), "just now");
  assert.equal(relativeTime(null, now), "—");
});

test("citationTarget", () => {
  const sections = [{ id: "summary" }, { id: "the-policy" }, { id: "sources" }];
  assert.deepEqual(citationTarget(3, sections), { tab: "sources", anchor: "src-3" });
  assert.equal(citationTarget(3, [{ id: "summary" }]), null);
});
