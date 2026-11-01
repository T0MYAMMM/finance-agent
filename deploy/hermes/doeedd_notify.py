#!/usr/bin/env python3
"""Hermes cron script for the "doeedd daily check" job (no agent, delivers to Telegram).

Prints at most one message, or nothing when nothing is due; Hermes delivers stdout verbatim
and skips empty output. The rules (quiet hours, once a day, outage alert after three failed
checks) live in ``doeedd.py notify``; this wrapper only finds the CLI and runs it.
Standard library only: it runs before any project environment is set up.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 120


def cli_command() -> list[str]:
    """The interpreter and ``doeedd.py`` path, from the image's environment."""
    agent_dir = Path(os.environ.get("FINANCE_AGENT_DIR") or Path.home() / "finance-system")
    python = os.environ.get("HERMES_PYTHON") or sys.executable
    return [python, str(agent_dir / "doeedd.py"), "notify", "--empty-when-silent"]


def main() -> int:
    """Run the check and pass its message through."""
    result = subprocess.run(
        cli_command(), capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
    )
    if result.returncode != 0:
        # The CLI never prints the token; keep only the tail for the cron log.
        print(result.stderr.strip()[-500:], file=sys.stderr)
        return result.returncode
    sys.stdout.write(result.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
