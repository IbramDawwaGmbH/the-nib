"""Away Guard background effects: bots, dust, the camera, the glow, the frame rate and the cost.

``BackgroundFx`` is a model first and a painter second, so nearly everything here is replayed from
a seeded ``random.Random`` against a synthetic clock and a fixed local time - minutes of bots in a
fraction of a second, the same every run. The painting checks render into plain ``QImage``\\s on
Qt's offscreen platform; the "fog" under them is made from a generated picture, never a screen
grab. The day / night / blackout previews land in the test scratch directory for eyeballing.
"""

import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QPointF, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication.instance() or QApplication([])

import guard_fx as F  # noqa: E402
import guard_overlay as O  # noqa: E402

SCRATCH = _scratch()
W, H = 1920, 1080
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def at(hour):
    return lambda: datetime(2026, 11, 3, hour, 15)


def make(seed=1, *, primary=True, hour=13, w=W, h=H):
    return F.BackgroundFx(w, h, rng=random.Random(seed), primary=primary, started=0.0,
                          local_time=at(hour), label="CAM 1" if primary else "CAM 2")


def run(fx, seconds, *, start=0.0, step=0.05, pointer=None, each=None):
    """Tick ``fx`` for ``seconds``; returns (end time, all dirty rects, full repaint count)."""
    t, rects, fulls = start, 0, 0
    while t < start + seconds:
        got, full = fx.step(t, pointer(t) if callable(pointer) else pointer)
        rects += len(got)
        fulls += int(full)
        if each is not None:
            each(t)
        t += step
    return t, rects, fulls


def synthetic_fog():
    """A colourful made-up desktop turned into fog by the overlay's own pipeline."""
    img = QImage(W, H, QImage.Format_RGB32)
    img.fill(QColor(30, 60, 110))
    p = QPainter(img)
    p.fillRect(80, 120, 900, 700, QColor(250, 250, 252))
    p.fillRect(1050, 200, 780, 600, QColor(25, 25, 30))
    p.fillRect(0, H - 48, W, 48, QColor(20, 22, 28))
    p.end()
    return O.fog_from_pixmap(img, QSize(W, H), 5)


def render(fx, base):
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    fx.paint_base(p, img.rect(), base)
    fx.paint(p, img.rect())
    p.end()
    return img


# --- bots -----------------------------------------------------------------------------------

print("\n--- worker bots: spawn rate, head count, where they walk ---")
fx = make(3)
spawned = []
real_spawn = fx.spawn_bot


def counting_spawn(now):
    bot = real_spawn(now)
    if bot is not None:
        spawned.append((now, bot))
    return bot


fx.spawn_bot = counting_spawn
peak = [0]
centre_ok = [True]
bands_ok = [True]


def watch(t):
    peak[0] = max(peak[0], len(fx.bots))
    for bot in fx.bots:
        r = bot.rect
        if r.isNull():
            continue
        if abs(r.center().x() - W / 2) < W * F.CENTRE_FADE_IN:
            if fx.centre_factor(r.center().x()) > F.CENTRE_FADE + 1e-6:
                centre_ok[0] = False
        if bot.kind == "walker" and r.top() < H * 0.85:
            bands_ok[0] = False
        if bot.kind == "flyer" and r.bottom() > H * 0.25:
            bands_ok[0] = False


MINUTES = 10
run(fx, MINUTES * 60, each=watch)
attempts = MINUTES * 60 / (sum(F.SPAWN_EVERY_S) / 2)
check("bots keep arriving (several a minute)", len(spawned) >= MINUTES * 3, len(spawned))
check("...but never more spawn attempts than the interval allows", len(spawned) <= attempts * 1.3,
      f"{len(spawned)} vs ~{attempts:.0f} attempts")
check("a few at a time on the primary", 1 <= peak[0] <= F.MAX_BOTS_PRIMARY, peak[0])
kinds = {b.kind for _, b in spawned}
cargo = {b.cargo for _, b in spawned}
check("both walkers and flyers", kinds == {"walker", "flyer"}, kinds)
check("boxes and gears get carried", {"box", "gear"} <= cargo, cargo)
check("walkers stay in the bottom band, flyers in the top band", bands_ok[0])
check("bots in the centre column are faded down", centre_ok[0])
check("centre opacity is a small share of full", fx.centre_factor(W / 2) <= 0.35, fx.centre_factor(W / 2))
check("full opacity out at the sides", fx.centre_factor(W * 0.05) > 0.99)

