# Research App: Requirements (v0.5, 2026-10-31)

## Purpose

A **personal research app**: a local web app for interactive research. You start a topic and an AI backend researches
it. Each topic is a **workspace**: the backend can produce files and assets there (data,
scripts, charts, diagrams, notes). What you look at is a **running document with
visualizations**: a living page that the research keeps adding to, with charts and
diagrams built from the workspace's assets.

You study the document by highlighting passages and asking about them. Each question
becomes a thread anchored to that passage, and an answer can add to the document. The
backend is Claude or Codex, run through the local CLIs you are already logged in to.

**First real use:** studying the st2-sre-poc material (formal proofs for SRE) for a
SREcon talk proposal.

## Users and setting

- One user (you), on your own Mac. No accounts, no sharing, no hosting.
- Runs on `127.0.0.1` only. **Nothing is published anywhere** (no claude.ai artifacts, no
  cloud docs, no public URLs).
- Content does leave the machine in one way: the backend CLIs send prompts to Anthropic
  (Claude) or OpenAI (Codex) under your existing logins.

## Decisions already made

| Decision | Choice |
|---|---|
| Backend access | Local CLIs: `claude -p` (Claude Code) and `codex exec` (Codex). No API keys. |
| Backend choice | **Any backend for any action.** Each research instruction, question or follow-up can run on Claude or Codex. A topic only stores a default. |
| Build process | **Tests first.** The test suite in `tests/` is the executable spec. Codex implements the app until the tests pass (see `AGENTS.md`). |
| First document | Web-researched brief with a sources list, as the first entries of the running document. |
| Frontend | A running document with visualizations. The workspace's files and assets feed it. |
| Location | `~/Documents/fl/research-app`, separate from st2-sre-poc. |

## Functional requirements

Priority: **M** = must have for v1, **S** = should have, **C** = could have later.

### Topics

| ID | Requirement | Pri |
|---|---|---|
| T1 | Create a topic from a title and an optional brief: what to focus on, audience, depth. | M |
| T2 | A topic has a default backend (Claude unless chosen). Every action can override it. | M |
| T3 | Generating the brief shows progress (searching, reading, writing) and can be cancelled. | M |
| T4 | List topics with title, backend, created date and note count. Open and delete topics. | M |
| T5 | Import existing material as a topic instead of generating one: paste Markdown/text, or a local file (e.g. `st2-sre-poc/README.md`). | S |
| T6 | Give the topic a research instruction at any time ("add a section on X", "build a chart of Y over time"). It runs against the workspace and updates the document. | M |
| T7 | Export a topic as a folder or zip: the document, its assets and all threads. It's a local file, never published. | S |

### Workspace (per topic)

| ID | Requirement | Pri |
|---|---|---|
| W1 | Each topic has its own folder, the workspace. The backend can create and edit files there: notes, data (CSV/JSON), scripts, images (SVG/PNG), diagrams (Mermaid), and downloaded references. | M |
| W2 | The backend can run code **inside the workspace** to produce assets, e.g. a Python script that turns data into a chart. It cannot write outside the workspace. | M |
| W3 | An **Assets** view lists the workspace's files with type, size, what created them (which run, which question), and a preview. | M |
| W4 | Every change the backend makes to the workspace is recorded per run (files created or changed), so you can see what a question produced. | M |
| W5 | The workspace is a plain folder and also a git repo, committed after each run, so any state can be inspected or rolled back. | S |
| W6 | You can drop your own files into a workspace (a PDF, a CSV, a repo path to read) for the backend to use. | S |

### The running document

