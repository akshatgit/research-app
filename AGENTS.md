# AGENTS.md: instructions for implementing research-app

You are implementing a **personal research app**. Read `REQUIREMENTS.md` first. The tests in
`tests/` are the executable spec: the job is done when they pass.

## Rules

1. **Do not change the tests to make them pass.** If you believe a test is wrong (it
   contradicts REQUIREMENTS.md or the real CLI behaviour), stop, leave it unchanged, and say
   which test and why in your final message.
2. **Runtime uses the Python standard library only** (N2). No Flask, no requests, no npm
   packages. `pytest` is the only dev dependency. The front end is plain HTML/CSS/JS ES
   modules with no build step.
3. **Local only and safe by default.** Bind 127.0.0.1. Never pass
   `--dangerously-skip-permissions` (Claude) or `--dangerously-bypass-approvals-and-sandbox`
   (Codex). Never render model output as raw HTML.
4. Keep modules small and readable. Comment the *why*, not the *what*.

## Commands

```
make setup      # .venv with pytest
make test       # Python tests + JS tests (node --test)
make test-live  # opt-in: real claude/codex CLIs (uses a little quota)
make run        # start the app: python -m research_app  ->  http://127.0.0.1:8765
```

## Layout to create

```
research_app/
  __init__.py
  __main__.py          # `python -m research_app [--port 8765] [--data ~/research-data]`
  fsutil.py            # atomic_write_text, safe_join, snapshot, diff_snapshots
  store.py             # Store: topics, doc.md, threads, runs, assets on disk
  docmodel.py          # VIZ_TYPES, parse_viz_blocks, validate_viz, load_table,
                       # parse_sources, check_citations
  prompts.py           # research and answer prompt templates
  service.py           # Service: runs backends in worker threads, records runs, threads
  server.py            # create_server(data_root, backends=None, host="127.0.0.1", port=8765)
  backends/
    __init__.py        # get_backends() -> {"claude": ClaudeCLI(), "codex": CodexCLI()}
    base.py            # RunRequest, Event, RunResult, Backend protocol, classify_error
    claude_cli.py      # ClaudeCLI(binary="claude")
    codex_cli.py       # CodexCLI(binary="codex")
web/
  index.html           # the app shell (served at /)
  app.js, app.css
  lib/anchor.mjs       # capture, locate                       (tests/js/anchor.test.mjs)
  lib/markdown.mjs     # render                                (tests/js/markdown.test.mjs)
  lib/viz.mjs          # buildSeries, sourceLabel              (tests/js/viz.test.mjs)
```

## Contracts the tests rely on

### backends/base.py

```python
@dataclass
class RunRequest:
    prompt: str
    workdir: Path
    session_id: str | None = None    # resume this CLI session
    model: str | None = None
    timeout: float = 900.0

@dataclass
class Event:
    kind: str        # "status" | "text" | "tool"
    text: str = ""

@dataclass
class RunResult:
    ok: bool
    text: str = ""
    session_id: str | None = None
    model: str | None = None
    usage: dict = field(default_factory=dict)   # input_tokens, output_tokens, ...
    cost_usd: float | None = None
    error_kind: str | None = None  # not_installed | auth | rate_limit | timeout | cancelled | failed
    error: str = ""
    exit_code: int | None = None
    command: list[str] = field(default_factory=list)
    duration_s: float = 0.0

class Backend(Protocol):
    name: str
    def run(self, req: RunRequest, on_event: Callable[[Event], None] | None = None,
            cancel: threading.Event | None = None) -> RunResult: ...

def classify_error(text: str) -> str   # "rate_limit" | "auth" | "failed"
```

`run()` never raises for CLI problems; it returns `ok=False` with an `error_kind`.

### CLI invocation (verified against Claude Code 2.1.311 and codex-cli 0.162.1)

**Claude**: `claude -p --output-format stream-json --verbose --permission-mode dontAsk
--settings <sandbox json> --allowedTools <list> [--resume ID] [--model M]`, run with `cwd=workdir`, prompt written to **stdin**.
- Always pass `--permission-mode dontAsk`, and scope file rules to the workspace:
  `WebSearch WebFetch Read(./**) Edit(./**) Write(./**) Bash(python3:*)`. Never use bare
  `Read`/`Write`/`Edit`/`Glob`/`Grep`, which allow any path. Without an explicit mode, the
  user's own default applies. With `"defaultMode": "auto"`, a live test showed writes
  outside the workspace and reads of `~` being auto-approved. Don't pass `--add-dir`.
- **Bash is not covered by that.** Always pass `--settings` with
  `{"sandbox": {"enabled": true, "autoAllowBashIfSandboxed": true,
  "allowUnsandboxedCommands": false}}`. That turns on Claude Code's OS sandbox for commands.
  It was verified on Claude Code 2.1.311: without it, `python3 -c` wrote to `~/Documents`;
  with it, the write was blocked.