sec = make(3, primary=False)
sec_peak = [0]
run(sec, 300, each=lambda t: sec_peak.__setitem__(0, max(sec_peak[0], len(sec.bots))))
check("a secondary monitor is cheaper: one bot at a time", sec_peak[0] == F.MAX_BOTS_SECONDARY, sec_peak[0])
check("...and less dust", len(sec.motes) < len(make(3).motes), (len(sec.motes), len(make(3).motes)))

a, b = make(9), make(9)
run(a, 60)
run(b, 60)
check("same seed, same show", [(x.kind, x.cargo, round(x.x)) for x in a.bots]
      == [(x.kind, x.cargo, round(x.x)) for x in b.bots])

# --- dust -----------------------------------------------------------------------------------

print("\n--- dust drifts, and the pointer pushes it away ---")
calm, pushed = make(5), make(5)
target = pushed.motes[0]
pointer = QPointF(target.x + 6, target.y + 4)  # just up-left of the first mote
near = [i for i, m in enumerate(pushed.motes)
        if ((m.x - pointer.x()) ** 2 + (m.y - pointer.y()) ** 2) ** 0.5 < F.PUSH_RADIUS_BLOCKS * pushed.b * 0.8]


def dist(m):
    return ((m.x - pointer.x()) ** 2 + (m.y - pointer.y()) ** 2) ** 0.5


before = [dist(pushed.motes[i]) for i in near]
run(calm, 1.0, pointer=None)
run(pushed, 1.0, pointer=pointer)
after_calm = [dist(calm.motes[i]) for i in near]
after_push = [dist(pushed.motes[i]) for i in near]
check("motes near the pointer exist in this seed", len(near) >= 1, len(near))
check("every nearby mote ends up farther from the pointer than it would have drifted",
      all(p > c + 1 for p, c in zip(after_push, after_calm)),
      [round(p - c) for p, c in zip(after_push, after_calm)])
check("far motes are untouched", all(
    abs(calm.motes[i].x - pushed.motes[i].x) < 1e-6 for i in range(len(calm.motes))
    if i not in near and dist(calm.motes[i]) > F.PUSH_RADIUS_BLOCKS * calm.b * 3))
run(pushed, 6.0, start=1.0, pointer=None)
check("a pushed mote settles back into its drift", max(abs(m.px) + abs(m.py) for m in pushed.motes)
      < F.PUSH_MAX_BLOCKS * pushed.b * 0.05)
check("dust wraps around rather than leaving", all(0 <= m.x < W and 0 <= m.y < H for m in pushed.motes))

# --- camera ---------------------------------------------------------------------------------

print("\n--- the camera: scan line period, REC blink and clock ---")
cam = make(2)
starts, ys, prev = [], [], None
t = 0.0
while t < 30.0:
    y = cam.scan_y(t)
    if y is not None and prev is None:
        starts.append(round(t, 2))
    if y is not None and t < F.SCAN_SWEEP_S:
        ys.append(y)
    prev = y
    t += 0.01
gaps = [round(b - a, 2) for a, b in zip(starts, starts[1:])]
check("a sweep every ~6-8 s", gaps and all(6.0 <= g <= 8.0 for g in gaps), gaps)
check("the sweep only runs top to bottom", ys == sorted(ys) and ys[0] <= cam.b and ys[-1] >= H * 0.95,
      (ys[0], ys[-1]))
check("REC blinks once a second", cam.rec_on(0.1) and not cam.rec_on(0.8) and cam.rec_on(1.1))
check("REC shows the time guarded", cam.rec_text(754.0).endswith("12:34") and "REC" in cam.rec_text(0),
      cam.rec_text(754.0))
