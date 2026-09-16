#!/usr/bin/env python3
"""Entry point for the Hermes ``doeedd-finance`` skill: ``python doeedd.py --help``."""

import sys

from doeedd_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