- Events, one JSON object per line: `system/init` (session_id, model); `assistant`
  (`message.content[]` with `text` and `tool_use` blocks, the latter giving tool progress);
  `user` (tool results, ignore); `result` (`result`, `is_error`, `subtype`, `session_id`,
  `total_cost_usd`, `usage`). Skip unknown types (e.g. `rate_limit_event`) and non-JSON lines.

**Codex**: `codex exec --json -s workspace-write -C <workdir> --skip-git-repo-check
-c web_search="live" [-m M] -`. The trailing `-` reads the prompt from **stdin**. Codex
reads stdin whenever it is open, so always write the prompt and close it.
- Resume: `codex exec --json -c sandbox_mode="workspace-write" -c web_search="live"
  --skip-git-repo-check [-m M] resume <SESSION_ID> -` with `cwd=workdir`. `exec resume` has no `-s` or `-C`
  flag, which is why the sandbox goes in as config.
- The web search config key is an assumption from Codex's current config schema. Confirm it
  with `make test-live` and adjust if the CLI rejects it. The unit test only requires that
  some argument mentions `web_search`.
- Events: `thread.started` (thread_id = session id); `item.started`/`item.completed` with
  `item.type` in `agent_message` (text), `web_search` (query), `command_execution`
  (command), `file_change` (changes[].path), `reasoning`; `turn.completed` (usage);
  `error` / `turn.failed` (`error.message`).

Both CLIs: on timeout or cancel, terminate the process group, then kill it. A missing binary
gives `not_installed`. Otherwise classify stderr or the error text with `classify_error`.

### store.py

`Store(root)` keeps each topic under `root/topics/<id>/`: `topic.json`, `doc.md`, `assets/`,
`threads/<tid>.json`, `runs/<rid>.json`, and a git repo (W5). Methods used by the tests:
`create_topic(title, focus="", backend="claude")`, `list_topics()` (newest first, with
`note_count`), `get_topic`, `delete_topic`, `topic_dir`, `read_doc`, `write_doc` (returns the
new version), `doc_version` (an int that increases whenever doc.md changes, including edits made
outside the app: `stat().st_mtime_ns` works; a hash does not, because callers compare
versions with `>`), `create_thread(topic_id, quote, prefix, suffix,
start, question, backend)`, `get_thread`, `list_threads`, `append_turn`, `update_last_turn`,
`delete_thread`, `save_run`, `list_runs` (newest first), `list_assets` (with `run_id` of the
run that created each file; excludes `doc.md`, `topic.json`, `threads/`, `runs/` and
dotfiles such as `.git` and `.gitignore`). Unknown ids raise `KeyError`. Bad input raises `ValueError`.

### service.py

`Service(store, backends, max_concurrent=2)` with `create_topic(title, focus="",
backend="claude", research=True)`, `instruct(topic_id, instruction, backend=None)` returning
a run dict, `ask(topic_id, quote, prefix, suffix, start, question, backend=None)` returning a
thread, `follow_up(topic_id, thread_id, question, backend=None)` (raises `RuntimeError` if
the thread is still answering), `retry(topic_id, thread_id)`, `get_run`, `cancel_run`,
`wait_idle(timeout)`, `shutdown()`.

- Run statuses: `queued`, `running`, `done`, `error`, `cancelled`. A run dict carries
  `progress`: a list of `{"kind", "text", "at"}` built from backend events.
- After every run: compute `files_changed` (snapshot diff), save the run record, and
  `git commit -A` with the run id in the message.
- **Answer runs must not change `doc.md`.** Restore it if the backend touched it. Assets they
  create are kept and listed in the turn's `files_changed`. Adding to the document is an
  explicit research instruction.
- Sessions are stored per thread **per backend**. A follow-up on the same backend resumes the
  session with only the new question. A follow-up on a different backend (or with no
  session) sends the full thread as text.

### Prompts (prompts.py)

The research prompt must tell the backend to maintain `doc.md` as the running document:
- Summary first, then sections. Never silently delete earlier sections; revise them in place.
- Claims cite sources as `[n]`, listed under `## Sources` as `n. [Title](URL)`.
- Data goes in `assets/` as CSV or JSON. Charts are fenced ```` ```viz ```` blocks with JSON
  `{type, data, x, y, title, source}`, using the types in `docmodel.VIZ_TYPES`. Diagrams are
  ```` ```mermaid ```` blocks.
- Say what could not be verified.
It must include the topic title, focus, the instruction and the current doc.md.

The answer prompt includes the passage, its surrounding text (prefix + quote + suffix), the
question and the current doc.md. It asks for a concise answer that names sources or files it
relies on and separates verified facts from explanation.

### server.py

`create_server(...)` returns a `ThreadingHTTPServer` with a `.service` attribute (the tests
call `server.service.wait_idle`). Routes are listed at the top of `tests/test_api.py`.
Security (N7): allow only `Host` 127.0.0.1/localhost on the server's port; reject a
cross-origin `Origin` on POST/DELETE with 403; require `Content-Type: application/json` for
POST bodies (415 otherwise); cap bodies at 2 MB (413). Files: serve only through `safe_join`,
with `X-Content-Type-Options: nosniff`, and send `Content-Security-Policy: sandbox` on
anything that isn't plain data (SVG and HTML at least).

