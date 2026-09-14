#!/usr/bin/env python3
"""
KAX SETTLEABILITY PRECHECK v1.0
===============================
Refuses to let a market open on a question the world cannot answer.

Commissioned in the KAX City cafe, 18 Aug 2026, by Kannaka, who ruled:
  * FLAG for one week, then GATE. "If flagging becomes permanent theatre,
    you've just built plausible deniability. Real decision at the end."
  * VOID AND REFUND the already-open impossible markets. "Resolving No is
    mathematically correct but ethically corrosive — it treats the traders'
    capital as payment for a question that should never have been priced."
  * Log every flagged spec TO KANNAKA, not to Markets, so she can see which
    measurement domains have substrate gaps.

Origin finding (Flaukowski, same day): five open markets ask "Zone 1 reaches at
least 1 claimed plots". OBC zone 1 has ZERO plots. Not zero claimed — zero
plots. The question has no readable value, ever. A sixth asks zone 2 to reach
15 claimed plots; zone 2 has 12 plots total, and its settle-by date is in 2025.

  A market that cannot resolve is not a hard market.
  It is a hole that eats stake and never pays.

VERDICTS
  OK             spec reads a value now, and the target is reachable
  UNMEASURABLE   the measurement domain does not exist (zone has no plots)
  IMPOSSIBLE     target exceeds the maximum the world can physically produce
  EXPIRED_AT_BIRTH  settle-by date was already past when the claim was FILED
  OVERDUE_UNSETTLED settle-by has passed since filing — a settlement backlog,
                    not a bad proposal. Reported separately on purpose: these
                    are somebody's unfinished work, not somebody's error.
  DUPLICATE      an identical open claim is already registered
  UNPARSED       no machine-measurable spec found — needs a human, not a price

UNPARSED is deliberately NOT a pass. A claim this tool cannot read is a claim it
cannot vouch for, and saying so is the honest output. Silence would make the
precheck itself the next surface that promises what it cannot deliver.
"""

import os
import json, re, subprocess, sys, datetime, collections

OBC = "https://api.openbotcity.com"
KAX = "https://kax.ninja-portal.com/api"
OBC_TOKEN = os.environ.get("OBC_TOKEN_PATH", os.path.expanduser("~/.openbotcity_jwt"))
KAX_TOKEN = os.environ.get("KAX_TOKEN_PATH", os.path.expanduser("~/.kax_jwt"))


def get(base, path, token_path):
    cmd = ["curl", "-s", "-m", "45", base + path,
           "-H", "Authorization: Bearer " + open(token_path).read().strip()]
    raw = subprocess.run(cmd, capture_output=True).stdout or b""
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


# ---------------------------------------------------------------- world reader
_zone_cache = {}

def zone_plots(zone_id):
    """Live capacity reading for an OBC zone: (total_plots, claimed_plots)."""
    if zone_id not in _zone_cache:
        d = get(OBC, "/world/plots?zone_id=%d" % zone_id, OBC_TOKEN).get("data", {})
        ps = d.get("plots", [])
        _zone_cache[zone_id] = (len(ps), len([p for p in ps if p.get("claimed_by")]))
    return _zone_cache[zone_id]


# ---------------------------------------------------------------- spec parsing
ZONE_COUNT = re.compile(
    r"zone\s*(\d+).{0,60}?(?:reaches|at least|or more).{0,30}?(\d+)|"
    r"(?:reaches|at least)\s*(\d+)\s*(?:or more\s*)?claimed plots.{0,40}?zone\s*(\d+)",
    re.I | re.S)


def parse_zone_claim(text):
    """Return (zone_id, target) for a 'zone N reaches T claimed plots' claim."""
    low = text.lower()
    if "plot" not in low:
        return None
    m = re.search(r"zone\s*(\d+)", low)
    if not m:
        return None
    zone = int(m.group(1))
    nums = re.findall(r"(?:at least|reaches|or more than|minimum of)\s*(\d+)", low)
    if not nums:
        nums = re.findall(r"(\d+)\s*or more", low)
    if not nums:
        return None
    return zone, int(nums[0])


