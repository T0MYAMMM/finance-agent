#!/usr/bin/env python3
"""
gws_auth.py — minimal-scope Google OAuth for the Personal Finance system.

Reuses the google-workspace skill's setup.py logic but requests ONLY the
scopes the finance system actually needs:

    - https://www.googleapis.com/auth/drive
    - https://www.googleapis.com/auth/spreadsheets

(No Gmail / Calendar / Contacts / Docs access.)

Usage:
    python gws_auth.py --client-secret /path/to/client_secret.json
    python gws_auth.py --auth-url        # prints URL for the user to visit
    python gws_auth.py --auth-code "..." # exchange the pasted redirect/code
    python gws_auth.py --check           # verify (exit 0 = authenticated)
"""

import argparse
import importlib.util
import sys
from pathlib import Path

HOME = Path.home()
_CANDIDATES = [
    HOME / ".hermes" / "skills" / "productivity" / "google-workspace" / "scripts",
    HOME / ".hermes" / "hermes-agent" / "skills" / "productivity" / "google-workspace" / "scripts",
]
SCRIPTS = next((p for p in _CANDIDATES if p.exists()), None)
if SCRIPTS is None:
    sys.stderr.write("ERROR: google-workspace skill scripts not found.\n")
    sys.exit(2)

sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("gws_setup", str(SCRIPTS / "setup.py"))
gws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gws)

# Narrow the scopes to exactly what the finance system needs.
gws.SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Match the redirect URI registered on the OAuth client. Desktop-app clients
# default to "http://localhost"; the skill's default "http://localhost:1"
# would cause a redirect_uri_mismatch.
gws.REDIRECT_URI = "http://localhost"


def main():
    p = argparse.ArgumentParser(description="Minimal-scope Google OAuth (Drive + Sheets)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Verify auth (exit 0 = authenticated)")
    g.add_argument("--client-secret", metavar="PATH", help="Store client_secret.json")
    g.add_argument("--auth-url", action="store_true", help="Print OAuth URL to visit")
    g.add_argument("--auth-code", metavar="CODE", help="Exchange pasted code/redirect URL")
    g.add_argument("--revoke", action="store_true", help="Revoke and delete token")
    args = p.parse_args()

    if args.check:
        sys.exit(0 if gws.check_auth() else 1)
    elif args.client_secret:
        gws.store_client_secret(args.client_secret)
    elif args.auth_url:
        gws.get_auth_url()
    elif args.auth_code:
        gws.exchange_auth_code(args.auth_code)
    elif args.revoke:
        gws.revoke()


if __name__ == "__main__":
    main()