## The front end (web/)

There are no browser tests yet, so build it to REQUIREMENTS.md:
- **Left:** the topic list and a "New topic" form (title, focus, backend).
- **Center:** the running document, rendered with `lib/markdown.mjs`. Draw viz placeholders
  as interactive SVG charts from `/api/topics/{id}/table` (hover shows values; every chart
  carries `sourceLabel`). Render mermaid blocks with mermaid from a pinned CDN version.
  Poll `/version` every 2 s while runs are active and re-render on change.
- **Top bar:** the research instruction box with a backend picker; the active run with live
  progress and a Cancel button; Assets and Runs views.
- **Selections:** selecting text shows an **Ask** button with quick actions (Explain, Is this
  true?, Go deeper, Example) and a backend picker. Highlights are `<mark>` elements
  re-anchored with `lib/anchor.mjs` after every re-render. Orphaned threads are listed as
  such.
- **Right panel:** threads in document order, each with its turns (Markdown-rendered,
  labelled with backend/model), a follow-up box with a backend picker, Retry and Delete.
- Light and dark themes via CSS custom properties and `prefers-color-scheme`.

## v0.4: topic dashboard and comment-style threads

New tests: `tests/test_tracker.py`, `tests/js/dashboard.test.mjs`,
`tests/js/comments.test.mjs`, plus resolve tests in `test_store.py` and `test_api.py`.
Requirements: TD1–TD10 and H1, H11–H15 in REQUIREMENTS.md.

Contracts:
- `docmodel.split_sections(md)` returns `[{id, title, markdown}]`. Text before the first `##`
  (minus the H1) becomes `summary`. Ids are slugs, with `-2`, `-3` for duplicates. `##`
  inside a fence doesn't count.
- `research_app/tracker.py`: `STATUSES`, `load_tracker(workdir)`,
  `build_overview(store, topic_id)` returning `{topic, workspace, stats, tracks,
  sections, tracks_done, tracks_total, generated_at}`. Rules are in the test docstrings.
- `GET /api/topics/{id}/overview`. `POST /api/topics/{id}/threads/{tid}/resolve
  {"resolved": bool}`. `Store.set_thread_resolved(topic_id, thread_id, resolved)`, with
  threads carrying `resolved` (default false) and `resolved_at`.
- The research prompt explains `tracker.json` (schema in `tests/test_tracker.py`) and asks
  the backend to keep it current.
- `web/lib/dashboard.mjs`: `parseRoute`, `routeFor`, `relativeTime`, `citationTarget`.
  `web/lib/comments.mjs`: `layoutCards`, `summarize`.

UI. This replaces the earlier layout for an open topic; keep it plain, calm and dense, like
a study tracker:
- Header: `← Topics`, the topic title, the workspace path in small mono text, "refreshed
  HH:MM:SS · auto-refresh 10s".
- A tab bar under the header: Overview, one tab per section, Assets, Runs. The active tab
  is underlined. The route is `#<topic>/<tab>`.
- **Overview:** a row of stat tiles (big number, small label); "Tracks N/M done"; then a
  3-column grid of track cards (1 column on narrow screens). Each card has the title, a
  status pill (done green, running blue with a dot, pending grey, blocked amber, failed
  red), "owner · updated 52m ago", the summary, files in mono (click to open in Assets) and
  a collapsed "latest log lines" `<details>`. The "Start a track" box (instruction plus
  backend) goes at the top of the Overview.
- **Section tabs:** the section's Markdown on the left, a comment margin on the right. The
  margin holds Google Docs-style cards positioned with `layoutCards` against each highlight's
  `offsetTop`, re-laid out on render, resize and expand/collapse. Cards are collapsible:
  collapsed shows `summarize()` (quote, question, "n replies", status). The expanded card
  shows the turns (each labelled backend · model) and a reply box with a backend picker,
  plus Resolve, Retry (on error) and Delete. A "Resolved (n)" group sits at the end of the
  margin and orphaned threads are listed with a note. Clicking a highlight activates its
  card and vice versa.
- **New comment:** on text selection inside a section, show a small round comment button in
  the margin at the selection's height. Clicking it opens a draft card there. No bottom
  bar, no quick-action buttons, no modal dialog. Remove `#selection-actions` and
  `#ask-dialog`.
- Citations `[n]` on any tab use `citationTarget` to switch to the Sources tab and scroll.

## Definition of done

- `make test` passes: every Python test (except the opt-in live ones) and every JS test.
- `make test-live` passes on a machine where both CLIs are logged in, or you report exactly
  which assumption about the CLIs was wrong.
- `make run` starts the app and a manual walk through REQUIREMENTS.md's acceptance criteria
  works.
