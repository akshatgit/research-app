"""Document metadata and visualization validation."""
import csv
import json
import re
from .fsutil import safe_join

VIZ_TYPES = ('line', 'bar', 'area', 'scatter', 'table')
FENCE = re.compile(r'^```(\w*)\s*$', re.M)


def _without_fences(doc):
    return re.sub(r'^```[^\n]*\n.*?^```\s*$', '', doc, flags=re.M | re.S)


def parse_viz_blocks(doc):
    blocks = []
    for match in re.finditer(r'^```viz\s*\n(.*?)^```', doc, flags=re.M | re.S):
        body = match.group(1)
        block = {'index': len(blocks), 'line': doc.count('\n', 0, match.start()) + 1, 'spec': None, 'error': None}
        try:
            block['spec'] = json.loads(body)
        except ValueError as exc:
            block['error'] = str(exc)
        blocks.append(block)
    return blocks


def _coerce(value):
    if value == '':
        return None
    try:
        n = float(value)
        return int(n) if n.is_integer() else n
    except (ValueError, TypeError):
        return value


def load_table(path, max_rows=100_000):
    if path.stat().st_size > 20_000_000:
        raise ValueError('table too large')
    if path.suffix.lower() == '.json':
        data = json.loads(path.read_text())
        if isinstance(data, list):
            columns = list(data[0]) if data else []
            rows = [[r.get(c) for c in columns] for r in data]
        else:
            columns, rows = data['columns'], data['rows']
    elif path.suffix.lower() == '.csv':
        with path.open(newline='') as file:
            reader = csv.reader(file)
            columns = next(reader, [])
            rows = []
            for row in reader:
                if len(rows) >= max_rows:
                    raise ValueError('too many rows')
                rows.append([_coerce(x) for x in row])
    else:
        raise ValueError('table must be CSV or JSON')
    if len(rows) > max_rows:
        raise ValueError('too many rows')
    return {'columns': columns, 'rows': rows}


def validate_viz(spec, workspace):
    errors = []
    if not isinstance(spec, dict):
        return ['invalid spec']
    if spec.get('type') not in VIZ_TYPES:
        errors.append('invalid type')
    path = None
    try:
        path = safe_join(workspace, spec.get('data', ''))
        if not path.is_file():
            errors.append(f"missing data file: {spec.get('data')}")
    except (ValueError, TypeError):
        errors.append('data path outside workspace')
    for key in ('title', 'source'):
        if not spec.get(key):
            errors.append(f'missing {key}')
    if path and path.is_file():
        try:
            columns = load_table(path)['columns']
            if spec.get('type') != 'table':
                for col in [spec.get('x'), *(spec.get('y') if isinstance(spec.get('y'), list) else [spec.get('y')])]:
                    if col not in columns:
                        errors.append(f'missing column: {col}')
        except (ValueError, KeyError) as exc:
            errors.append(str(exc))
    return errors


def parse_sources(doc):
    plain = _without_fences(doc)
    m = re.search(r'^## Sources\s*\n(.*?)(?=^## |\Z)', plain, flags=re.M | re.S | re.I)
    if not m:
        return {}
    return {int(n): {'title': title, 'url': url} for n, title, url in
            re.findall(r'^\s*(\d+)\. \[([^]]+)\]\(([^)]+)\)', m.group(1), flags=re.M)}


def check_citations(doc):
    sources = parse_sources(doc)
    plain = _without_fences(doc)
    plain = re.sub(r'^## Sources\s*\n.*?\Z', '', plain, flags=re.M | re.S | re.I)
    cited = {int(n) for n in re.findall(r'\[(\d+)\](?!\()', plain)}
    return {'missing': sorted(cited - sources.keys()), 'unused': sorted(sources.keys() - cited)}
