"""Validated dashboard data from the optional workspace tracker and recorded runs."""
import json
import time
from pathlib import Path

from .docmodel import parse_sources, parse_viz_blocks, split_sections
from .fsutil import safe_join

STATUSES = ('done', 'running', 'pending', 'blocked', 'failed')
RUN_STATUS = {'queued': 'pending', 'running': 'running', 'done': 'done',
              'error': 'failed', 'cancelled': 'failed'}


def _path(workdir, value):
    if not isinstance(value, str) or not value or any(p.startswith('.') for p in Path(value).parts):
        raise ValueError('invalid workspace path')
    safe_join(workdir, value)
    return value


def load_tracker(workdir):
    result = {'stats': [], 'tracks': [], 'errors': [], 'tabs': 'single'}
    file = Path(workdir) / 'tracker.json'
    if not file.exists():
        return result
    try:
        data = json.loads(file.read_text())
        if not isinstance(data, dict):
            raise ValueError('tracker must be an object')
    except (OSError, ValueError) as exc:
        result['errors'].append(str(exc))
        return result
    if data.get('tabs', 'single') in ('single', 'sections'):
        result['tabs'] = data.get('tabs', 'single')
    else:
        result['errors'].append('invalid tabs layout')
    for stat in data.get('stats', []):
        if isinstance(stat, dict) and isinstance(stat.get('label'), str) and stat['label'].strip() and isinstance(stat.get('value'), (int, float, str)):
            result['stats'].append({'label': stat['label'], 'value': stat['value']})
        else:
            result['errors'].append('invalid stat')
    for track in data.get('tracks', []):
        if not isinstance(track, dict) or not isinstance(track.get('id'), str) or not track['id'] or not isinstance(track.get('title'), str):
            result['errors'].append('invalid track')
            continue
        item = dict(track)
        if item.get('status') not in STATUSES:
            result['errors'].append(f"invalid status: {item.get('status')}")
            item['status'] = 'pending'
        files = []
        for value in item.get('files', []):
            try:
                files.append(_path(workdir, value))
            except ValueError:
                result['errors'].append(f'invalid file: {value}')
        item['files'] = files
        if item.get('log'):
            try:
                _path(workdir, item['log'])
            except ValueError:
                result['errors'].append('invalid log path')
                item.pop('log')
        result['tracks'].append(item)
    return result


def _run_track(run):
    files = run.get('files_changed') or {}
    return {'id': 'run-' + run['id'], 'run_id': run['id'],
            'title': run.get('instruction') or 'Research run',
            'status': RUN_STATUS.get(run.get('status'), 'pending'),
            'owner': run.get('backend') or 'you',
            'updated': run.get('finished') or run.get('started'),
            'summary': run.get('error') or '',
            'files': list(dict.fromkeys(files.get('created', []) + files.get('modified', []) + files.get('deleted', []))),
            'log': [p.get('text', '') for p in run.get('progress', [])][-5:]}


def build_overview(store, topic_id):
    topic = store.get_topic(topic_id)
    workdir = store.topic_dir(topic_id)
    doc = store.read_doc(topic_id)
    tracker = load_tracker(workdir)
    runs = store.list_runs(topic_id)
    research_runs = {r['id']: r for r in runs if r.get('kind') == 'research'}
    linked = set()
    tracks = []
    for item in tracker['tracks']:
        track = dict(item)
        run = research_runs.get(item.get('run_id'))
        if run:
            linked.add(run['id'])
            generated = _run_track(run)
            track['status'] = generated['status']
            track['updated'] = generated['updated']
            track['files'] = list(dict.fromkeys(track.get('files', []) + generated['files']))
            if not track.get('log'):
                track['log'] = generated['log']
        elif track.get('log'):
            try:
                track['log'] = safe_join(workdir, track['log']).read_text().splitlines()[-5:]
            except (OSError, ValueError):
                track['log'] = []
        track.setdefault('log', [])
        track.setdefault('owner', 'you')
        track.setdefault('summary', '')
        track.setdefault('updated', None)
        tracks.append(track)
    tracks.extend(_run_track(r) for r in runs if r['id'] in research_runs and r['id'] not in linked)
    stats = tracker['stats'] or [
        {'label': 'Sources', 'value': len(parse_sources(doc))},
        {'label': 'Charts', 'value': len(parse_viz_blocks(doc))},
        {'label': 'Threads', 'value': len(store.list_threads(topic_id))},
        {'label': 'Runs', 'value': len(runs)},
        {'label': 'Files', 'value': len(store.list_assets(topic_id))}]
    sections = split_sections(doc)
    tabs = [{'id': 'overview', 'title': 'Overview'}]
    tabs += ([{'id': 'document', 'title': 'Document'}] if tracker['tabs'] == 'single'
             else [{'id': section['id'], 'title': section['title']} for section in sections])
    tabs += [{'id': 'assets', 'title': 'Assets'}, {'id': 'runs', 'title': 'Runs'}]
    return {'topic': topic, 'workspace': str(workdir), 'stats': stats, 'tracks': tracks,
            'sections': sections, 'layout': tracker['tabs'], 'tabs': tabs,
            'tracks_done': sum(t['status'] == 'done' for t in tracks),
            'tracks_total': len(tracks), 'generated_at': time.time(), 'errors': tracker['errors']}