check("hours roll over", F.format_elapsed(3 * 3600 + 62) == "3:01:02", F.format_elapsed(3 * 3600 + 62))
rec = cam._rec_box()
check("REC lives in the top-left corner", rec.right() < W * 0.25 and rec.bottom() < H * 0.08, rec)
check("...clear of the top-right unlock link", rec.right() < W / 2)

# --- frame rate ------------------------------------------------------------------------------

print("\n--- frame rate: 20 FPS around people, 8 FPS when idle ---")
idle = make(4)
still = QPointF(300, 300)
idle.step(0.0, still)
check("starts at 20 FPS", idle.frame_ms(0.0) == F.FRAME_MS == 50)
run(idle, 59.0, start=0.05, pointer=still)
check("still 20 FPS after 59 s", idle.frame_ms(59.0) == F.FRAME_MS)
run(idle, 3.0, start=59.05, pointer=still)
check("8 FPS after a minute without input", idle.frame_ms(62.0) == F.IDLE_FRAME_MS == 125)
idle.step(62.1, QPointF(320, 300))
check("pointer movement brings it back to 20 FPS", idle.frame_ms(62.1) == F.FRAME_MS)
run(idle, 61.0, start=62.2, pointer=QPointF(320, 300))
idle.note_input(123.5)
check("a key or click does too", idle.frame_ms(123.5) == F.FRAME_MS)

_, n_rects, fulls = run(make(6), 60.0)
check("full repaints stay rare (glow steps only, <= ~1 a second)", fulls <= 70, fulls)
check("dirty rects are reported in small pieces", n_rects > 0)

# --- blackout ---------------------------------------------------------------------------------

print("\n--- blackout: bots and dust stop, almost everything goes black ---")
bk = make(8)
run(bk, 20.0)
check("there were bots before the blackout", len(bk.bots) > 0, len(bk.bots))
bk.set_mode("blackout")
rects, _ = bk.step(20.0, QPointF(500, 500))
check("blackout clears the bots", bk.bots == [])
check("...repaints where they were", len(rects) > 0)
check("...and no new ones arrive", bk.spawn_bot(21.0) is None)
pos = [(m.x, m.y) for m in bk.motes]
run(bk, 5.0, start=20.05, pointer=QPointF(500, 500))
check("dust stops moving", pos == [(m.x, m.y) for m in bk.motes])
check("no scan line in blackout", bk.scan_rect.isNull())
check("blackout ticks at the idle rate", bk.frame_ms(25.0) == F.IDLE_FRAME_MS)
img = render(bk, None)
rec_box = bk._rec_box()
bright = coloured = total = 0
for y in range(0, H, 6):
    for x in range(0, W, 6):
        c = img.pixelColor(x, y)
        total += 1
        if max(c.red(), c.green(), c.blue()) > 40:
            bright += 1
        if not rec_box.contains(x, y) and (c.green() > 12 or c.blue() > 12):
            coloured += 1
check("blackout stays overwhelmingly black", bright / total < 0.005, f"{bright}/{total}")
check("...outside the REC only a red pulse", coloured == 0, coloured)
img.save(str(SCRATCH / "guard_fx_blackout.png"))

# --- time of day ------------------------------------------------------------------------------

print("\n--- time of day: warm evenings, stars at night ---")
fog = synthetic_fog()
day, night, evening = make(12, hour=13), make(12, hour=23), make(12, hour=19)
for f in (day, night, evening):
    run(f, 12.0)
check("13:15 is day", day.part == "day" and not day.is_night)
check("19:15 is evening", evening.part == "evening")
check("23:15 is night", night.is_night)
tint = evening._tint()
check("evening tint is warm", tint is not None and tint.red() > tint.blue() + 100, tint and tint.name())
check("stars only at night", len(night.stars) > 0 and all(s.level >= 0 for s in night.stars)
      and all(s.level == -1 for s in day.stars))
img_day, img_night = render(day, fog), render(night, fog)
img_evening = render(evening, fog)


def mean(img, rect):
    r = g = b = n = 0
    for y in range(rect.top(), rect.bottom(), 9):
        for x in range(rect.left(), rect.right(), 9):
            c = img.pixelColor(x, y)
            r, g, b, n = r + c.red(), g + c.green(), b + c.blue(), n + 1
    return r / n, g / n, b / n


