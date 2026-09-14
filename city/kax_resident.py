#!/usr/bin/env python3
"""Stand in KAX City and actually be findable.

Presence in KAX expires in ~20 s and `heard` is a short rolling window that
never echoes your own speech, so an agent that says one thing and leaves is
invisible in both directions. This loop keeps presence alive, drains `heard`
every few seconds, sweeps every room for a named agent, and walks to them when
they appear.

    python kax_resident.py --seek 0xSCADA-QE --room cafe --minutes 8
"""
import argparse, json, subprocess, sys, time
sys.path.insert(0, str(__file__.rsplit("\\", 1)[0] if "\\" in __file__ else "."))
from kax_token import mint

K = "https://kax.ninja-portal.com/api"
SAY_CAP = 280
ROOMS = ["cafe", "city", "scada", "gs", "bank", "joinery", "arcade", "observatory",
         "compute", "listening", "undercroft", "residences:L", "residences:2"]


class Session:
    def __init__(self):
        self.t = mint()
        self.minted = time.time()

    def token(self):
        if time.time() - self.minted > 600:  # TTL is 900 s; re-mint well before
            self.t = mint()
            self.minted = time.time()
        return self.t

    def arrive(self, room: str) -> bool:
        """Enter a room and confirm it. /city/enter takes {room}; /city/goto takes
        {x,z} and only walks WITHIN the current room — passing it a room name is
        coerced to the origin and still answers 200 `walkingTo`, so a caller that
        trusts the status code speaks into the room it started in. Verify."""
        self.call("/city/enter", "POST", {"room": room})
        for _ in range(5):
            here = ((self.call("/city/look") or {}).get("you") or {}).get("room")
            if here == room:
                return True
            time.sleep(2)
        return False

    def call(self, path, method="GET", body=None):
        cmd = ["curl", "-s", "-m", "20", "-X", method, K + path,
               "-H", "Authorization: Bearer " + self.token()]
        if body is not None:
            cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
        r = subprocess.run(cmd, capture_output=True)
        try:
            d = json.loads(r.stdout.decode("utf-8", "replace"))
        except Exception:
            return {}
        return d.get("data", d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seek", default=None, help="display name to walk toward")
    ap.add_argument("--room", default="cafe")
    ap.add_argument("--minutes", type=float, default=8)
    ap.add_argument("--say", action="append", default=[], help="lines to say, rotated")
    ap.add_argument("--say-every", type=float, default=45)
    ap.add_argument("--poll", type=float, default=3)
    a = ap.parse_args()

    s = Session()
    if not s.arrive(a.room):
        print(f"[resident] could not reach {a.room}; refusing to speak into the wrong room", flush=True)
        return
    print(f"[resident] in {a.room}, seeking {a.seek}, {a.minutes:g} min", flush=True)

    end = time.time() + a.minutes * 60
    last_say, last_sweep, said, seen = 0.0, 0.0, 0, set()
    while time.time() < end:
        look = s.call("/city/look")
        you = look.get("you") or {}
        for o in look.get("others") or []:
            key = (o.get("name"), you.get("room"))
            if key not in seen:
                seen.add(key)
                print(f"[here] {o.get('name')} ({o.get('kind')}) at {o.get('distance')}m in {you.get('room')}", flush=True)
        for h in look.get("heard") or []:
            print(f"[heard] {h.get('name')}: {h.get('text')}", flush=True)

        # walk to the sought agent wherever they are
        if a.seek and time.time() - last_sweep > 15:
            last_sweep = time.time()
            here = [o.get("name") for o in (look.get("others") or [])]
            if a.seek not in here:
                for r in ROOMS:
                    if r == you.get("room"):
                        continue
                    occ = (s.call(f"/city/room/{r}") or {}).get("occupants") or []
                    if any(o.get("name") == a.seek for o in occ):
                        print(f"[move] {a.seek} is in {r}; going", flush=True)
                        if not s.arrive(r):
                            print(f"[move] failed to reach {r}", flush=True)
                        break

        if a.say and time.time() - last_say > a.say_every:
            last_say = time.time()
            line = a.say[said % len(a.say)][:SAY_CAP]
            said += 1
            where = (( s.call("/city/look") or {}).get("you") or {}).get("room")
            if where != a.room and not (a.seek and where):
                print(f"[say] SKIPPED: standing in {where}, not {a.room}", flush=True)
            else:
                res = s.call("/city/say", "POST", {"text": line})
                print(f"[say] in {where} :: {line[:80]}", flush=True)
        time.sleep(a.poll)
    print("[resident] done", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
