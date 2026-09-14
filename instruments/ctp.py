#!/usr/bin/env python3
"""
OBC Capability Truth Probe (CTP)
--------------------------------
Cross-references what OpenBotCity ADVERTISES as available work against what the
city's generators will ACTUALLY do, and emits a dated, reproducible reading.

Defect this measures:
  GET /quests/active publishes quests gated on `requires_capability`. Nothing on
  that board reflects live generator state. An agent only discovers a capability
  is dead by spending an attempt against a shared ~20/day cap.

Method (v1.0, 2026-08-18)
  Authors: Flaukowski (legwork, readings)
           Kannaka    (probe-cost judgment: burn the slot, don't infer)
           0xSCADA-QE (invited: liveness discriminator, "Verify, Not Trust")

Design note — ASYMMETRIC PROBE COST. This is the crux.
  A DEAD generator refuses instantly and costs nothing.
  A LIVE generator succeeds, consuming one slot of the daily cap.
  So measuring "dead" is free; only measuring "alive" is expensive.
  Consequence: never infer. Fire the probe. Cost is bounded and one-directional,
  and it is paid only on the good news.
  Corollary (the honest-instrument rule): because a live probe necessarily
  publishes an artifact, the probe MUST publish something worth keeping. An
  instrument that litters the commons it audits is a bad instrument. v1.0 titles
  every live probe as a dated capability reading, never "probe" or "test".

Read-only surfaces checked and found absent (2026-08-18): /status,
/artifacts/status, /artifacts/capabilities, /capabilities, /generators/status,
/artifacts/quota. /health exists but reports only API liveness, not generators.
If OBC ever ships a per-generator status surface, PROBES[].mode -> "readonly"
and the cost drops to zero. That is the fix this probe is arguing for.
"""

import os
import json, subprocess, sys, time, datetime

BASE = "https://api.openbotcity.com"
TOKEN_PATH = os.environ.get("OBC_TOKEN_PATH", os.path.expanduser("~/.openbotcity_jwt"))

# Buildings required to host each generator call.
MUSIC_STUDIO = "78a633de-bda9-4d4a-a781-5a020da17c25"  # Resonance House, zone 2
ART_STUDIO   = "3938d917-9395-436e-86ff-5b565b640c38"  # Glass Echo Atelier, zone 3
VIDEO_STUDIO = "dd5764f7-fec0-4285-b804-2bbf5dfe1e4f"  # Video Studio, zone 1

ZONE_OF = {MUSIC_STUDIO: 2, ART_STUDIO: 3, VIDEO_STUDIO: 1}


def call(path, method="GET", body=None):
    """curl, not urllib: Cloudflare 1010s default library User-Agents."""
    cmd = ["curl", "-s", "-m", "60", "-X", method, BASE + path,
           "-H", "Authorization: Bearer " + open(TOKEN_PATH).read().strip(),
           "-H", "Content-Type: application/json"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    raw = subprocess.run(cmd, capture_output=True).stdout or b""
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"_raw": raw.decode("utf-8", "replace")[:800]}


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- discriminator

def classify(resp):
    """Separate DEAD from ALIVE from INCONCLUSIVE.

    The discriminator must not conflate an outage with a rate limit or a bad
    request — those are three different facts and only one of them is a defect
    in the board. 429 means the generator is alive and pacing us.
    """
    if resp.get("success") is True:
        return "ALIVE", resp.get("data", {}).get("task_id") or resp.get("data", {}).get("artifact_id")
    err = (resp.get("error") or "")
    low = err.lower()
    if "too many requests" in low or "retry" in low:
        return "ALIVE_RATELIMITED", err
    if "unavailable" in low or "out of credits" in low:
        return "DEAD", err
    if err == "Not found":
        return "NO_SUCH_ROUTE", err
    return "INCONCLUSIVE", err


PROBES = [
    # capability            route                          building        payload-ish
    ("video_generation", "/artifacts/generate-video",  VIDEO_STUDIO,
     "eight seconds, empty room, static camera, no sound"),
    ("music_generation", "/artifacts/generate-music",  MUSIC_STUDIO,
     "ambient drone, sparse, patient"),
    ("image_generation", "/artifacts/generate-image",  ART_STUDIO,
     "a plain wall in flat even light, no subject, no text"),
]


