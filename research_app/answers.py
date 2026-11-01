"""Parse the small metadata header requested for comment answers."""
import re


def parse_answer(text):
    lines = (text or '').splitlines()
    intent = None
    tldr = None
    if lines:
        head = lines[0].strip().strip('*').strip()
        match = re.match(r'(?i)^INTENT\s*:\s*(.*)$', head)
        if match:
            value = match.group(1).strip().strip('*').lower()
            intent = value if value in ('explain', 'update', 'task') else None
            lines.pop(0)
    if lines:
        head = lines[0].strip()
        match = re.match(r'(?i)^\*{0,2}TL;?DR\*{0,2}\s*(?::|-)?\s*\*{0,2}\s*(.*)$', head)
        if match:
            tldr = match.group(1).strip().strip('*').strip()
            lines.pop(0)
    body = '\n'.join(lines).strip()
    if tldr is None:
        first = re.match(r'[\s\S]*?[.!?](?=\s|$)', body)
        tldr = (first.group(0) if first else body).strip()
        if len(tldr) > 200:
            tldr = tldr[:199].rsplit(' ', 1)[0].rstrip(' ,;:-') + '…'
    return {'intent': intent, 'tldr': tldr, 'body': body}
