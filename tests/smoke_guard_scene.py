"""Away Guard scene: phrase bags, the eyes' behaviour table, the stage director, and painting.

Everything time-driven is replayed from a seeded ``random.Random`` with synthetic clocks, so the
statistics (double/slow blink share, routine mix) are exact and repeatable. The widget checks run
on Qt's offscreen platform: nothing here opens a real window.
"""

import math
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import guard_scene as G  # noqa: E402
import sound  # noqa: E402
from animator import SpriteSheet  # noqa: E402

SCRATCH = _scratch()
ASSETS = SRC / "assets"
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


# --- phrases ---------------------------------------------------------------------------------

print("\n--- phrase file and merge ---")
builtin = G.load_phrases(ASSETS, None)
for cat in ("idle", "pointer_near", "typing", "wrong_1", "wrong_2", "blackout", "unlock",
            "peek_return", "sleeping", "coffee", "counting", "poked"):
    check(f"category {cat} present", bool(builtin.get(cat)))
check("idle has the full starter set", len(builtin["idle"]) >= 10, len(builtin["idle"]))

user_dir = SCRATCH / "guard_phrases_user"
user_dir.mkdir(exist_ok=True)
(user_dir / G.PHRASES_FILE).write_text(
    '{"idle": ["My own line.", "Agents at work. Please do not feed the agents."],'
    ' "custom": ["A brand new category {nope}"]}', encoding="utf-8")
merged = G.load_phrases(ASSETS, user_dir)
check("user line merged in", "My own line." in merged["idle"])
check("duplicate not added twice",
      merged["idle"].count("Agents at work. Please do not feed the agents.") == 1)
check("built-ins kept", len(merged["idle"]) == len(builtin["idle"]) + 1)
check("new user category accepted", merged.get("custom") == ["A brand new category {nope}"])
(user_dir / G.PHRASES_FILE).write_text("{ not json", encoding="utf-8")
broken = G.load_phrases(ASSETS, user_dir)
check("malformed user file ignored", broken == builtin)
check("missing assets dir falls back to built-in lines",
      bool(G.load_phrases(SCRATCH / "no-such-dir", None).get("blackout")))

print("\n--- shuffle bag ---")
bag = G.PhraseBag(builtin, random.Random(7))
for cat, lines in builtin.items():
    n = len(lines)
    drawn = [bag.next(cat, minutes=12) for _ in range(n * 6)]
    rounds_ok = all(len(set(drawn[i * n:(i + 1) * n])) == n for i in range(6))
    check(f"{cat}: every line once before any repeats (x6 bags)", rounds_ok)
    if n > 1:
        check(f"{cat}: no back-to-back repeat across refills",
              all(a != b for a, b in zip(drawn, drawn[1:])))
lines = [bag.next("counting", minutes=42)]
check("{minutes} filled in", "42" in lines[0] and "{minutes}" not in lines[0], lines[0])
weird = G.PhraseBag({"idle": ["brace { and {unknown} and {minutes}"]}, random.Random(1))
check("stray/unknown braces never raise", weird.next("idle", minutes=3).startswith("brace"))
check("unknown category falls back to idle", bag.next("no_such_category") in builtin["idle"])

# --- the eyes --------------------------------------------------------------------------------

print("\n--- eyes: behaviour table ---")
CENTRE = QPointF(1000, 400)


def run_eyes(model, seconds, *, start=0.0, fps=30, pointer=None, speed=0.0, idle0=0.0,
             idle_grows=True, typing=False, near=False, each=None, **extra):
    """Drive a model for ``seconds``; returns the end time."""
    dt = 1.0 / fps
    t = start
    steps = int(round(seconds * fps))
    for i in range(steps):
        t = start + (i + 1) * dt
        idle = idle0 + (t - start if idle_grows else 0.0)
        model.update(dt, t, pointer or CENTRE, CENTRE, pointer_speed=speed, idle_s=idle,
                     typing=typing, near_field=near, **extra)
        if each:
            each(model, t)
    return t


m = G.EyesModel(random.Random(1))
run_eyes(m, 1.0, pointer=QPointF(1600, 400), idle_grows=False)
check("pointer right -> pupils go right", m.pupil_x > 0.8 and abs(m.pupil_y) < 0.1, m.pupil)
m2 = G.EyesModel(random.Random(1))
run_eyes(m2, 1.0, pointer=QPointF(5000, 5000), idle_grows=False)
check("pupil stays inside the unit disc", (m2.pupil_x ** 2 + m2.pupil_y ** 2) <= 1.0001, m2.pupil)
check("diagonal pointer -> diagonal gaze", m2.pupil_x > 0.6 and m2.pupil_y > 0.6, m2.pupil)
# Saccades replaced the old 15 %-per-frame glide: a pointer that jumps gets a jump back.
print("\n--- eyes: saccades, fixations, pursuit ---")
sac = G.EyesModel(random.Random(1))
xs, speeds = [], []
run_eyes(sac, 1.5, pointer=QPointF(CENTRE.x() + 0.8 * G.TRACK_RANGE_PX, 400), idle_grows=False,
         each=lambda mm, t: (xs.append(mm.pupil_x), speeds.append(mm.pupil_speed)))