mid = QRect(W // 3, H // 3, W // 3, H // 3)
d, e, n = mean(img_day, mid), mean(img_evening, mid), mean(img_night, mid)
check("evening is warmer than the day", e[0] - e[2] > d[0] - d[2], (round(e[0] - e[2]), round(d[0] - d[2])))
check("night is darker than the day", sum(n) < sum(d) - 30, (round(sum(n)), round(sum(d))))
img_day.save(str(SCRATCH / "guard_fx_day.png"))
img_night.save(str(SCRATCH / "guard_fx_night.png"))
img_evening.save(str(SCRATCH / "guard_fx_evening.png"))
print(f"  previews in {SCRATCH}")

# --- cost ------------------------------------------------------------------------------------

print("\n--- cost at 1080p ---")
cost = make(13, hour=23)
target_img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
spent, calls, t = 0.0, 0, 0.0
while t < 30.0:
    rects, full = cost.step(t, QPointF(900 + 300 * (t % 3), 500))
    p = QPainter(target_img)
    started = time.perf_counter()
    if full:
        cost.paint_base(p, target_img.rect(), fog)
        cost.paint(p, target_img.rect())
    else:
        for r in rects:
            p.setClipRect(r)
            cost.paint_base(p, r, fog)
            cost.paint(p, r)
    spent += time.perf_counter() - started
    calls += 1
    p.end()
    t += 0.05
per_tick = spent / calls * 1000
check(f"fx painting averages {per_tick:.3f} ms a tick at 20 FPS ({per_tick * 2:.1f}% of a core)",
      per_tick < 3.0, per_tick)

# --- the overlay hook and the setting --------------------------------------------------------

print("\n--- guard_background_fx: on paints, off paints nothing and runs no timer ---")
screen = QGuiApplication.primaryScreen()
off = O.GuardOverlay(screen, primary=False, scene=None, config={"guard_background_fx": False})
on = O.GuardOverlay(screen, primary=False, scene=None, config={"guard_background_fx": True})
check("off: no effects model", off.fx is None)
check("on (and by default): a model", on.fx is not None
      and O.GuardOverlay(screen, primary=False, scene=None, config={}).fx is not None)
for ov in (off, on):
    ov.set_fog(O._haze_pixmap(ov.size(), 5))
off_img = off.grab().toImage()
haze = QColor(*O._HAZE_RGB).rgb()
check("off: the overlay is plain fog, pixel for pixel",
      all(off_img.pixel(x, y) == haze for y in range(0, off_img.height(), 23)
          for x in range(0, off_img.width(), 23)))
on.fx.step(time.monotonic(), None)
on_img = on.grab().toImage()
check("on: something is painted over the fog", any(
    on_img.pixel(x, y) != haze for y in range(0, on_img.height(), 23) for x in range(0, on_img.width(), 23)))
off.show()
on.show()
app.processEvents()
check("off: no effects timer while shown", not off._fx_timer.isActive())
check("on: the effects timer runs while shown", on._fx_timer.isActive())
on.hide()
app.processEvents()
check("hidden (Peek): the timer stops", not on._fx_timer.isActive())
on.set_mode("blackout")
check("overlay mode reaches the effects", on.fx.mode == "blackout")
for ov in (off, on):
    ov.allow_close()
    ov.close()

print("\n--- every monitor's REC counts from the same start, and Peek does not reset it ---")
oset = O.OverlaySet({"guard_scene": "eyes"}, scene_factory=lambda: QLabel("scene"), mini_factory=lambda: None)
oset.show("fog", primary_screen=screen)
app.processEvents()
first = {ov.fx.started for ov in oset.overlays}
check("one shared start", len(first) == 1, first)
check("the primary is CAM 1", oset.primary.fx.label == "CAM 1")
oset.hide()
oset.show("fog")
app.processEvents()
check("Peek and back keeps the clock", {ov.fx.started for ov in oset.overlays} == first)
oset.close()
app.processEvents()
check("a fresh guard starts a fresh clock", oset._started is None)

print()
if fails:
    print(f"{len(fails)} FAILED:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all guard fx checks passed")
