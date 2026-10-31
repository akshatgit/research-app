from .claude_cli import ClaudeCLI
from .codex_cli import CodexCLI


def get_backends():
    return {'claude': ClaudeCLI(), 'codex': CodexCLI()}