check("saccade: a big jump is mostly done within 2 frames (no glide)", xs[1] > 0.6, round(xs[1], 2))
check("saccade: pupil velocity spikes", max(speeds[:3]) > 8.0, round(max(speeds[:3]), 1))
check("saccade: overshoots a little, then settles",
      0.82 < max(xs[:6]) < 1.0 and abs(xs[-1] - 0.8) < 0.07, (round(max(xs[:6]), 3), round(xs[-1], 3)))
check("fixation: velocity back to ~0 and holds", max(speeds[10:]) < 0.5, round(max(speeds[10:]), 3))
check("one saccade for one jump", sac.saccade_count == 1, sac.saccade_count)
quiet_sig = []
run_eyes(sac, 5.0, start=1.5, pointer=QPointF(CENTRE.x() + 0.8 * G.TRACK_RANGE_PX, 400),
         idle_grows=False, each=lambda mm, t: quiet_sig.append(mm.signature()))
changes = sum(1 for a, b in zip(quiet_sig, quiet_sig[1:]) if a != b)
check("fixation is quiet: few repaints while the pointer sits still", changes < len(quiet_sig) * 0.15,
      f"{changes}/{len(quiet_sig)}")

pu = G.EyesModel(random.Random(1))
run_eyes(pu, 0.6, idle_grows=False)
n0 = pu.saccade_count
pursuit_v = []
for i in range(60):  # a slow pointer: 200 px/s to the right for 2 s
    t = 0.6 + (i + 1) / 30
    pu.update(1 / 30, t, QPointF(CENTRE.x() + 200 * (i + 1) / 30, 400), CENTRE, pointer_speed=200.0,
              idle_s=0.0, typing=False, near_field=False)
    pursuit_v.append(pu.pupil_speed)
check("slow pointer -> smooth pursuit, no saccades", pu.saccade_count == n0 and pu.pupil_x > 0.6,
      (pu.saccade_count - n0, round(pu.pupil_x, 2)))
check("...at a gentle, steady speed", max(pursuit_v) < 2.0, round(max(pursuit_v), 2))
fast = G.EyesModel(random.Random(1))
run_eyes(fast, 0.6, idle_grows=False)
n0 = fast.saccade_count
for i in range(30):  # a quick sweep: 900 px/s
    t = 0.6 + (i + 1) / 30
    fast.update(1 / 30, t, QPointF(CENTRE.x() - 450 + 900 * (i + 1) / 30, 400), CENTRE,
                pointer_speed=900.0, idle_s=0.0, typing=False, near_field=False)
check("fast pointer -> catch-up saccades", fast.saccade_count - n0 >= 3, fast.saccade_count - n0)
look = G.EyesModel(random.Random(3))
run_eyes(look, 30, idle0=3.0)
check("idle looking is saccadic too", look.saccade_count >= 6, look.saccade_count)
red_sac = G.EyesModel(random.Random(3))
red_sac.set_mood("red")
run_eyes(red_sac, 3, pointer=QPointF(1600, 400), idle_grows=False)
check("blackout eyes glide, never saccade", red_sac.saccade_count == 0)

# idle gaze: with the pointer still, the pupils wander on their own
gaze = G.EyesModel(random.Random(3))
seen = []
run_eyes(gaze, 30, idle0=3.0, each=lambda mm, t: seen.append(round(mm.pupil_x, 1)))
check("idle gaze wanders (2-60 s idle)", len(set(seen)) >= 6, len(set(seen)))
check("idle gaze stays open-eyed (not sleepy yet)", not gaze.sleepy and not gaze.sleeping)

# blinks
blinks = G.EyesModel(random.Random(11))
run_eyes(blinks, 1800, idle_grows=False)
counts = blinks.blink_counts
total = sum(counts.values())
check("blinks every 2-6 s on average", 1800 / 6.5 < total < 1800 / 2.0, total)
check("~10 % double blinks", 0.06 < counts["double"] / total < 0.14, counts)
check("~5 % slow blinks", 0.02 < counts["slow"] / total < 0.08, counts)
closed_frames = []
bl = G.EyesModel(random.Random(5))
run_eyes(bl, 20, idle_grows=False, each=lambda mm, t: closed_frames.append(mm.blinking))
check("blinking flag shows up during blinks", any(closed_frames))

# fast pointer -> widen
w = G.EyesModel(random.Random(2))
run_eyes(w, 1.0, idle_grows=False)
rest = w.lid_open if not w.blinking else G.LID_REST
run_eyes(w, 0.3, start=1.0, speed=5000.0, idle_grows=False)
check("fast pointer widens the lids (+20 %)", w.widened and w.lid_open > rest + 0.1,
      (round(rest, 2), round(w.lid_open, 2)))
run_eyes(w, 1.6, start=1.3, speed=0.0, idle_grows=False)
check("...for about a second", not w.widened)

# near the PIN field -> glance down, then back up
gl = G.EyesModel(random.Random(2))
run_eyes(gl, 0.5, pointer=QPointF(1000, 100), idle_grows=False)  # pointer above: looks up
run_eyes(gl, 0.4, start=0.5, pointer=QPointF(1000, 100), near=True, idle_grows=False)
check("pointer near field -> glance down", gl.glancing and gl.pupil_y > 0.5, gl.pupil)
run_eyes(gl, 1.2, start=0.9, pointer=QPointF(1000, 100), near=True, idle_grows=False)
check("...then back up at the pointer", not gl.glancing and gl.pupil_y < 0, gl.pupil)