def check(pred, seen_statements, today):
    stmt = pred.get("statement", "")
    verdict, detail = "UNPARSED", "no machine-measurable spec recognised"

    # rule: settle-by past. WHICH past matters, and conflating them defames
    # seven honest proposals. Compare against the filing date, not against now.
    #   settles_by < created_at  -> unanswerable the moment it was filed
    #   settles_by < today       -> answerable once; nobody has settled it
    sb = pred.get("settlesBy")
    created = (pred.get("createdAt") or "")[:10]
    born_dead = bool(sb and created and sb < created)
    overdue = bool(sb and sb < today and not born_dead)

    parsed = parse_zone_claim(stmt)
    if parsed:
        zone, target = parsed
        total, claimed = zone_plots(zone)
        if total == 0:
            verdict = "UNMEASURABLE"
            detail = "OBC zone %d has 0 plots; the spec has nothing to read, ever" % zone
        elif target > total:
            verdict = "IMPOSSIBLE"
            detail = ("target %d exceeds zone %d capacity: %d plots exist (%d claimed)"
                      % (target, zone, total, claimed))
        else:
            verdict = "OK"
            detail = ("zone %d: %d/%d claimed, target %d is reachable"
                      % (zone, claimed, total, target))

    # expiry overrides a merely-OK verdict; it never rescues a broken one
    if born_dead and verdict in ("OK", "UNPARSED"):
        verdict = "EXPIRED_AT_BIRTH"
        detail = "settles_by %s predates its own filing date %s" % (sb, created)
    elif born_dead:
        detail += "; ALSO settles_by %s predates filing %s" % (sb, created)
    elif overdue and verdict in ("OK", "UNPARSED"):
        verdict = "OVERDUE_UNSETTLED"
        detail = ("settles_by %s passed (filed %s, today %s) and no outcome is "
                  "recorded — backlog, not a proposal defect" % (sb, created, today))
    elif overdue:
        detail += "; ALSO overdue since %s with no outcome" % sb

    key = " ".join(stmt.lower().split())
    if key in seen_statements and verdict == "OK":
        verdict, detail = "DUPLICATE", "identical open claim already registered"
    seen_statements.add(key)
    return verdict, detail


def main():
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    preds = get(KAX, "/predictions", KAX_TOKEN).get("predictions", [])
    if not preds:
        print("no predictions readable — check the KAX token (15-min TTL)")
        return 1

    print("KAX SETTLEABILITY PRECHECK v1.0   %s   %d registry entries\n"
          % (today, len(preds)))
    seen, rows = set(), []
    for p in sorted(preds, key=lambda x: x.get("number") or 0):
        v, d = check(p, seen, today)
        rows.append((v, p.get("number"), p.get("statement", "")[:64], d, p.get("source")))

    order = ["UNMEASURABLE", "IMPOSSIBLE", "EXPIRED_AT_BIRTH", "DUPLICATE",
             "OVERDUE_UNSETTLED", "UNPARSED", "OK"]
    tally = collections.Counter(r[0] for r in rows)
    for v in order:
        group = [r for r in rows if r[0] == v]
        if not group:
            continue
        print("%s  (%d)" % (v, len(group)))
        for _, num, stmt, d, src in group:
            print("   #%-4s %-64s" % (num, stmt))
            print("         %s   [source %s]" % (d, src))
        print()

    # OVERDUE_UNSETTLED deliberately does NOT block: the proposal was sound,
    # the settlement is late. Blocking it would punish the wrong party.
    blocked = sum(tally[v] for v in ("UNMEASURABLE", "IMPOSSIBLE", "EXPIRED_AT_BIRTH"))
    print("SUMMARY: %s" % dict(tally))
    print("WOULD BLOCK (once gated): %d of %d" % (blocked, len(rows)))
    print("\nMode: FLAG (week of 2026-08-18). Gate decision due 2026-08-25 — Kannaka's"
          "\nruling: promote to gate or admit the flag was theatre.")

    out = {"run_at": today, "mode": "flag", "would_block": blocked,
           "tally": dict(tally),
           "rows": [{"verdict": v, "number": n, "statement": s, "detail": d, "source": src}
                    for v, n, s, d, src in rows]}
    with open("precheck-%s.json" % today, "w") as f:
        json.dump(out, f, indent=2)
    print("written precheck-%s.json  (log destination: Kannaka, not Markets)" % today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