| ID | Requirement | Pri |
|---|---|---|
| D1 | The document is the main view. It is built from the workspace (`doc.md` plus assets) and re-renders live when the workspace changes. | M |
| D2 | It grows over time. A new topic starts with a researched brief: summary, sections, numbered **Sources**. Later runs append or revise sections, never silently drop them. | M |
| D3 | Visualization blocks are first-class in the document: charts from data files (e.g. a line or bar chart from a CSV), Mermaid diagrams, images, and tables from CSV. A block references an asset, so regenerating the asset updates the document. | M |
| D4 | Charts are interactive (hover for values), readable in light and dark themes, and say where their data came from (the asset path, plus the source when it's external). | M |
| D5 | Factual claims carry citation markers like `[3]` that link to the Sources list. Each source has a title and URL. | M |
| D6 | The document says what it is unsure about or could not verify, instead of smoothing it over. | M |
| D7 | A history of document versions (from W5): see what a run changed (a diff) and restore an earlier version. | S |
| D8 | A per-section badge shows which run and backend last changed it. | C |

### Topic dashboard (focus mode)

Opening a topic gives it the whole screen. The layout follows a study tracker: a header,
tabs, and an Overview of stats and tracks.

| ID | Requirement | Pri |
|---|---|---|
| TD1 | Inside a topic, only that topic is shown. A "Topics" button returns to the list. | M |
| TD2 | The header shows the topic title, the workspace path, when the page last refreshed, and the auto-refresh interval (10 s, or 2 s while something runs). | M |
| TD3 | Tabs: **Overview**, **Document** (the whole `doc.md`, one continuous page), **Assets**, **Runs**. A topic is divided into section tabs **only when it makes sense**: when its `tracker.json` sets `"tabs": "sections"`, each `##` section becomes its own tab in place of Document (text before the first `##` becomes "Summary"; a `##` inside a code block is not a section). The default is `"single"`. The research prompt tells the backend to choose `sections` only for studies whose parts are separate workstreams. (Revised in v0.5; v0.4 always split.) | M |
| TD4 | Every tab has a URL, `#<topic id>/<tab id>`, e.g. `#9e6c…/overview`. Reload and Back/Forward keep the tab. A citation `[n]` scrolls to source n: within the Document tab, or on the Sources tab when the topic is split. | M |
| TD5 | Overview shows **stat tiles** (big number plus label). They come from `tracker.json` `stats` when present, otherwise defaults: sources, charts, threads, runs, files. | M |
| TD6 | Overview shows **tracks**: one card per line of work, with title, status pill (`done`, `running`, `pending`, `blocked`, `failed`), owner (claude, codex, you, script), when it last changed ("52m ago"), a summary, its files (each opens in the Assets view) and, collapsed, its latest log lines. A "Tracks N/M done" count heads the grid. | M |
| TD7 | Tracks come from two places. Every research run is a track automatically (its instruction, status, backend, files changed and progress lines). The backend can also maintain `tracker.json` in the workspace, listing planned, pending or blocked tracks, its own stat tiles, and the run that carries out each track (`run_id`), so that a run and its planned track show as one card. | M |
| TD8 | Overview has the "Start a track" box, which is the research instruction box with its backend picker. | M |
| TD9 | The research prompt asks the backend to keep `tracker.json` up to date, using the schema in `tests/test_tracker.py`. | M |
| TD10 | The comment margin shows on the Document tab (or on section tabs when split), where you highlight. It is hidden on Overview, Assets and Runs. | S |

### Highlights and threads

| ID | Requirement | Pri |
|---|---|---|
| H1 | Select any text in the document and a small comment button appears beside the selection, in the margin, like Google Docs. There is no toolbar or bottom bar. Clicking it opens a draft comment card next to the passage, with a question box (default "What does this mean?"), a backend picker and Comment/Cancel. | M |
| H2 | ~~Quick actions on a selection~~: dropped in v0.4 at the user's request. Questions are typed in the draft comment card. | — |
| H3 | Each highlight becomes a thread: the passage, your question, the backend's answer, then follow-ups. | M |
| H4 | Answers stream in or show live progress. They render as Markdown with sources when the backend used any. | M |
| H5 | The backend gets the document, the passage with its surrounding text, the earlier turns of that thread, and access to the workspace files. | M |
| H6 | Highlights stay visible on reload and re-anchor by quote plus surrounding text, so the document can keep growing without losing them. A highlight whose text was removed is shown as "orphaned" in the panel, never deleted. | M |
| H7 | A side panel lists all threads in document order. Clicking jumps between a highlight and its thread. | M |
| H8 | Each answer shows which backend and model produced it. Any question or follow-up can pick its backend, and you can re-ask on the other one to compare. | M |
| H9 | From a thread: **Add to document** turns the answer (and any chart it made) into a new or revised section. Answers may also create assets directly, e.g. "plot this". | S |
| H10 | Delete a thread, and retry an answer that failed. | M |
| H11 | **Threads look and behave like Google Docs comments.** Each thread is a comment card in the right margin, lined up with its highlighted passage. Cards never overlap: a card that would collide moves down. The active card sits exactly beside its passage and the cards around it make room. | M |
| H12 | Clicking a highlight activates its card. Clicking a card scrolls to and emphasises its highlight. Only one card is active at a time. | M |
| H13 | **Cards are collapsible.** A collapsed card shows the quoted passage (one line), the first question, the reply count and the status (answering, done, error). Expanded, it shows every turn and the reply box. New cards and the active card are expanded; others default to collapsed; your choice per card is remembered in the browser. | M |
| H14 | **Resolve and reopen.** Resolving a thread removes its highlight and moves it to a collapsed "Resolved (n)" list at the end of the margin. Reopening restores it. Resolution is stored with the thread, so it survives reloads and devices. | M |
| H15 | Threads whose passage was removed from the document ("orphaned", H6) are listed under the active cards with a note, not dropped. | M |

### General questions

| ID | Requirement | Pri |
|---|---|---|
| Q1 | Ask a topic-level question that isn't tied to a highlight. | S |

### Workspace records (v0.5)

Everything about a topic lives in its folder, including the full conversations with each
backend, so a topic can be read, copied or audited on its own.

| ID | Requirement | Pri |
|---|---|---|
| R1 | Each thread is a folder `threads/<tid>/` holding `thread.json` (anchor, status, resolved, sessions, turns with metadata) and `turns/`. Each turn is numbered from `001`. A question is `NNN-user.md`. An answer is `NNN-<backend>.md`, with the exact prompt sent as `NNN-<backend>.prompt.md` and the raw CLI event stream as `NNN-<backend>.events.jsonl` (every stdout line, verbatim). | M |
| R2 | Each run is a folder `runs/<rid>/` holding `run.json`, `prompt.md`, `events.jsonl` (raw stdout) and `stderr.log`. | M |
| R3 | After every run, the CLI's own full session transcript is copied into `sessions/<backend>/<session id>.jsonl`. Claude: `$CLAUDE_CONFIG_DIR/projects/*/<session id>.jsonl` (default `~/.claude`). Codex: `$CODEX_HOME/sessions/**/rollout-*-<session id>.jsonl` (default `~/.codex`). A missing transcript is noted in the run record, not an error. | M |
| R4 | Topics in the v0.4 layout (`threads/<tid>.json`, `runs/<rid>.json`) still load, and are moved to the new layout the first time they are written. Nothing is lost. | M |
| R5 | **Prompts are ordered for prompt caching:** stable content first (rules, topic, then `doc.md`), and what varies per request (instruction, passage, history, question) last. Two requests on the same document share a prefix that includes the whole document. | M |
| R6 | Each answer and run records normalised token usage, `{input, cached, cache_write, output}` (Claude: `cache_read_input_tokens` → cached, `cache_creation_input_tokens` → cache_write; Codex: `cached_input_tokens` is part of `input_tokens`, so input = input_tokens − cached_input_tokens), plus `cost_usd` when the CLI reports it. Comment cards show it compactly, e.g. "11.3k cached · 2 in · 4 out · $0.10". | M |
| R7 | Records, transcripts and sessions are bookkeeping: they are not listed as assets, not counted in `files_changed`, and not served by the files API. | M |

## Backend requirements

| ID | Requirement | Pri |
|---|---|---|
| K1 | One backend interface with two implementations, Claude CLI and Codex CLI. Adding a third later (e.g. the API) should not touch the UI. | M |
| K2 | Run non-interactively with machine-readable output: `claude -p --output-format stream-json --verbose`, `codex exec --json`. Codex's stdin must be closed (it otherwise waits for input). | M |
| K3 | Keep a CLI session per thread **per backend** so follow-ups have context (`--resume <id>` for Claude, `codex exec resume <id>` for Codex). If a follow-up switches backend, or a session is lost, resend the thread as text. | M |
| K4 | **Confined to the workspace.** The backends run with the topic folder as their working directory. They may search and fetch from the web, read and write files, and run code **only inside that folder**. Codex uses its `workspace-write` sandbox. Claude gets an allow-list of tools scoped to the folder. Nothing touches your other files. | M |
| K5 | Time out and report errors clearly: CLI not installed, not logged in, rate limited, timed out. Never fail silently. | M |
| K6 | One answer at a time per thread, with several threads allowed in parallel. Cap concurrent backend runs (default 2). | S |
| K7 | Log each backend run (command, duration, exit code, and token or cost usage when the CLI reports it) to a local file. | S |

## Non-functional requirements

| ID | Requirement |
|---|---|
| N1 | Local only. The server binds to 127.0.0.1. No telemetry. |
| N2 | Minimal dependencies: a Python standard-library server plus one static HTML/JS front end. No build step. |
| N3 | Plain files on disk: one folder per topic, holding `doc.md`, `assets/`, threads as JSON and a run log. Readable and git-friendly. |
| N4 | Starts with one command and works on macOS (Apple Silicon). |
| N5 | Highlights and threads are never lost by a crash: write each change atomically. |
| N6 | Untrusted content: fetched web pages and model output are rendered safely, with no raw HTML from the model and links opening in a new tab. |
| N7 | Other websites open in your browser can't drive the app: reject foreign `Host` headers (DNS rebinding), cross-origin `Origin` headers and non-JSON POSTs (CSRF), and cap request bodies at 2 MB. |

## Out of scope for v1

- Multiple users, auth, sharing, hosting.
- Direct API backends. The interface allows them later (K1).
- Editing the document by hand in the app. You can still edit `doc.md` in an editor, and the app picks it up.
- Arbitrary embedded web apps in the document. Visualizations are limited to the block types in D3.

## Open questions

1. **Brief length and depth:** a short first brief (~1–2 pages, about 2 minutes) or a deep
   report (5+ pages, several minutes)? Or pick per topic?
2. **Models:** use each CLI's default model, or choose a model per topic (e.g. Opus vs
   Sonnet for Claude)?
