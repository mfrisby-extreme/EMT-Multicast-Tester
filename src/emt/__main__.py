"""`python -m emt` -> GUI; `python -m emt send|recv|ifaces ...` -> CLI."""

from __future__ import annotations

import sys


def main() -> int:
    from emt.cli import COMMANDS

    if len(sys.argv) > 1 and (sys.argv[1] in COMMANDS or sys.argv[1] in ("-h", "--help", "--version")):
        from emt.cli import main as cli_main

        return cli_main()

    from emt.gui.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
