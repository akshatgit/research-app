// R6: compact token/cost line on each answer.
// web/lib/usage.mjs exports formatUsage(usage, costUsd) -> string
//   usage: {input, cached, cache_write, output} (any may be missing) or null
//   Parts in this order, skipping zero cached/written: "<n> cached", "<n> written",
//   "<n> in", "<n> out", then "$<cost>" when cost is a number.
//   Numbers: < 1000 as is; < 1e6 as one-decimal "k"; else one-decimal "M".
//   Cost: two decimals, or "<$0.01" when below a cent.
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatUsage } from "../../web/lib/usage.mjs";

test("claude-style usage with cost", () => {
  assert.equal(formatUsage({ input: 2, cached: 11267, cache_write: 12271, output: 4 }, 0.1005094),
    "11.3k cached · 12.3k written · 2 in · 4 out · $0.10");
});

test("codex-style usage without cost", () => {
  assert.equal(formatUsage({ input: 2796, cached: 11776, cache_write: 0, output: 5 }, null),
    "11.8k cached · 2.8k in · 5 out");
});

test("small cost and big numbers", () => {
  assert.equal(formatUsage({ input: 1_260_000, output: 900 }, 0.004), "1.3M in · 900 out · <$0.01");
});

test("nothing to show", () => {
  assert.equal(formatUsage(null, null), "");
  assert.equal(formatUsage({}, null), "0 in · 0 out");
});