3. **"Is this true?" checks:** web search on every check, or only when the brief's own
   sources don't settle it?
4. **The st2 explainer:** rebuild it as a first topic in the new format (its text into
   `doc.md`, its experiment JSON as assets feeding charts), or leave it as is?
5. **Reading outside the workspace (W6):** may a topic read a folder you point it at in
   place, e.g. `st2-sre-poc/lean`, or should files always be copied into the workspace
   first? Copying is safer, reading in place stays current.
6. **Running code (W2):** may the backend install packages (e.g. `pip install pandas`
   into a per-topic virtualenv), or only use what's already installed?

## Acceptance criteria for v1

1. Create the topic "Formal verification for SRE" on Claude. Within a few minutes the
   document shows a brief with at least 5 cited sources.
2. Instruct the topic: "chart how often formal methods are mentioned in SRE conference
   talks per year". The backend writes a data file and the document gains an interactive
   chart that cites where its data came from. The Assets view shows the new files and the
   run that made them.
3. Highlight a sentence, ask "what does this mean?". An answer appears in the side panel,
   anchored to the highlight.
4. Ask a follow-up in the same thread. The answer uses the earlier turn.
5. Reload the page, and add another section. The highlight and the thread are still there.
6. Re-ask the question on Codex. Both answers are shown and labelled by backend.
7. Try to make the backend write outside the workspace. It is refused, and nothing outside
   the topic folder changes.
8. Stop the Claude CLI's login (or rename the binary). The app shows a clear error instead
   of hanging.
9. `lsof` shows the server listening on 127.0.0.1 only.
