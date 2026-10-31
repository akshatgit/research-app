"""Instructions passed to the local CLI backends."""
from .docmodel import VIZ_TYPES


def research_prompt(topic, instruction, doc):
    return f'''Research topic: {topic['title']}
Focus: {topic.get('focus', '')}
Instruction: {instruction}

Maintain doc.md as the running document. Put a summary first, then sections. Never silently delete earlier sections; revise them in place. Cite factual claims as [1], [2], etc. List sources under ## Sources as 1. [Title](URL). Say what could not be verified. Put data in assets/ as CSV or JSON. For charts use a ```viz fenced JSON block with type, data, x, y, title, source. Types: {', '.join(VIZ_TYPES)}. Use ```mermaid blocks for diagrams. Keep all work inside this workspace.

Keep tracker.json current in the workspace. Its schema is {{"stats": [{{"label": "Verified references", "value": 66}}], "tracks": [{{"id": "lit-review", "title": "Literature review", "status": "done", "owner": "claude", "updated": 1793480000.0, "summary": "What changed", "files": ["assets/refs.csv"], "log": "logs/latest.log", "run_id": null}}]}}. Track statuses are done, running, pending, blocked, or failed. Link a track to its research run with run_id when known. Keep paths relative to this workspace.

Current doc.md:
{doc}'''


def answer_prompt(thread, question, doc, history=''):
    passage = thread['prefix'] + thread['quote'] + thread['suffix']
    return f'''Answer the question about this passage: {thread['quote']}
Surrounding text: {passage}
Question: {question}
Earlier turns:
{history}

Current doc.md:
{doc}

Give a concise answer. Name sources or files you rely on, and separate verified facts from explanation. You may create assets, but do not edit doc.md; adding an answer to the document requires an explicit research instruction.'''
