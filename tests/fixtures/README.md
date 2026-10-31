# CLI output fixtures

- `claude_ok.jsonl`: a real `claude -p ... --output-format stream-json --verbose` run
  (Claude Code 2.1.311), trimmed to the fields the app reads.
- `codex_ok.jsonl`: a real `codex exec --json` run (codex-cli 0.162.1), unmodified.
- `claude_tools.jsonl`, `claude_error.jsonl`, `codex_tools.jsonl`, `codex_failed.jsonl`:
  synthetic, built in the same event shapes as the real runs to cover tool use and errors.
  `tests/test_live.py` (opt-in) checks the real CLIs still match the shapes the parsers expect.
