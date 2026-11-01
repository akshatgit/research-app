"""Instructions passed to the local CLI backends."""
from .docmodel import VIZ_TYPES


def research_prompt(topic, instruction, doc):
    return f'''Research topic: {topic['title']}
Focus: {topic.get('focus', '')}
Maintain doc.md as the running document. Put a summary first, then sections. Never silently delete earlier sections; revise them in place. Cite factual claims as [1], [2], etc. List sources under ## Sources as 1. [Title](URL). Say what could not be verified. Put data in assets/ as CSV or JSON. For charts use a ```viz fenced JSON block with type, data, x, y, title, source. Types: {', '.join(VIZ_TYPES)}. Use ```mermaid blocks for diagrams. Keep all work inside this workspace.

Keep tracker.json current in the workspace. Its schema is {{"tabs": "single", "stats": [{{"label": "Verified references", "value": 66}}], "tracks": [{{"id": "lit-review", "title": "Literature review", "status": "done", "owner": "claude", "updated": 1793480000.0, "summary": "What changed", "files": ["assets/refs.csv"], "log": "logs/latest.log", "run_id": null}}]}}. Track statuses are done, running, pending, blocked, or failed. Link a track to its research run with run_id when known. Keep paths relative to this workspace. Use "tabs": "sections" only when the parts are separate workstreams; otherwise use "single".

## Current doc.md
{doc}

## Instruction
{instruction}'''


def answer_prompt(thread, question, doc, history=''):
    passage = thread['prefix'] + thread['quote'] + thread['suffix']
    return f'''Give a concise answer. Name sources or files you rely on, and separate verified facts from explanation. You may create assets, but do not edit doc.md; adding an answer to the document requires an explicit research instruction.

## Current doc.md
{doc}

Passage: {thread['quote']}
Surrounding text: {passage}
Earlier turns:
{history}
Question: {question}'''


def update_prompt(thread, question, doc, history=''):
    return f'''You may edit doc.md to answer this update request. Keep other sections and citations intact. Explain what changed, or say if no change was needed.

## Current doc.md
{doc}

Passage: {thread['quote']}
Surrounding text: {thread['prefix']}{thread['quote']}{thread['suffix']}
Earlier turns:
{history}
Question: {question}'''


def task_prompt(thread, question, doc, history=''):
    return f'''Complete this task in the topic workspace. You may edit doc.md, assets, and tracker.json. Keep tracker.json current and report the work and sources used.

## Current doc.md
{doc}

Passage: {thread['quote']}
Surrounding text: {thread['prefix']}{thread['quote']}{thread['suffix']}
Earlier turns:
{history}
Task: {question}'''


def resumed_prompt(intent, thread, question):
    rules = {
        'explain': 'Give a concise answer. Do not edit doc.md.',
        'update': 'You may edit doc.md. Keep other sections and citations intact.',
        'task': 'Complete this task in the workspace; you may edit doc.md and tracker.json.',
    }
    return f"{rules[intent]}\nPassage: {thread['quote']}\nQuestion: {question}"