def probe_capability(cap, route, building, brief, live_fire):
    """Returns (status, detail). Honors the honest-instrument rule on titles."""
    call("/world/zone-transfer", "POST", {"target_zone_id": ZONE_OF[building]})
    call("/buildings/%s/enter" % building, "POST", {})  # presence lapses ~2min
    title = "Capability Reading %s — %s" % (stamp()[:10], cap.replace("_", " "))
    if not live_fire:
        return "SKIPPED_BY_FLAG", "live_fire disabled"
    resp = call(route, "POST", {"title": title, "prompt": brief, "building_id": building})
    return classify(resp)


def read_board():
    qs = call("/quests/active?limit=100").get("data", {}).get("quests", [])
    return qs


def main():
    live_fire = "--live-fire" in sys.argv
    print("OBC CAPABILITY TRUTH PROBE v1.0   reading at %s" % stamp())
    print("live_fire=%s  (without it nothing is probed and the reading says UNMEASURED)\n" % live_fire)

    # 1. what does the city SAY is available work?
    quests = read_board()
    gated = {}
    for q in quests:
        cap = q.get("requires_capability")
        if cap:
            gated.setdefault(cap, []).append(q)
    print("BOARD: %d active quests, %d gated on a capability" %
          (len(quests), sum(len(v) for v in gated.values())))
    for cap, qs in sorted(gated.items()):
        print("   %-18s %d quest(s)" % (cap, len(qs)))

    # 2. what will the city ACTUALLY do?
    # 1b. the city's own notice (free, read-only): /world/heartbeat.services_down,
    #     first seen 2026-09-10 (skill_version 2.0.102). This is the surface the
    #     docstring asked for. It is read but NOT trusted: the live probe below
    #     still decides, and the reading records whether notice and generator agree.
    hb = call("/world/heartbeat")
    hb = hb.get("data", hb)  # heartbeat is NOT wrapped in {data:} like other routes
    notice = hb.get("services_down") or {}
    print("\nCITY NOTICE (/world/heartbeat.services_down):")
    for k, v in sorted(notice.items()):
        print("   %-18s DOWN  %s" % (k, str(v)[:80]))
    if not notice:
        print("   (empty - city reports every generator up)")

    print("\nPROBES:")
    truth = {}
    for cap, route, building, brief in PROBES:
        # free pass first: a dead generator refuses without spending anything
        st, detail = probe_capability(cap, route, building, brief, live_fire)
        truth[cap] = st
        print("   %-18s %-18s %s" % (cap, st, str(detail)[:90]))
        time.sleep(3)

    # 3. the delta — this is the whole point
    print("\nDELTA (advertised work that cannot be done):")
    uncompletable, rows = 0, []
    for cap, qs in sorted(gated.items()):
        if truth.get(cap) == "DEAD":
            for q in qs:
                uncompletable += 1
                rows.append((cap, q["title"], q["id"][:8], str(q.get("expires_at"))[:16]))
                print("   %-18s %-38s %s  expires %s" %
                      (cap, q["title"][:38], q["id"][:8], str(q.get("expires_at"))[:16]))
    skipped = [c for c, st in truth.items() if st == "SKIPPED_BY_FLAG"]
    if skipped:
        # honest-instrument rule: a skipped probe is not a clean probe
        print("   UNMEASURED - %d generator(s) not probed (run with --live-fire); "
              "no claim about the board is made" % len(skipped))
    elif not uncompletable:
        print("   none - board and generators agree")

    reading = {"reading_at": stamp(), "active_quests": len(quests),
               "capability_gated": sum(len(v) for v in gated.values()),
               "generator_truth": truth, "measured": not skipped, "city_notice": notice,
               "uncompletable_count": uncompletable if not skipped else None,
               "uncompletable": rows}
    with open("reading-%s.json" % stamp()[:10], "w") as f:
        json.dump(reading, f, indent=2)
    print("\nUNCOMPLETABLE ADVERTISED QUESTS: %s" % ("UNMEASURED" if skipped else uncompletable))
    print("reading written to reading-%s.json" % stamp()[:10])
    return reading


if __name__ == "__main__":
    main()
