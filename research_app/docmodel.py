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


def split_sections(md):
    """Split level-two sections while ignoring headings inside fenced code."""
    if not md.strip():
        return []
    lines = md.splitlines(keepends=True)
    starts = []
    fence = None
    for index, line in enumerate(lines):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            heading = re.match(r'^##\s+(.+?)\s*$', line)
            if heading:
                starts.append((index, heading.group(1).strip().rstrip('#').strip()))
    sections = []
    preface = ''.join(lines[:starts[0][0] if starts else len(lines)])
    preface = re.sub(r'^#\s+[^\n]*(?:\n|$)', '', preface, count=1).strip()
    if preface:
        sections.append({'id': 'summary', 'title': 'Summary', 'markdown': preface})
    counts = {'summary': 1} if preface else {}
    for number, (start, title) in enumerate(starts):
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or 'section'
        counts[slug] = counts.get(slug, 0) + 1
        section_id = slug if counts[slug] == 1 else f'{slug}-{counts[slug]}'
        end = starts[number + 1][0] if number + 1 < len(starts) else len(lines)
        sections.append({'id': section_id, 'title': title, 'markdown': ''.join(lines[start:end]).strip()})
    return sections