# typing -> squeeze shut
ty = G.EyesModel(random.Random(2))
run_eyes(ty, 0.5, typing=True, idle_grows=False)
check("typing -> eyes squeezed shut", ty.squeezed and ty.lid_open == 0.0)

# sleepy at 60 s, asleep at 5 min, one-eye peeks, startle on input
sl = G.EyesModel(random.Random(4))
run_eyes(sl, 5, idle0=70.0, idle_grows=False)
check("60 s idle -> sleepy half lids", sl.sleepy and 0.2 < sl._base < 0.6, round(sl._base, 2))
run_eyes(sl, 5, start=5, idle0=301.0, idle_grows=False)
check("5 min idle -> asleep, closed", sl.sleeping and sl.lid_open == 0.0)
peeks = []
run_eyes(sl, 100, start=10, idle0=306.0, fps=10,
         each=lambda mm, t: peeks.append((t, mm.one_eye_peek, mm.peek_lid)))
peek_times = [t for t, on, _ in peeks if on]
check("one eye pops open within ~50 s of sleeping", bool(peek_times) and peek_times[0] < 10 + 50,
      peek_times[:1])
check("...more than once over 100 s", sl.peek_count >= 2, sl.peek_count)
check("...and actually opens", max(lid for _, _, lid in peeks) > 0.5)
run_eyes(sl, 0.1, start=110, idle0=0.0, idle_grows=False)
check("input while sleeping -> snap open with a shake",
      not sl.sleeping and sl.widened and sl.shake != 0.0, round(sl.shake, 2))

# moods
mo = G.EyesModel(random.Random(9))
mo.set_mood("squint")
run_eyes(mo, 2, idle_grows=False)
check("wrong 1 -> squint", mo.lid_open <= G.LID_SQUINT + 1e-6, round(mo.lid_open, 2))
mo.set_mood("veins")
run_eyes(mo, 2, start=2, idle_grows=False)
check("wrong 2 -> narrower slits", mo.lid_open <= G.LID_SLITS + 1e-6, round(mo.lid_open, 2))
red = G.EyesModel(random.Random(9))
red.set_mood("red")
blinked = []
run_eyes(red, 60, pointer=QPointF(1600, 400), idle_grows=False,
         each=lambda mm, t: blinked.append(mm.blinking))
check("blackout -> no blinking at all", not any(blinked))
check("blackout -> never falls asleep", not red.sleeping)
slow_red = G.EyesModel(random.Random(9))
slow_red.set_mood("red")
slow_red.update(1 / 30, 0.0, QPointF(3000, 400), CENTRE, pointer_speed=0, idle_s=0,
                typing=False, near_field=False)
check("blackout -> slow, heavy tracking", slow_red.pupil_x < 0.07, round(slow_red.pupil_x, 3))
st = G.EyesModel(random.Random(9))
st.set_mood("stare")
run_eyes(st, 10, pointer=QPointF(1600, 400), idle_grows=False)
check("peek return -> stare: wide open, on the pointer",
      st.lid_open > 0.95 and st.pupil_x > 0.8 and st.blink_counts == {"single": 0, "double": 0, "slow": 0})
hp = G.EyesModel(random.Random(9))
hp.set_mood("happy")
check("happy mood accepted", hp.mood == "happy")
hp.set_mood("nonsense")
check("unknown mood -> calm", hp.mood == "calm")
bump = G.EyesModel(random.Random(9))
run_eyes(bump, 0.1, idle_grows=False)
before = sum(bump.blink_counts.values())
bump.bump()
run_eyes(bump, 0.1, start=0.1, idle_grows=False)
check("football bump forces a blink", sum(bump.blink_counts.values()) == before + 1)

print("\n--- eyes: blink shape ---")
bs = G.EyesModel(random.Random(5))
run_eyes(bs, 0.5, idle_grows=False)
t_bs = 0.5
while bs.blinking:  # let any natural blink finish first
    t_bs = run_eyes(bs, 1 / 30, start=t_bs, idle_grows=False)
bs._next_blink = 1e9  # only the forced one
bs.bump()
trace = []
run_eyes(bs, 0.4, start=t_bs, idle_grows=False, each=lambda mm, t: trace.append((mm.squash, mm.lids)))
sq = [max(a) for a, _ in trace]
check("blink squashes the eye vertically", max(sq) > 0.9, round(max(sq), 2))
check("...and the lids really shut", min(min(lid) for _, lid in trace) < 0.05)
peak = sq.index(max(sq))
closing = sum(1 for v in sq[:peak] if v > 0)
opening = sum(1 for v in sq[peak + 1:] if v > 0)
check("eased: closes fast, opens slower", closing < opening, (closing, opening))

lag_blinks, starts = set(), []


def _lag(mm, t):
    if mm.blinking:
        if not starts or starts[-1] != mm._blink_start:
            starts.append(mm._blink_start)
        if abs(mm.lids[0] - mm.lids[1]) > 0.1:
            lag_blinks.add(mm._blink_start)


