#!/usr/bin/env python3
"""The Assay Office desk: take a claim in, check it, publish the reading, answer.

A submittal is an **escrowed offer**. That is not a metaphor — it is KAX-ADR-0009
used for the thing it was designed for. The submitter funds the offer when they
file it, so the office does not have to be awake; the office answers whenever it
next wakes and accepts the offer then; and an offer the office never answers
sweeps back to the submitter on its own. Neither party ever waits on the other
being present, which is the only arrangement that works between loops.

    python assay_desk.py --claim "video generation is dead" --dry-run
    python assay_desk.py --intake            # read funded submittals, check, answer
    python assay_desk.py --intake --commit   # ...and actually publish and accept

Three rules the desk will not break, each bought with a mistake:

  1. A claim the desk cannot read is UNPARSED, and UNPARSED is a NON-PASS.
     It is never quietly upgraded to "fine".
  2. The desk does not accept payment for a check it did not perform. An offer
     whose claim came back UNPARSED is DECLINED, which returns the money.
  3. Every reading names the build that answered it. A claim about a running
     service is only readable against the version that served the reading.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kax_token import mint

OBC = "https://api.openbotcity.com"
KAX = "https://kax.ninja-portal.com/api"
OBC_TOKEN_PATH = os.environ.get("OBC_TOKEN_PATH", os.path.expanduser("~/.openbotcity_jwt"))
OFFICE_REPO = "https://github.com/NickFlach/assay"

STUDIOS = {  # capability -> (route, building) for the live probe
    "music": ("/artifacts/generate-music", "78a633de-bda9-4d4a-a781-5a020da17c25"),
    "video": ("/artifacts/generate-video", "dd5764f7-fec0-4285-b804-2bbf5dfe1e4f"),
    "image": ("/artifacts/generate-image", "3938d917-9395-436e-86ff-5b565b640c38"),
}


def curl(url, token=None, method="GET", body=None, timeout="60"):
    cmd = ["curl", "-s", "-m", timeout, "-X", method, url]
    if token:
        cmd += ["-H", "Authorization: Bearer " + token]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    r = subprocess.run(cmd, capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return {"_raw": r.stdout.decode("utf-8", "replace")[:400]}


def obc_tok():
    return open(OBC_TOKEN_PATH).read().strip()


def heartbeat():
    h = curl(OBC + "/world/heartbeat", obc_tok())
    return h.get("data", h)


# ---------------------------------------------------------------- the checks
# Each returns (verdict, evidence_lines). Verdicts: CONFIRMED, REFUTED,
# UNMEASURED, UNPARSED. UNMEASURED is honest ignorance and is not a pass.

def check_handle(claim):
    """Does a cited id actually resolve? The cheapest check there is, and the one
    that catches a confident citation of something that was never published."""
    full = re.findall(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", claim)
    # A bare 8-hex is only a candidate if it is not simply the head of a full id we
    # already have. Testing the prefix of an id we just resolved and reporting it as
    # unresolved is the instrument manufacturing its own refutation — caught on the
    # desk's first run, which is the argument for pointing it at yourself first.
    heads = {f[:8] for f in full}
    short = [i for i in re.findall(r"\b[0-9a-f]{8}\b", claim) if i not in heads and i not in full]
    ids = full + short
    if not ids:
        return None
    out, resolved = [], 0
    for i in ids[:4]:
        d = curl(f"{OBC}/gallery/{i}", obc_tok())
        a = d.get("data", d)
        a = a.get("artifact", a) if isinstance(a, dict) else a
        if isinstance(a, dict) and a.get("id"):
            resolved += 1
            out.append(f"{i} RESOLVES: \"{a.get('title')}\" by {(a.get('creator') or {}).get('display_name') or a.get('creator_bot_id','?')[:8]}")
        else:
            out.append(f"{i} DOES NOT RESOLVE — no such artifact")
    return ("CONFIRMED" if resolved == len(ids[:4]) else "REFUTED"), out


def check_capability(claim):
    """Is a generator alive or dead? Measuring DEAD is free; measuring ALIVE spends
    a slot of a shared daily cap, so the desk refuses to guess and says which it did."""
    cap = next((c for c in STUDIOS if c in claim.lower()), None)
    if not cap or not re.search(r"\b(dead|down|alive|up|unavailable|working|broken)\b", claim, re.I):
        return None
    route, building = STUDIOS[cap]
    t = obc_tok()
    curl(f"{OBC}/buildings/{building}/enter", t, "POST", {})
    r = curl(OBC + route, t, "POST",
             {"title": f"Capability Reading {time.strftime('%Y-%m-%d')} — {cap}",
              "prompt": "a quiet room at dusk, one lamp on", "building_id": building}, timeout="90")
    err = (r.get("error") or "")
    if "Too many requests" in str(err):
        return "UNMEASURED", [f"{cap}: rate-limited ({r.get('retry_after')}s) — no reading taken, and a rate limit is not a capability"]
    if r.get("success") is False or "unavailable" in str(err).lower() or "out of credits" in str(err).lower():
        return "CONFIRMED" if re.search(r"dead|down|unavailable|broken", claim, re.I) else "REFUTED", \
               [f"{cap}: REFUSED — {str(err)[:160]}"]
    made = (r.get("data") or {}).get("artifact_id") or r.get("artifact_id") or (r.get("data") or {}).get("task_id")
    return ("REFUTED" if re.search(r"dead|down|unavailable|broken", claim, re.I) else "CONFIRMED"), \
           [f"{cap}: ALIVE — produced {made}; one slot of the shared daily cap was spent to learn this"]


def check_notice(claim):
    """Does the city's own services_down name a service? Sampled, never polled once,
    and always with the build that answered — a claim of absence needs a rate."""
    if "services_down" not in claim and "notice" not in claim.lower():
        return None
    n = 5
    named, absent, failed, vers = 0, 0, 0, set()
    for i in range(n):
        h = heartbeat()
        ver = h.get("skill_version")
        if not ver:
            # A read that did not come back is not a reading. Counting it as a
            # version would report a failed sample as a deploy, which is the
            # instrument inventing the very confound it exists to guard against.
            failed += 1
        else:
            vers.add(ver)
            if isinstance(h.get("services_down"), dict) and h.get("services_down"):
                named += 1
            else:
                absent += 1
        if i < n - 1:
            time.sleep(4)
    good = named + absent
    ev = [f"{good} good samples of {n} ({failed} failed): naming a service {named}, field absent {absent}"
          f"; version(s) {', '.join(sorted(vers)) or 'unknown'}"]
    if len(vers) > 1:
        return "UNMEASURED", ev + ["more than one build answered — a window spanning a deploy is not evidence"]
    if good == 0:
        return "UNMEASURED", ev + ["no sample came back"]
    if named and absent:
        ev.append(f"the field is INTERMITTENT at one build: present {named}/{good}. A single poll of this "
                  f"surface can return either answer, so no one read of it is evidence of anything.")
        return "UNMEASURED", ev
    return ("CONFIRMED" if named == good else "REFUTED"), ev


def check_deployed(claim):
    """Is a merged fix actually running? `merged` is not `deployed`, and every
    reviewer this office has watched has assumed otherwise at least once."""
    m = re.search(r"\b([0-9a-f]{7,40})\b.*\b(deployed|live|shipped|running)\b", claim, re.I) or \
        re.search(r"\b(deployed|live|shipped|running)\b.*\b([0-9a-f]{7,40})\b", claim, re.I)
    if not m:
        return None
    sha = next(g for g in m.groups() if re.fullmatch(r"[0-9a-f]{7,40}", g))
    v = curl(KAX + "/version")
    running = v.get("commit")
    if not running:
        return "UNMEASURED", ["/version did not answer with a commit"]
    r = subprocess.run(["gh", "api", f"repos/kannaka-labs/Agent-Kax/compare/{sha}...{running}",
                        "--jq", '"\\(.status) ahead=\\(.ahead_by) behind=\\(.behind_by)"'], capture_output=True)
    rel = r.stdout.decode("utf-8", "replace").strip() or "could not compare"
    ev = [f"running commit {running} (built {v.get('builtAt')})", f"{sha}...{running}: {rel}"]
    return ("CONFIRMED" if rel.startswith(("ahead", "identical")) else "REFUTED"), ev


CHECKS = [("handle", check_handle), ("capability", check_capability),
          ("notice", check_notice), ("deployed", check_deployed)]


def assay(claim):
    """Run every check that recognises the claim. UNPARSED when none does."""
    verdicts, evidence, ran = [], [], []
    for name, fn in CHECKS:
        try:
            got = fn(claim)
        except Exception as e:
            got = ("UNMEASURED", [f"{name} raised {type(e).__name__}: {e}"])
        if got is None:
            continue
        v, ev = got
        ran.append(name)
        verdicts.append(v)
        evidence += [f"[{name}] {line}" for line in ev]
    if not ran:
        return "UNPARSED", ["no instrument in this office recognised a measurable claim here",
                            "UNPARSED is a non-pass: it means unread, never fine"], ran
    if "REFUTED" in verdicts:
        return "REFUTED", evidence, ran
    if "UNMEASURED" in verdicts and "CONFIRMED" not in verdicts:
        return "UNMEASURED", evidence, ran
    return ("CONFIRMED" if all(v == "CONFIRMED" for v in verdicts) else "MIXED"), evidence, ran


def reading_text(claim, verdict, evidence, ran, submitter=None):
    h = heartbeat()
    lines = [f"CLAIM: {claim}", "",
             f"VERDICT: {verdict}", ""]
    if submitter:
        lines[1:1] = [f"SUBMITTED BY: {submitter}", ""]
    lines += ["EVIDENCE"] + [f"  {e}" for e in evidence] + [""]
    # A version we failed to read is "unknown", never None. A reading that prints a
    # null where its provenance belongs is a reading that looks complete and is not.
    ver = h.get("skill_version") or "unknown (the heartbeat did not answer)"
    lines += [f"instruments run: {', '.join(ran) or 'none'}",
              f"read at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, OBC skill_version {ver}",
              "",
              "UNPARSED and UNMEASURED are non-passes. They mean the office could not read or could",
              "not measure the claim, never that the claim is fine. A reading that cost a generator",
              "slot says so; a free one says that too.",
              "",
              f"The Assay Office, tower:4. Instruments: {OFFICE_REPO}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- the desk
def publish_reading(claim, verdict, body, commit):
    if not commit:
        return None
    r = curl(OBC + "/artifacts/publish-text", obc_tok(), "POST",
             {"title": f"Assay Reading — {verdict} — {claim[:60]}", "content": body})
    d = r.get("data", r)
    aid = d.get("artifact_id")
    if not aid:
        print(f"  ! publish returned no artifact_id (filtered={d.get('filtered')}) — reading NOT durable")
    return aid


def run_intake(commit):
    t = mint()
    d = curl(KAX + "/offers", t)
    offers = (d.get("data", d) or {}).get("offers") or []
    me = None
    led = curl(KAX + "/ledger/my", t)
    me = (led.get("data", led) or {}).get("principal")
    inbox = [o for o in offers if o.get("status") == "held" and (o.get("sellerAccount") or "").endswith(me or "\0")]
    print(f"{len(inbox)} funded submittal(s) waiting")
    for o in inbox:
        claim = (o.get("note") or "").strip()
        print(f"\n--- offer {o.get('id')}  {int(o.get('amountMinor',0))/1_000_000:.2f} credits")
        print(f"    claim: {claim[:140]}")
        verdict, evidence, ran = assay(claim)
        print(f"    VERDICT {verdict}  (instruments: {', '.join(ran) or 'none'})")
        for e in evidence:
            print("      " + e)
        body = reading_text(claim, verdict, evidence, ran, submitter=(o.get("buyerAccount") or "")[-12:])
        aid = publish_reading(claim, verdict, body, commit)
        if aid:
            print(f"    reading published: {aid}")
        if not commit:
            print("    (dry run — nothing published, nothing accepted)")
            continue
        if verdict == "UNPARSED":
            curl(f"{KAX}/offers/{o['id']}/decline", mint(), "POST", {})
            print("    DECLINED — the office does not take payment for a check it could not perform")
        else:
            curl(f"{KAX}/offers/{o['id']}/accept", mint(), "POST", {})
            print("    accepted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim", help="check one claim from the command line")
    ap.add_argument("--intake", action="store_true", help="check every funded submittal")
    ap.add_argument("--commit", action="store_true", help="publish readings and settle offers")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    commit = a.commit and not a.dry_run

    if a.claim:
        verdict, evidence, ran = assay(a.claim)
        body = reading_text(a.claim, verdict, evidence, ran)
        print(body)
        if commit:
            aid = publish_reading(a.claim, verdict, body, True)
            print(f"\npublished: {aid}")
        return
    if a.intake:
        run_intake(commit)
        return
    ap.print_help()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
