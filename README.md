# research-app

A personal research app. Start a topic and an AI backend researches it on the web. Each
topic is a workspace where the backend keeps a **running document** (`doc.md`) with
citations, plus data, scripts and charts in `assets/`. You read the document in the browser,
highlight any passage, and ask about it. Each question becomes a thread anchored to that
passage.

The backends are the **Claude Code** (`claude`) and **Codex** (`codex`) CLIs, run under
your own logins. No API keys are needed. Any instruction, question or follow-up can use
either backend.

It runs locally on `127.0.0.1` only and is meant for one person.

## Run it

Python 3.11+ is required, standard library only. You also need `claude` and/or `codex`
installed and logged in.

```
make run                      # http://127.0.0.1:8765, data in ~/research-data
```

### As a service on a Linux machine

```
git clone https://github.com/akshatgit/research-app.git ~/Documents/research-app
~/Documents/research-app/deploy/install.sh
```

This installs a systemd **user** service (`deploy/research-app.service`) that runs as you,
starts at boot and restarts on failure. The service listens on 127.0.0.1 only, so reach it
through an SSH tunnel:

```
ssh -N -L 8765:127.0.0.1:8765 <host>      # then open http://127.0.0.1:8765
```

Manage it with `systemctl --user status|restart|stop research-app` and read its logs with
`journalctl --user -u research-app -f`. To update: `git pull && deploy/install.sh`.

## How it works

```
browser ──HTTP──> research_app.server ──> service (worker threads, max 2 runs)
                                            ├── claude -p --output-format stream-json ...
                                            └── codex exec --json ...
                                                 (cwd = the topic's workspace)
```

Each topic folder holds `doc.md`, `assets/`, `threads/*.json`, `runs/*.json` and a git
repo with one commit per backend run. You can edit `doc.md` in your editor and the page
picks up the change.

## Security model

Content from the web is untrusted and can try to steer the model, so the backends are
confined:

- **Claude Code** runs with `--permission-mode dontAsk` and an explicit allow-list: web
  search/fetch, `Read`/`Edit`/`Write` scoped to the workspace (`./**`), and `python3`
  inside Claude Code's OS sandbox (macOS Seatbelt or Linux bubblewrap). Without
  `dontAsk`, your personal default mode would apply. A live test showed `"auto"` mode
  approving writes outside the workspace and reads of your home directory.
- **Codex** runs in its `workspace-write` sandbox, on resumed runs too.
- `tests/test_live.py` checks against the real CLIs that writes outside the workspace are
  blocked, through file tools and through `python3`.
- The web server binds 127.0.0.1, rejects foreign `Host` and `Origin` headers and non-JSON
  POSTs (DNS rebinding and CSRF), serves workspace files only through a path check, and
  sandboxes SVG/HTML responses. Model output is rendered as escaped Markdown, never as raw
  HTML, and remote images are not loaded.

**Known limit:** both CLIs' sandboxes block writes outside the workspace, but **commands
they run can still read most files your user can read**. Treat a topic like a program you
run as yourself. For strong isolation, run the service on a separate machine or VM that
holds nothing sensitive.

## Development

The tests are the spec (see `REQUIREMENTS.md` and `AGENTS.md`).

```
make setup        # .venv with pytest
make test         # Python + JS tests (fake CLIs, no quota used)
make test-live    # real claude/codex CLIs; uses a little quota
```