run_eyes(G.EyesModel(random.Random(11)), 600, idle_grows=False, each=_lag)
share = len(lag_blinks) / max(1, len(starts))
check("eyes blink together, one a frame late now and then", 0.1 < share < 0.5, round(share, 2))
total_full = sum(blinks.blink_counts.values())
half_share = blinks.half_blink_count / (total_full + blinks.half_blink_count)
check("occasional half-blinks (~8 %)", 0.04 < half_share < 0.13, round(half_share, 3))
half_peaks, cur = [], []


def _half(mm, t):
    if mm.blinking and mm.blink_kind == "half":
        cur.append(max(mm.squash))
    elif cur:
        half_peaks.append(max(cur))
        cur.clear()


run_eyes(G.EyesModel(random.Random(11)), 300, idle_grows=False, each=_half)
check("half-blinks stop about halfway", bool(half_peaks) and all(0.3 < v < 0.65 for v in half_peaks),
      [round(v, 2) for v in half_peaks[:5]])
runs, run = [], [0]


def _lazy(mm, t):
    if mm.blinking:
        run[0] += 1
    elif run[0]:
        runs.append(run[0])
        run[0] = 0


run_eyes(G.EyesModel(random.Random(4)), 60, idle0=70.0, idle_grows=False, each=_lazy)
check("sleepy -> lazy slow blinks (> 1.2 s)", bool(runs) and min(runs) >= 1.2 * 30,
      [round(r / 30, 2) for r in runs[:4]])

print("\n--- eyes: depth, brows, fun ---")
dil = {}
for mood in ("stare", "calm", "squint", "veins", "red"):
    mm = G.EyesModel(random.Random(2))
    mm.set_mood(mood)
    run_eyes(mm, 3, pointer=QPointF(1300, 400), idle_grows=False)
    dil[mood] = mm.dilation
check("dilation by mood: stare > calm > squint > veins > red",
      dil["stare"] > dil["calm"] > dil["squint"] > dil["veins"] > dil["red"],
      {k: round(v, 2) for k, v in dil.items()})
check("...wide when staring, pin-prick in the blackout", dil["stare"] > 0.9 and dil["red"] < 0.05)
brow = {}
for mood in ("calm", "squint", "stare", "veins", "red"):
    mm = G.EyesModel(random.Random(2))
    mm.set_mood(mood)
    run_eyes(mm, 2, pointer=QPointF(1300, 400), idle_grows=False)
    brow[mood] = (mm.brow_pose, mm.brows)
pose, (bl, br) = brow["calm"]
check("calm brows: flat and level", pose == "calm" and abs(bl[0] - br[0]) < 0.2 and abs(bl[1]) < 0.2,
      brow["calm"])
pose, (bl, br) = brow["squint"]
check("suspicious: one brow raised", pose == "suspicious" and abs(bl[0] - br[0]) >= 1.0
      and max(bl[0], br[0]) >= 2.0, brow["squint"])
pose, (bl, br) = brow["stare"]
check("surprised: both up", pose == "surprised" and bl[0] >= 2.5 and br[0] >= 2.5, brow["stare"])
pose, (bl, br) = brow["veins"]
check("angry: angled down to the nose", pose == "angry" and bl[1] <= -3 and br[1] <= -3, brow["veins"])
check("blackout brows are angry too", brow["red"][0] == "angry")
ra = G.EyesModel(random.Random(2))
ra.set_mood("squint")
run_eyes(ra, 1.0, idle_grows=False)
ra.really()
run_eyes(ra, 0.5, start=1.0, idle_grows=False)
raised = max(ra.brows[0][0], ra.brows[1][0])
other = min(ra.brows[0][0], ra.brows[1][0])
check("wrong attempt -> 'really?' one-brow raise",
      ra.brow_pose == "really" and raised > 3.0 and other < 1.5, ra.brows)
run_eyes(ra, G.REALLY_S + 0.5, start=1.5, idle_grows=False)
check("...briefly", ra.brow_pose == "suspicious", ra.brow_pose)

SPAN = 700.0
ce = G.EyesModel(random.Random(2))
run_eyes(ce, 1.0, pointer=QPointF(CENTRE.x() + 20, CENTRE.y() + 30), idle_grows=False, eye_span_px=SPAN)
check("pointer parked between the eyes -> cross-eyed",
      ce.crossed and ce.pupil_l[0] > 0.5 and ce.pupil_r[0] < -0.5, (ce.pupil_l, ce.pupil_r))
run_eyes(ce, 1.0, start=1.0, pointer=QPointF(CENTRE.x() + 600, CENTRE.y()), idle_grows=False,
         eye_span_px=SPAN)
check("...and back to normal when it leaves",
      not ce.crossed and abs(ce.pupil_l[0] - ce.pupil_r[0]) < 0.05, (ce.pupil_l, ce.pupil_r))
nce = G.EyesModel(random.Random(2))
run_eyes(nce, 1.0, pointer=QPointF(CENTRE.x() + 20, CENTRE.y()), idle_grows=False)
check("no eye span given -> never crosses", not nce.crossed and nce.cross == 0.0)
passing = G.EyesModel(random.Random(2))
run_eyes(passing, 0.15, pointer=QPointF(CENTRE.x(), CENTRE.y()), idle_grows=False, eye_span_px=SPAN)
check("a pointer just passing through doesn't cross them", not passing.crossed)

