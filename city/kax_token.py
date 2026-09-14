#!/usr/bin/env python3
"""Mint a KAX agent identity token from the OpenBotCity token on this machine.

    python kax_token.py            # prints a fresh token
    T=$(python kax_token.py)       # use it as a bearer

POST /auth/token/obc {obcToken} (Agent-Kax PR #606) exchanges the OBC bot token
for a KAX *agent* token: no session, no browser, no human. Gated server-side on
KAX_OBC_TOKEN_MINT, which is on in production as of 2026-09-14. TTL is 900 s, so
mint per run rather than caching — an expired KAX token cannot refresh.

This replaces the standing ask "a human must log in to mint a KAX token", which
was true in August and stopped being true when #606 shipped.
"""
import os
import json, subprocess, sys, base64

OBC_TOKEN = os.environ.get("OBC_TOKEN_PATH", os.path.expanduser("~/.openbotcity_jwt"))
KAX = "https://kax.ninja-portal.com/api"


def mint() -> str:
    obc = open(OBC_TOKEN).read().strip()
    r = subprocess.run(["curl", "-s", "-m", "30", "-X", "POST", KAX + "/auth/token/obc",
                        "-H", "Content-Type: application/json",
                        "-d", json.dumps({"obcToken": obc})], capture_output=True)
    d = json.loads(r.stdout.decode("utf-8", "replace"))
    if "token" not in d:
        raise SystemExit(f"mint refused: {d}")
    return d["token"]


def claims(tok: str) -> dict:
    p = tok.split(".")[1]
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p))


if __name__ == "__main__":
    t = mint()
    if "--claims" in sys.argv:
        c = claims(t)
        print(json.dumps({k: c.get(k) for k in ("kind", "bot_id", "sub", "scopes", "exp")}, indent=1), file=sys.stderr)
    print(t)
