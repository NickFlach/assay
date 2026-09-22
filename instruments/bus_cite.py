#!/usr/bin/env python3
"""bus_cite: does a reading's evidence exist on the bus, exactly as cited?

A reading that settles a claim on something the constellation's bus carried
should say which message, precisely enough that anyone can check it. This
instrument defines that citation and checks it.

A citation, in a reading's top-level `cites` array:

    {"stream": "KANNAKA_MEMORY_EVENTS",
     "subject": "KANNAKA.events.memory.Kannaka.remember",
     "seq": 3187,
     "payload_sha256": "<hex sha256 of the payload, canonical JSON>",
     "how": "cites" | "quotes" | "resolves_on"}

`payload_sha256` is over `json.dumps(payload, sort_keys=True,
separators=(",", ":"), ensure_ascii=False)` encoded as UTF-8. Subject and seq
say where; the hash says what. A citation whose seq exists but whose payload
differs is `ALTERED`, not a pass, and one the export does not hold is
`UNRESOLVED`: absent from what was read, which is not proof of absence from
the bus (the README's first corollary).

The check runs against an export, not the live bus, so that it is
reproducible: a JSON array of messages as the nats ninja-portal MCP
`query_messages` returns them ({subject, seq, ts, payload, ...}).

    python instruments/bus_cite.py --export mem.json readings/*.json
    python instruments/bus_cite.py --hash '{"a": 1}'      # print a payload hash

Exit status is 0 only if every citation in every reading is VERIFIED.
Readings with no `cites` field are reported as `NO_CITES` and do not fail.

Written for kannaka-wave E-004, whose ground truth is "an event a settlement
reading cites, quotes, or resolves on". This makes that label a lookup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HOWS = {"cites", "quotes", "resolves_on"}
FIELDS = ("stream", "subject", "seq", "payload_sha256", "how")


def payload_sha256(payload) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_export(path: Path) -> dict[tuple[str, int], dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("messages", [])
    index = {}
    for m in data:
        index[(m["subject"], int(m["seq"]))] = m
    return index


def check(cite, index) -> tuple[str, str]:
    if not isinstance(cite, dict):
        return "MALFORMED", "a citation is an object"
    missing = [f for f in FIELDS if f not in cite]
    if missing:
        return "MALFORMED", f"missing {', '.join(missing)}"
    if cite["how"] not in HOWS:
        return "MALFORMED", f"how must be one of {sorted(HOWS)}"
    try:
        key = (cite["subject"], int(cite["seq"]))
    except (TypeError, ValueError):
        return "MALFORMED", "seq must be an integer"
    msg = index.get(key)
    if msg is None:
        return "UNRESOLVED", f"{key[0]} seq {key[1]} is not in the export"
    got = payload_sha256(msg.get("payload"))
    if got != cite["payload_sha256"]:
        return "ALTERED", f"payload hash {got[:12]}… is not the cited {str(cite['payload_sha256'])[:12]}…"
    return "VERIFIED", f"{key[0]} seq {key[1]} ({cite['how']})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--export", type=Path, help="bus export (JSON array of messages)")
    ap.add_argument("--hash", help="print the payload hash of this JSON and exit")
    ap.add_argument("readings", nargs="*", type=Path)
    a = ap.parse_args()

    if a.hash is not None:
        print(payload_sha256(json.loads(a.hash)))
        return 0
    if not a.export or not a.readings:
        ap.error("--export and at least one reading are required")

    index = load_export(a.export)
    ok = True
    for r in a.readings:
        reading = json.loads(r.read_text(encoding="utf-8"))
        cites = reading.get("cites")
        if cites is None:
            print(f"NO_CITES    {r}")
            continue
        if not isinstance(cites, list):
            print(f"MALFORMED   {r}: cites must be an array")
            ok = False
            continue
        for i, c in enumerate(cites):
            verdict, why = check(c, index)
            ok &= verdict == "VERIFIED"
            print(f"{verdict:<11} {r}#{i}: {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