wi = G.EyesModel(random.Random(2))
run_eyes(wi, 0.5, idle_grows=False)
wi.wince()
run_eyes(wi, 0.1, start=0.5, idle_grows=False)
check("wince: squeezed shut, brows down", wi.wincing and wi.lids == (0.0, 0.0)
      and wi.brow_pose == "wince" and min(wi.squash) > 0.9, (wi.lids, wi.brow_pose))
run_eyes(wi, 0.45, start=0.6, idle_grows=False)
check("...for ~0.4 s", not wi.wincing and max(wi.lids) > 0.5, wi.lids)

bf = G.EyesModel(random.Random(2))
ys = []
for i in range(150):  # the ball bobbing 80 px, well below the eyes; the pointer idle
    t = (i + 1) / 30
    by = 900 + 40 * math.sin(t * 6.0)
    bf.update(1 / 30, t, QPointF(300, 100), CENTRE, pointer_speed=0.0, idle_s=2.0 + t, typing=False,
              near_field=False, ball=QPointF(1050, by), eye_span_px=SPAN)
    ys.append((by, bf.pupil_y))
check("football: eyes follow the ball", bf.following_ball and min(y for _, y in ys[60:]) > 0.0)
late = ys[60:]
hi = [y for by, y in late if by < 875]
lo = [y for by, y in late if by > 925]
check("...up and down with the bounce",
      bool(hi) and bool(lo) and sum(lo) / len(lo) - sum(hi) / len(hi) > 0.25,
      (round(sum(hi) / max(1, len(hi)), 2), round(sum(lo) / max(1, len(lo)), 2)))
bf.update(1 / 30, 5.1, QPointF(1600, 400), CENTRE, pointer_speed=900.0, idle_s=0.0, typing=False,
          near_field=False, ball=QPointF(1050, 900), eye_span_px=SPAN)
check("...but a moving pointer wins", not bf.following_ball)

# determinism
a, b = G.EyesModel(random.Random(77)), G.EyesModel(random.Random(77))
run_eyes(a, 120, idle_grows=True)
run_eyes(b, 120, idle_grows=True)
check("same seed -> identical eyes", a.blink_counts == b.blink_counts and a.pupil == b.pupil)

# --- the stage director ----------------------------------------------------------------------

print("\n--- stage director ---")
sheet = SpriteSheet(ASSETS / "nib_sheet.png", ASSETS / "frames.json", 3)
REAL = set(sheet.states())
WITH_COVER = REAL | {"cover_eyes"}
WITHOUT_COVER = REAL - {"cover_eyes"}


def drive(d, seconds, *, start=0.0, fps=10, idle=lambda t: 0.0, each=None):
    dt = 1.0 / fps
    t = start
    for i in range(int(round(seconds * fps))):
        t = start + (i + 1) * dt
        d.update(t, idle(t))
        if each:
            each(d, t)
    return t


d = G.StageDirector(WITH_COVER, random.Random(123), phrases=G.PhraseBag(builtin, random.Random(5)))
states_seen = set()
sounds_seen = []
phrases_seen = []


def collect(dd, t):
    states_seen.add(dd.state)
    s = dd.take_sound()
    if s:
        sounds_seen.append(s)
    ph = dd.take_phrase()
    if ph:
        phrases_seen.append((t, ph))


end = drive(d, 2 * 3600, each=collect)
hist = d.history
check("routines run (2 h simulated)", len(hist) > 300, len(hist))
check("never the same routine twice in a row", all(x != y for x, y in zip(hist, hist[1:])))
mix = Counter(hist)
check("every routine shows up", set(mix) == set(G.ROUTINE_WEIGHTS), dict(mix))
check("patrol is the most common", mix.most_common(1)[0][0] == "patrol", dict(mix))
d2 = G.StageDirector(WITH_COVER, random.Random(5))
drive(d2, 19 * 60)
check("'tired' never before 20 min", "tired" not in d2.history)
check("every state used exists in the sheet", states_seen <= WITH_COVER, states_seen - WITH_COVER)
check("x stays on the stage while patrolling",
      G.STAGE_MIN - 1e-6 <= d.x <= G.STAGE_MAX + 1e-6, d.x)
# Routine lines (coffee, counting) restart the idle-line clock, so the 9-14 s rule is between
# consecutive lines of any kind - he never goes quiet for long and never talks over himself.
line_times = [t for t, _ in phrases_seen]
gaps = [b - a for a, b in zip(line_times, line_times[1:])]
check("a new line every 9-14 s (no overlaps)", gaps and min(gaps) >= 0.0 and max(gaps) <= 14.2,
      (round(min(gaps), 1), round(max(gaps), 1)) if gaps else None)
counting = [text for _, (cat, text) in phrases_seen if cat == "counting"]
check("counting line carries the minutes", counting and "{minutes}" not in counting[-1], counting[-1:])
check("sound names are all real sound events", set(sounds_seen) <= sound.EVENTS, set(sounds_seen))

print("\n--- reactions ---")


