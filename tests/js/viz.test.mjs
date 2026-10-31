// D3/D4: turning a viz spec plus a table into chart series.
// web/lib/viz.mjs exports:
//   buildSeries(spec, table) -> {x: [...], series: [{name, values: [...]}]}   (throws Error naming a missing column)
//   sourceLabel(spec) -> string shown under every chart, e.g. "Data: assets/t.csv · Source: SREcon programs"
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSeries, sourceLabel } from "../../web/lib/viz.mjs";

const table = { columns: ["year", "talks", "papers"], rows: [[2024, 3, 7], [2025, null, 9], [2026, 5, 11]] };

test("single y column (string or array)", () => {
  for (const y of ["talks", ["talks"]]) {
    const s = buildSeries({ type: "line", x: "year", y }, table);
    assert.deepEqual(s.x, [2024, 2025, 2026]);
    assert.deepEqual(s.series, [{ name: "talks", values: [3, null, 5] }]);
  }
});

test("multiple y columns keep their order", () => {
  const s = buildSeries({ type: "bar", x: "year", y: ["papers", "talks"] }, table);
  assert.deepEqual(s.series.map((x) => x.name), ["papers", "talks"]);
});

test("missing column throws with its name", () => {
  assert.throws(() => buildSeries({ type: "line", x: "month", y: "talks" }, table), /month/);
  assert.throws(() => buildSeries({ type: "line", x: "year", y: ["views"] }, table), /views/);
});

test("table type returns all columns", () => {
  const s = buildSeries({ type: "table" }, table);
  assert.equal(s.series.length, 3);
});

test("every chart says where its data came from", () => {
  assert.equal(sourceLabel({ data: "assets/t.csv", source: "SREcon programs" }),
    "Data: assets/t.csv · Source: SREcon programs");
  assert.equal(sourceLabel({ data: "assets/t.csv" }), "Data: assets/t.csv");
});
