"""D2, D3, D4, D5: the running document's structure.

A visualization is a fenced block in doc.md:

    ```viz
    {"type": "line", "data": "assets/mentions.csv", "x": "year", "y": ["talks"],
     "title": "Talks per year", "source": "SREcon programs 2016-2026"}
    ```

Types: line, bar, area, scatter, table. `data` is a CSV or JSON file inside the workspace.
Diagrams use ```mermaid fences and images use ![alt](assets/x.svg).
"""
import json

import pytest

from research_app.docmodel import (VIZ_TYPES, check_citations, load_table, parse_sources,
                                   parse_viz_blocks, validate_viz)

DOC = """# Formal verification for SRE

Formal methods are used at AWS [1] and in some SRE teams [2].

```viz
{"type": "line", "data": "assets/talks.csv", "x": "year", "y": ["talks"], "title": "Talks", "source": "SREcon programs"}
```

Some code that is not a viz:

```python
print("[9] is not a citation here")
```

```viz
{not valid json
```

```mermaid
graph TD; A-->B
```

## Sources

1. [How AWS uses formal methods](https://cacm.acm.org/research/how-amazon-web-services-uses-formal-methods/)
2. [Formal methods at Datadog](https://www.datadoghq.com/blog/engineering/formal-modeling-and-simulation/)
3. [Unused source](https://example.com/unused)
"""


def test_viz_types():
    assert set(VIZ_TYPES) == {"line", "bar", "area", "scatter", "table"}


class TestParseViz:
    def test_finds_blocks_in_order_with_index_and_line(self):
        blocks = parse_viz_blocks(DOC)
        assert [b["index"] for b in blocks] == [0, 1]
        assert blocks[0]["spec"]["type"] == "line"
        assert blocks[0]["spec"]["y"] == ["talks"]
        assert DOC.splitlines()[blocks[0]["line"] - 1].startswith("```viz")

    def test_invalid_json_is_reported_not_raised(self):
        blocks = parse_viz_blocks(DOC)
        assert blocks[1]["spec"] is None and blocks[1]["error"]

    def test_ignores_other_fences(self):
        assert all(b.get("spec") is None or b["spec"]["type"] in VIZ_TYPES
                   for b in parse_viz_blocks(DOC))
        assert len(parse_viz_blocks("```python\nx\n```\n```mermaid\ny\n```")) == 0


class TestValidateViz:
    @pytest.fixture
    def ws(self, tmp_path):
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "talks.csv").write_text("year,talks\n2024,3\n2025,5\n")
        return tmp_path

    def test_valid_spec(self, ws):
        spec = {"type": "line", "data": "assets/talks.csv", "x": "year", "y": ["talks"],
                "title": "T", "source": "S"}
        assert validate_viz(spec, ws) == []

    def test_y_may_be_a_single_column_name(self, ws):
        assert validate_viz({"type": "bar", "data": "assets/talks.csv", "x": "year",
                             "y": "talks", "title": "T", "source": "S"}, ws) == []

    @pytest.mark.parametrize("change,needle", [
        ({"type": "pie"}, "type"),
        ({"data": "assets/missing.csv"}, "missing.csv"),
        ({"data": "../outside.csv"}, "workspace"),
        ({"x": "month"}, "month"),
        ({"y": ["talks", "views"]}, "views"),
        ({"source": ""}, "source"),
    ])
    def test_errors_name_the_problem(self, ws, change, needle):
        spec = {"type": "line", "data": "assets/talks.csv", "x": "year", "y": ["talks"],
                "title": "T", "source": "S", **change}
        errs = validate_viz(spec, ws)
        assert errs and any(needle in e for e in errs)

    def test_table_needs_no_axes(self, ws):
        assert validate_viz({"type": "table", "data": "assets/talks.csv", "title": "T",
                             "source": "S"}, ws) == []


class TestLoadTable:
    def test_csv_with_number_coercion(self, tmp_path):
        p = tmp_path / "t.csv"
        p.write_text("year,talks,rate,label\n2024,3,0.5,a\n2025,,1e3,b\n")
        t = load_table(p)
        assert t["columns"] == ["year", "talks", "rate", "label"]
        assert t["rows"] == [[2024, 3, 0.5, "a"], [2025, None, 1000.0, "b"]]

    def test_json_records(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps([{"year": 2024, "talks": 3}, {"year": 2025, "talks": 5}]))
        assert load_table(p) == {"columns": ["year", "talks"], "rows": [[2024, 3], [2025, 5]]}

    def test_json_columns_rows(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(json.dumps({"columns": ["a"], "rows": [[1]]}))
        assert load_table(p) == {"columns": ["a"], "rows": [[1]]}

    def test_rejects_huge_files(self, tmp_path):
        p = tmp_path / "big.csv"
        p.write_text("a\n" + "1\n" * 3_000_000)
        with pytest.raises(ValueError):
            load_table(p, max_rows=100_000)


class TestSourcesAndCitations:
    def test_parse_sources(self):
        s = parse_sources(DOC)
        assert s[1]["title"] == "How AWS uses formal methods"
        assert s[2]["url"].startswith("https://www.datadoghq.com/")
        assert set(s) == {1, 2, 3}

    def test_citations_ignore_code_blocks(self):
        c = check_citations(DOC)
        assert c["missing"] == []       # [9] sits inside a code block
        assert c["unused"] == [3]

    def test_missing_citation(self):
        assert check_citations("Claim [4].\n\n## Sources\n\n1. [A](https://a.example)\n") == \
            {"missing": [4], "unused": [1]}

    def test_no_sources_section(self):
        assert parse_sources("# Title\n\nNo sources yet.") == {}