def fresh(states=WITH_COVER, seed=1):
    dd = G.StageDirector(states, random.Random(seed), phrases=G.PhraseBag(builtin, random.Random(seed)))
    drive(dd, 3.0)
    dd.take_sound()
    dd.take_phrase()
    return dd


r = fresh()
r.react("typing_start")
check("typing -> cover_eyes", r.state == "cover_eyes" and r.hold_frame is None)
check("...with a typing line", r.take_phrase()[0] == "typing")
routines_before = len(r.history)
drive(r, 30, start=3.0)
check("...held for as long as typing lasts", r.state == "cover_eyes")
check("...no routine starts while covering", len(r.history) == routines_before)
r.react("typing_stop")
drive(r, 0.2, start=33.0)
check("typing stop -> releases", r.state != "cover_eyes")

nc = fresh(WITHOUT_COVER)
nc.react("typing_start")
check("no cover_eyes art -> sleep frame 0 held", nc.state == "sleep" and nc.hold_frame == 0,
      (nc.state, nc.hold_frame))

r = fresh()
r.react("wrong")
check("wrong -> angry + grump + wrong_1", r.state == "angry" and r.take_sound() == "grump"
      and r.take_phrase()[0] == "wrong_1")
drive(r, 1.5, start=3.0)
check("...then foot_tap", r.state == "foot_tap", r.state)
r.react("wrong")
check("second wrong -> wrong_2 line", r.take_phrase()[0] == "wrong_2")

r = fresh()
for _ in range(2):
    r.react("clicked")
check("click -> wave (pointing at the field) + poke", r.state == "wave" and r.take_sound() == "poke")
r.react("clicked")
check("3rd poke -> angry + grump + poked line",
      r.state == "angry" and r.take_sound() == "grump" and r.take_phrase()[0] == "poked")

r = fresh()
r.react("pointer_near")
check("pointer near -> startle + poke + line",
      r.state == "startle" and r.take_sound() == "poke" and r.take_phrase()[0] == "pointer_near")
drive(r, 0.8, start=3.0)
check("...then wave (nope)", r.state == "wave", r.state)
r.take_sound()
r.react("pointer_near")
check("pointer near is rate-limited", r.take_sound() is None)

r = fresh()
r.react("blackout")
check("blackout -> grump + blackout line", r.take_sound() == "grump" and r.take_phrase()[0] == "blackout")
check("...runs right", r.state == "walk_right" and r.facing == 1)
drive(r, 3.0, start=3.0)
check("...and leaves the stage", not r.visible and r.x > 1.0, (r.visible, round(r.x, 2)))
r.react("clicked")
check("...nothing reacts while he's gone", r.take_sound() is None)
r.react("unlock")
check("unlock -> task_finished + cheer + line",
      r.visible and r.state == "task_finished" and r.take_sound() == "cheer"
      and r.take_phrase()[0] == "unlock")
drive(r, 1.5, start=6.0)
check("...then heart_eyes", r.state == "heart_eyes", r.state)

r = fresh()
r.react("peek_return")
check("peek return -> walks back on + wake + line",
      r.x < 0 and r.state == "walk_right" and r.take_sound() == "wake"
      and r.take_phrase()[0] == "peek_return")
drive(r, 5.0, start=3.0)
check("...then check_watch", r.state == "check_watch" and abs(r.x - 0.5) < 1e-6, (r.state, r.x))

r = fresh()
drive(r, 5.0, start=3.0, idle=lambda t: 400.0)
check("5 min idle -> sleeps + sleep sound", r.sleeping and r.state == "sleep" and r.take_sound() == "sleep")
drive(r, 0.2, start=8.0, idle=lambda t: 0.0)
check("input -> wakes + wake sound", not r.sleeping and r.state == "wake" and r.take_sound() == "wake")

# --- painting --------------------------------------------------------------------------------

print("\n--- painting ---")


class Cfg(dict):
    pass


def render(widget, w, h):
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(40, 40, 40))
    widget.render(img)
    return img


eyes_img = QImage(1200, 500, QImage.Format_ARGB32_Premultiplied)
painter = G.EyesPainter()
for mood in G.MOODS:
    model = G.EyesModel(random.Random(1))
    model.set_mood(mood)
    run_eyes(model, 0.5, pointer=QPointF(1500, 900), idle_grows=False)
    eyes_img.fill(QColor(0, 0, 0, 0))
    p = QPainter(eyes_img)
    painter.paint(p, QRectF(0, 0, 1200, 500), model, scale=G.EyesPainter.fit_scale(QRectF(0, 0, 1200, 500)))
    p.end()
    painted = sum(1 for x in range(0, 1200, 10) for y in range(0, 500, 10)
                  if eyes_img.pixelColor(x, y).alpha() > 0)
    check(f"eyes paint in mood {mood}", painted > 50, painted)
sleeper = G.EyesModel(random.Random(4))
run_eyes(sleeper, 2, idle0=400.0, idle_grows=False)
p = QPainter(eyes_img)
painter.paint(p, QRectF(0, 0, 1200, 500), sleeper, scale=6)
p.end()
check("sleeping eyes + Z z z paint", sleeper.sleeping)
squeezed = G.EyesModel(random.Random(4))
run_eyes(squeezed, 0.3, typing=True, idle_grows=False)
p = QPainter(eyes_img)
painter.paint(p, QRectF(0, 0, 1200, 500), squeezed, scale=6)
p.end()
check("squeezed (typing) eyes paint", squeezed.squeezed)

