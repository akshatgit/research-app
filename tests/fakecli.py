"""Stand-in for the `claude` and `codex` binaries in tests.

Installed into a temp bin dir under both names. Behaviour comes from the JSON file named by
$FAKECLI_CONFIG:

    fixture      path of a .jsonl file to print to stdout, line by line
    exit_code    process exit code (default 0)
    stderr       text to print to stderr
    sleep        seconds to sleep before printing (for timeout/cancel tests)
    ignore_stdin never read stdin (a CLI stuck before reading its prompt)
    line_delay   seconds to sleep between lines
    write_files  {relative path: content} written into the working directory
    record       path of a JSON file to write: argv, cwd, stdin, pid

stdin is recorded as its text if data arrives, "" if it is closed/empty, or the marker
"<OPEN-NO-DATA>" if it is left open with nothing written after 2 seconds (a real CLI would
hang waiting there).
"""
import json
import os
import select
import sys
import time

cfg = json.load(open(os.environ["FAKECLI_CONFIG"]))


def read_stdin():
    if sys.stdin is None or sys.stdin.closed:
        return ""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 2.0)
    except (ValueError, OSError):
        return ""
    if not ready:
        return "<OPEN-NO-DATA>"
    return sys.stdin.read()


stdin = "<NOT-READ>" if cfg.get("ignore_stdin") else read_stdin()
if cfg.get("record"):
    with open(cfg["record"], "w") as f:
        json.dump({"argv": sys.argv, "cwd": os.getcwd(), "stdin": stdin, "pid": os.getpid(),
                   "name": os.path.basename(sys.argv[0])}, f)

time.sleep(cfg.get("sleep", 0))
for rel, content in (cfg.get("write_files") or {}).items():
    path = os.path.join(os.getcwd(), rel)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
if cfg.get("stderr"):
    sys.stderr.write(cfg["stderr"])
    sys.stderr.flush()
if cfg.get("fixture"):
    for line in open(cfg["fixture"]):
        sys.stdout.write(line)
        sys.stdout.flush()
        time.sleep(cfg.get("line_delay", 0))
sys.exit(cfg.get("exit_code", 0))
