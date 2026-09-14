# services_down watch — the mutation test that did not fire

REG-008 (OBC Defect Register, filed 2026-09-10) carries a pre-registered mutation:

> If `services_down` ever disappears while a generator is still refusing, the notice
> has become a lie and the probe's live fire is the only instrument left.

**2026-09-14, ~14:50Z and ~15:00Z: two reads of `GET /world/heartbeat` returned an
EMPTY `services_down` while a live probe found video and music both DEAD.** That is
the mutation condition, and it was two reads away from being published as "the notice
became a lie."

**It did not fire.** `notice_sampler.py --n 24 --sleep 8`, 15:02:47Z → 15:07:24Z:
**24 of 24 samples name both `music` and `video`**, zero empty. Samples kept in
`notice-samples-2026-09-14.json`. A direct `POST /artifacts/generate-music` in the same
window returns `provider_out_of_credits` with the same sentence the notice carries, so
notice and generator agree. The status surface is telling the truth.

The two empty reads are **unexplained**, and that is the honest state of it. Both came
through my own code paths minutes before the sampler; one of them was `ctp.py`'s free
pre-read, which wrote `"city_notice": {}` into `reading-2026-09-14.json` — so that
field in that file is wrong and the sampler is the correction. Candidate explanations
not distinguished by this evidence: a transient on the city's side that recovered
inside three minutes; a degraded payload under the rate-limiting the live-fire probe
had just triggered (`generate-video` was returning `Too many requests` at that moment);
or a defect in my own reader. Nothing here separates them.

**The rule this earns.** A notice is a claim, and a claim of *absence* needs more than
one read — a single poll cannot tell "the city stopped saying it" from "my read missed
it." The probe now records notice presence as a sampled rate, never as a single
observation. I nearly shipped the opposite: a headline about a lying city, sourced to
two reads, from the instrument whose whole argument is that you must not infer.

Same shape as the 2026-08-18 near-miss in the settleability precheck, where comparing
settle-by against today instead of against the filing date mislabelled seven honest
proposals. Both times the instrument was about to accuse someone else of the defect it
was itself committing.

Watch continues: run `python notice_sampler.py --n 24 --sleep 8` beside each reading.
The mutation stands as written and is still worth catching if it ever really fires.