W, H = 1920, 1080
big_sheet = SpriteSheet(ASSETS / "nib_sheet.png", ASSETS / "frames.json", 4)
cfg = Cfg(guard_sounds=True, guard_peek_minutes=3)
scene = G.GuardScene(big_sheet, cfg, show_eyes=True, show_nib=True, rng=random.Random(3))
scene.resize(W, H)
sounds = []
lines = []
scene.sound_requested.connect(sounds.append)
scene.phrase_shown.connect(lambda c, t: lines.append((c, t)))
t0 = 1000.0
for i in range(150):  # 5 s of frames
    scene.step(t0 + i / 30)
img = render(scene, W, H)
check("scene renders to a QImage", not img.isNull())
check("eyes actually drawn (sclera pixels present)",
      any(img.pixelColor(x, y) == G.C_SCLERA for x in range(400, 1500, 7) for y in range(40, 520, 7)))
scene.notify("typing_start")
scene.step(t0 + 5.1)
check("scene: typing -> Nib covers eyes (or fallback)",
      scene.director.state in ("cover_eyes", "sleep") and scene.eyes.squeezed)
render(scene, W, H)
scene.notify("typing_stop")
scene.notify("wrong")
scene.step(t0 + 5.2)
check("scene: wrong -> squint + grump sound", scene.eyes.mood == "squint" and "grump" in sounds)
scene.notify("wrong")
scene.step(t0 + 5.3)
check("scene: 2nd wrong -> veins", scene.eyes.mood == "veins")
render(scene, W, H)
scene.set_mode("blackout")
for i in range(30):
    scene.step(t0 + 5.4 + i / 30)
check("scene: blackout -> red eyes", scene.eyes.mood == "red")
check("scene: phrase_shown fired with the blackout line", any(c == "blackout" for c, _ in lines))
render(scene, W, H)
scene.set_mode("unlock")
scene.step(t0 + 7.0)
check("scene: unlock -> happy + cheer", scene.eyes.mood == "happy" and "cheer" in sounds)
scene.step(t0 + 7.0 + G.HAPPY_S + G.UNLOCK_FADE_S + 0.1)
check("scene: fades out after the happy beat", scene.opacity == 0.0)
scene.set_mode("peek_return")
scene.step(t0 + 9.0)
check("scene: peek return -> stare", scene.eyes.mood == "stare" and scene.opacity == 1.0)
scene.step(t0 + 9.0 + G.STARE_S + 0.1)
check("scene: stare relaxes after a few seconds", scene.eyes.mood == "calm")

# Coming back from a Peek: the scene was hidden (clock forgotten) for minutes.
scene._last_now = None
scene._last_cursor = None
scene.set_mode("peek_return")
later = time.monotonic()
scene.step(later)
scene.step(later + 1 / 30)
check("peek return after a long hide -> still staring, not sleepy",
      scene.eyes.mood == "stare" and not scene.eyes.sleepy and scene.eyes.lid_open > 0.9,
      (scene.eyes.mood, scene.eyes.sleepy, round(scene.eyes.lid_open, 2)))

# the eye hooks the scene owns: wince on click, "really?" on wrong, the football, low idle FPS
eyes_scene = G.GuardScene(big_sheet, cfg, show_eyes=True, show_nib=True, rng=random.Random(4))
eyes_scene.resize(W, H)
for i in range(45):
    eyes_scene.step(300.0 + i / 30)
left_eye = G.EyesPainter.eye_rects(eyes_scene._eyes_rect(), scale=eyes_scene._eyes_scale())[0]
click = QMouseEvent(QEvent.MouseButtonPress, QPointF(left_eye.center()), QPointF(left_eye.center()),
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
eyes_scene.mousePressEvent(click)
eyes_scene.step(301.5 + 1 / 30)
check("scene: clicking an eye -> wince (and the click is taken)",
      click.isAccepted() and eyes_scene.eyes.wincing and eyes_scene.eyes.brow_pose == "wince")
miss = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, H - 5), QPointF(5, H - 5),
                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
eyes_scene.mousePressEvent(miss)
check("scene: a click elsewhere goes to the overlay", not miss.isAccepted())
for i in range(20):
    eyes_scene.step(301.6 + i / 30)
eyes_scene.notify("wrong")
eyes_scene.step(302.4)
eyes_scene.step(302.6)
check("scene: wrong attempt -> 'really?' brow", eyes_scene.eyes.brow_pose == "really",
      eyes_scene.eyes.brow_pose)
ball_scene = G.GuardScene(big_sheet, cfg, show_eyes=True, show_nib=True, rng=random.Random(4))
ball_scene.resize(W, H)
for i in range(30):
    ball_scene.step(400.0 + i / 30)
