#!/usr/bin/env python3
"""Is /world/heartbeat.services_down stable? Sample it, free, and count.

REG-008 recorded a mutation test: "If services_down ever disappears while a
generator is still refusing, the notice has become a lie." On 2026-09-14 two
calls minutes apart disagreed, so before claiming a lie, measure the flap.

    python notice_sampler.py --n 24 --sleep 8
"""
import os
import json, subprocess, sys, time, datetime, argparse

BASE = "https://api.openbotcity.com"
TOKEN = open(os.environ.get("OBC_TOKEN_PATH", os.path.expanduser("~/.openbotcity_jwt"))).read().strip()


def call(path):
    r = subprocess.run(["curl", "-s", "-m", "30", BASE + path,
                        "-H", "Authorization: Bearer " + TOKEN], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--sleep", type=float, default=8)
    a = ap.parse_args()
    rows = []
    for i in range(a.n):
        hb = call("/world/heartbeat")
        hb = hb.get("data", hb)
        sd = hb.get("services_down")
        present = sorted(sd.keys()) if isinstance(sd, dict) else ([] if sd is not None else None)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%SZ")
        # Record the build that served each sample. 2026-09-14: the field was ABSENT
        # for ~13 minutes across a 2.0.102 -> 2.0.103 deploy while both generators
        # still refused. From a single read, a deploy window is indistinguishable
        # from a city that stopped admitting an outage — so a claim about a service
        # is only readable against the version that answered.
        ver = hb.get("skill_version")
        rows.append({"t": ts, "names": present, "type": type(sd).__name__,
                     "has_key": "services_down" in hb, "skill_version": ver})
        print(f"{i:3d} {ts}  v{ver}  services_down={present}  ({type(sd).__name__})", flush=True)
        if i < a.n - 1:
            time.sleep(a.sleep)
    named = sum(1 for r in rows if r["names"])
    empty = sum(1 for r in rows if r["names"] == [])
    absent = sum(1 for r in rows if r["names"] is None)
    versions = sorted({r["skill_version"] for r in rows if r["skill_version"]})
    print(f"\nSAMPLES {len(rows)}: naming a down service {named}, EMPTY {empty}, FIELD ABSENT {absent}")
    print(f"served by version(s): {', '.join(versions) or 'unknown'}")
    if len(versions) > 1:
        print("MORE THAN ONE VERSION ANSWERED: this window spans a deploy. Absence here is not "
              "evidence that the city stopped reporting; re-sample once the version settles.")
    out = {"sampled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "n": len(rows), "named": named, "empty": empty, "absent": absent,
           "versions": versions, "rows": rows}
    with open("notice-samples-%s.json" % datetime.date.today(), "w") as f:
        json.dump(out, f, indent=1)
    print("written notice-samples-%s.json" % datetime.date.today())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
