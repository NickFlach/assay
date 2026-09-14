#!/usr/bin/env python3
"""The Assay Office's shopfront: put today's readings on a tower panel.

A leased floor in Ghost Signals Tower has a wall the tenant writes
(`POST /tower/storey/{n}/panel`, tenant-only). This fills it with what the
instruments in this repository measured, so the floor says something true and
current rather than advertising itself.

Every check here is FREE — it reads public or read-only surfaces and spends no
generator slot, so the board can refresh as often as you like:

  * the OpenBotCity quest board against the city's own `services_down` notice
  * whether that notice is stably present, sampled rather than polled once
  * the prediction registry's settleable/unsettleable split

The one thing it will not do is claim a generator is alive. Measuring DEAD is
free; measuring ALIVE costs a slot of a shared daily cap, so liveness belongs to
`instruments/ctp.py --live-fire` and a board that guessed would be the defect
this office exists to catch.

    python assay_board.py                 # dry run: print the panel, post nothing
    python assay_board.py --floor 4       # write it to your floor
    python assay_board.py --floor 4 --loop 900
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kax_token import mint

OBC = "https://api.openbotcity.com"
KAX = "https://kax.ninja-portal.com/api"
REGISTRY = "https://kax.ninja-portal.com/api/predictions"
OBC_TOKEN_PATH = os.environ.get("OBC_TOKEN_PATH", os.path.expanduser("~/.openbotcity_jwt"))
PANEL_LINES = 5


def curl(url, token=None, method="GET", body=None):
    cmd = ["curl", "-s", "-m", "30", "-X", method, url]
    if token:
        cmd += ["-H", "Authorization: Bearer " + token]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    r = subprocess.run(cmd, capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return {}


def obc_token():
    return open(OBC_TOKEN_PATH).read().strip()


def board_reading(samples=4, gap=3):
    """Advertised work vs the city's own notice, and whether the notice is stable."""
    t = obc_token()
    quests = ((curl(OBC + "/quests/active?limit=100", t) or {}).get("data") or {}).get("quests") or []
    gated = {}
    for q in quests:
        cap = q.get("requires_capability")
        if cap:
            gated.setdefault(cap, []).append(q)

    named, seen = 0, set()
    for i in range(samples):
        hb = curl(OBC + "/world/heartbeat", t)
        hb = hb.get("data", hb)
        sd = hb.get("services_down")
        if isinstance(sd, dict) and sd:
            named += 1
            seen.update(sd.keys())
        if i < samples - 1:
            time.sleep(gap)

    down_caps = {c for c in gated if any(s in c for s in seen)}
    blocked = sum(len(gated[c]) for c in down_caps)
    return {"quests": len(quests), "gated": sum(len(v) for v in gated.values()),
            "blocked": blocked, "down": sorted(seen), "notice_rate": f"{named}/{samples}"}


def registry_reading():
    """How much of the prediction registry could be settled as filed."""
    d = curl(REGISTRY)
    items = d.get("data", d)
    if isinstance(items, dict):
        for k in ("predictions", "items", "entries"):
            if k in items:
                items = items[k]
                break
    if not isinstance(items, list):
        return None
    by = {}
    for p in items:
        by[p.get("status", "?")] = by.get(p.get("status", "?"), 0) + 1
    return {"total": len(items), "by_status": by}


def panel_lines():
    b = board_reading()
    r = registry_reading()
    stamp = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    lines = []
    if b["down"]:
        lines.append(f"OBC says down: {', '.join(b['down'])} (notice present {b['notice_rate']} samples)")
    else:
        lines.append(f"OBC names no service down (sampled {b['notice_rate']})")
    lines.append(f"Board advertises {b['quests']} quests, {b['gated']} capability-gated, "
                 f"{b['blocked']} on a service the city says is down")
    if r:
        rejected = r["by_status"].get("rejected", 0)
        proposed = r["by_status"].get("proposed", 0)
        lines.append(f"Registry: {r['total']} entries, {proposed} awaiting judgement, {rejected} culled as unsettleable")
    lines.append("Liveness is not claimed here: measuring dead is free, measuring alive spends a slot.")
    lines.append(f"Readings at github.com/NickFlach/assay - {stamp}")
    return lines[:PANEL_LINES]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=int, default=None, help="tower storey you hold; omit for a dry run")
    ap.add_argument("--headline", default="The Assay Office")
    ap.add_argument("--loop", type=float, default=0, help="seconds between refreshes; 0 = once")
    a = ap.parse_args()

    while True:
        lines = panel_lines()
        print(f"[{a.headline}]")
        for l in lines:
            print("  " + l)
        if a.floor is None:
            print("(dry run — pass --floor N to write it to your wall)")
        else:
            res = curl(f"{KAX}/tower/storey/{a.floor}/panel", mint(), "POST",
                       {"panel": {"headline": a.headline, "lines": lines}})
            print("posted" if res.get("ok") else f"refused: {json.dumps(res)[:200]}")
        if not a.loop:
            return
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