check("no football -> no ball for the eyes", ball_scene.ball_pos() is None)
# FOOTBALL_BALL mirrors gen_sprites' FOOTBALL_KEYS by hand: check it against the sheet on disk.
ball_light = QColor(0xF5, 0xED, 0xE6)
for i, (bx, by) in enumerate(G.FOOTBALL_BALL):
    frame = big_sheet.pixmap("football", i).toImage()
    sc = big_sheet.scale
    hit = any(frame.pixelColor((bx + dx) * sc, (by + dy) * sc).rgb() == ball_light.rgb()
              for dx in range(-2, 2) for dy in range(-2, 2))
    check(f"football frame {i}: FOOTBALL_BALL sits on the drawn ball", hit, (bx, by))
ball_scene.director.start_routine("football")
ball_ys = []
for i in range(60):
    ball_scene.step(401.0 + i / 30)
    bp = ball_scene.ball_pos()
    if bp is not None:
        ball_ys.append(bp.y())
nib_box = ball_scene._current_nib_rect()
check("football: the scene reports the ball, on Nib, bouncing",
      len(ball_ys) > 50 and all(nib_box.top() <= y <= nib_box.bottom() for y in ball_ys)
      and max(ball_ys) - min(ball_ys) > 40, (len(ball_ys), round(min(ball_ys)), round(max(ball_ys))))
check("football: the big eyes watch it", ball_scene.eyes.following_ball and ball_scene.eyes.pupil_y > 0.0,
      (ball_scene.eyes.following_ball, round(ball_scene.eyes.pupil_y, 2)))
ball_scene._timer.start(G.FRAME_MS)  # never shown, so start the clock by hand to read its rate
ball_scene._last_input = 400.0
ball_scene.step(400.0 + G.SLEEPY_S + 1.0)
check("no input for a minute -> 15 FPS", ball_scene._timer.interval() == G.IDLE_FRAME_MS,
      ball_scene._timer.interval())
ball_scene.notify("input")
ball_scene.step(400.0 + G.SLEEPY_S + 1.1)
check("...and back to 30 FPS on input", ball_scene._timer.interval() == G.FRAME_MS)
ball_scene._timer.stop()

quiet = G.GuardScene(big_sheet, Cfg(guard_sounds=False), show_eyes=True, show_nib=True,
                     rng=random.Random(3))
quiet.resize(W, H)
qs = []
quiet.sound_requested.connect(qs.append)
quiet.step(1.0)
quiet.notify("wrong")
quiet.step(1.1)
check("guard_sounds off -> no sound requests", qs == [])

for show_eyes, show_nib in ((True, False), (False, True)):
    s2 = G.GuardScene(big_sheet, cfg, show_eyes=show_eyes, show_nib=show_nib, rng=random.Random(1))
    s2.resize(W, H)
    for i in range(30):
        s2.step(50.0 + i / 30)
    s2.notify("wrong")
    s2.step(51.5)
    render(s2, W, H)
    check(f"scene setting eyes={show_eyes} nib={show_nib} renders", True)

# paint cost at a realistic size: full renders (worst case - live frames only repaint dirty rects)
perf = G.GuardScene(big_sheet, cfg, show_eyes=True, show_nib=True, rng=random.Random(8))
perf.resize(W, H)
target = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
step_ms = 0.0
N = 60
for i in range(N):
    s0 = time.perf_counter()
    perf.step(200.0 + i / 30)
    step_ms += (time.perf_counter() - s0) * 1000
    target.fill(QColor(0, 0, 0, 0))
    perf.render(target)
avg_paint = perf.paint_ms_total / max(1, perf.paint_count)
avg_step = step_ms / N
print(f"  ..   average full-scene paint {avg_paint:.2f} ms, average step {avg_step:.3f} ms "
      f"({perf.paint_count} paints at {W}x{H})")
check("paint is cheap (< 25 ms per full frame, loose bound)", avg_paint < 25.0, f"{avg_paint:.2f} ms")
check("step is cheap (< 5 ms)", avg_step < 5.0, f"{avg_step:.3f} ms")

print("\n--- mini eyes and peek chip ---")
mini = G.MiniEyes(rng=random.Random(2))
mini.resize(500, 200)
mini.step(1.0)
mini.step(1.1)
mini.set_mode("blackout")
check("mini eyes go red in blackout", mini.eyes.mood == "red")
render(mini, 500, 200)
check("mini eyes render", True)

chip = G.PeekChip(Cfg(guard_peek_minutes=3))
warned = []
chip.warning.connect(lambda: warned.append(1))
chip.set_remaining(180, 180)
check("chip text m:ss", chip.text() == "3:00", chip.text())
chip.set_remaining(65.2, 180)
check("chip rounds up", chip.text() == "1:06", chip.text())
chip.set_remaining(10.0, 180)
chip.set_remaining(9.0, 180)
check("warning fires once at 0:10", warned == [1], warned)
chip.set_remaining(190, 360)
chip.set_remaining(8, 360)
check("...and re-arms after +3 min", warned == [1, 1], warned)
check("extend button label from config", chip._extend.text() == "+3 min")
ext, lck = [], []
chip.extend_requested.connect(lambda: ext.append(1))
chip.lock_requested.connect(lambda: lck.append(1))
chip._extend.click()
chip._lock.click()
check("buttons emit extend/lock", ext == [1] and lck == [1])
render(chip, chip.width(), chip.height())
check("chip renders", chip.width() > 0)

print()
if fails:
    print(f"{len(fails)} FAILED:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all guard scene checks passed")
