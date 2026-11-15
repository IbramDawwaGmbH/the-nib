"""Christmas snow on screen: the piles on your windows, and the snow you knock off them.

:mod:`snow` is the model; this is the view, and the only part of the snow that knows about Qt or
Win32. It owns two kinds of window (decided by the WP0 measurements in
``plans/CHRISTMAS-SNOW-PLAN.md`` 1.6), every one of them translucent, frameless, tool-type,
unfocusable, excluded from capture and **created** click-through:

*   **Cap strips**: one small window per visible pile, sized to :meth:`snow.Cap.extent` (fixed for a
    window's rectangle, so a pile that grows never resizes its strip), from a pool of at most
    ``snow.MAX_CAPS``. Each paints one pre-rendered pixmap of its pile, clipped to what the windows
    in front leave showing (:func:`snow.clip`). A strip repaints only when its pile changes shape
    or its clip changes, and moves only when the 2 Hz stack poll sees its window move: at rest it
    costs nothing at all.
*   **Flight layers**: one full-monitor window per monitor, created only when snow is knocked or
    thrown into the air there, repainted at 30 FPS in the model's grouped dirty boxes (never the
    whole window: that flush is 4-9 ms), and destroyed ``FLIGHT_LINGER_MS`` after the last chunk
    lands - a visible full-screen layered window can cost a game its direct path to the display.

Why the windows are **not** topmost, unlike every other overlay in Nib (measured here, with
``EnumWindows``): a window shown with ``WindowStaysOnTopHint`` lands at the very top of the topmost
band, over Nib, his bubble, the wheel and the focus timer, and keeping it under them would mean
re-raising all of those after every show. A non-topmost tool window lands at the top of the
*normal* band instead - over every application window, the foreground one included, and under
everything topmost by construction, Nib's windows, the taskbar, the Start menu and context menus
among them. The cost is drift: clicking a window brings it above the strips, so a pile on a window
behind it disappears under it, and once that window is clicked to the front its pile stays under
the window that used to be in front. The stack poll sees the front window change and raises the
strips back to the top of the normal band (``QWidget.raise_``, our own windows only; nothing of
anyone else's is ever restacked). Anything in front of a pile is then hidden by the clip, as it
is anyway: the clip comes from the same stack pass. The flight layer is non-topmost too, so
falling snow passes behind Nib rather than over him.

Nib's own ordinary windows (Settings, a message box) share that band, and ``list_window_stack``
leaves this process out, so the overlay puts them at the front of the stack it hands the model
itself: they hide the piles under them like any window in front (:meth:`SnowOverlay._own_windows`).

Click-through, twice over (plan section 0 rule 1): a painted pixel of a translucent window takes
the click under it, and soft snow has nothing but partly transparent pixels. Every window carries
``Qt.WindowTransparentForInput`` from its creation, which makes Qt set ``WS_EX_TRANSPARENT`` and
keep it across native re-creation, and :func:`win_integration.set_click_through` plus the capture
exclusion are re-applied on every show and on ``QEvent.WinIdChange`` (the affinity does not
survive a re-creation). ``setWindowFlags`` is never called on a shown snow window.

The look is realistic, and cheap because nothing is drawn live: :func:`render_cap` draws a pile
once per shape (``Cap.version``) - a soft rounded mound, bright on top and cool blue-grey
underneath, a curl over each corner of the window, a few sparkles, translucent icicles with a
highlight streak - and :func:`render_lump` and :func:`render_shard` draw what flies, once per size.
All softness is baked into those pixmaps; there is no blur, live or otherwise.

Ticks, all stopped while the snow is not showing:

*   ``STACK_POLL_MS`` (2 Hz): one ``list_window_stack`` pass and :meth:`snow.SnowField.sync`, the
    growth step, and the check whether the pointer is near a pile.
*   ``POINTER_POLL_MS`` (~60 Hz), only while the pointer is near a pile and the snow is not
    frozen: the cursor sampled for the brush and flick gestures.
*   ``1000 / snow.FPS`` while anything is airborne, stopped the moment nothing is.
"""

from __future__ import annotations

import logging
import math
import random
import time
from collections import deque
from collections.abc import Callable, Iterable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
)
from PySide6.QtWidgets import QApplication, QWidget

import snow
import win_integration as win

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]

# --- timing --------------------------------------------------------------------------------------

#: The window stack poll, the growth step and the near-a-pile check (plan 1.2: ``list_window_stack``
#: measured 0.6-2.2 ms median, so twice a second and never per frame).
STACK_POLL_MS = 500

#: The cursor while it is near a pile: fast enough that a swipe at a few px/ms gives the gesture
#: detector samples a stroke apart (``snow.VELOCITY_WINDOW_MS`` is 16).
POINTER_POLL_MS = 16

#: How far from a pile the pointer may be and still be sampled quickly, at 96 DPI. Generous, so a
#: swipe that starts some way off is already being watched when it arrives; the 2 Hz check is what
#: notices the pointer coming.
NEAR_MARGIN_PX = 120.0

#: How long the pointer may be away from every pile before the fast sampling stops again.
NEAR_LINGER_MS = 1000

#: How long a flight layer outlives the last chunk on it (plan 1.2: "about 2 s").
FLIGHT_LINGER_MS = 2000

#: How many landings are kept for Nib (WP5) if nobody collects them.
LANDINGS_KEPT = 64

#: While snow flies, a pile is re-rendered at most this often, however often it changes (every
#: frame, during a brush). A render is measured at ~9 ms for a 740 px pile and ~19-25 ms for the
#: widest, against a 33 ms frame; the piles' own changes are steps you never see as motion, the
#: chunks in the air are what move.
RENDER_GAP_MS = 100

# --- the look -------------------------------------------------------------------------------------
#
# The palette is the ``snowcap`` costume's (tools/gen_sprites.py): its white and its silver shade,
# taken towards a cool sky blue for the parts of a pile that face away from the light.

SNOW_WHITE = QColor(255, 255, 255)          # the costume's white: the crest, in the light
SNOW_FACE = QColor(218, 227, 240)           # the front of a pile where it meets the window
SNOW_SHADE = QColor(212, 222, 237)          # the costume's silver, cooled: the top of the lip
SNOW_COOL = QColor(184, 200, 224)           # the underside, in its own shadow
SNOW_DEEP = QColor(146, 168, 204)           # the darkest crease under an overhang
SNOW_RELIEF = QColor(118, 146, 190)         # a lump's side that faces away from the light
ICE_EDGE = QColor(170, 198, 232)
ICE_BODY = QColor(214, 230, 248)

#: How far the snow curls down over each top corner of the window, at full height (96-DPI px).
CURL_PX = 9.0

#: The faint shadow a pile casts on the window under it, and how far down it reaches.
CONTACT_SHADOW_PX = 6.0
CONTACT_SHADOW_ALPHA = 60

#: The drawn surface is the model's heights with a gentle seeded lumpiness on top, as a fraction
#: of the local depth: a pile reads as soft snow rather than icing piped along a line. Drawing
#: only - the model's heights are what brushing and landing go by, and they differ by a px or two.
LUMPINESS = ((0.1, 83.0), (0.07, 37.0), (0.03, 14.0))   # (amplitude, wavelength at 96 DPI)

#: Sparkles: about one per this many px of pile, only where it is at least ``SPARKLE_MIN_PX`` deep.
SPARKLE_EVERY_PX = 18.0
SPARKLE_MIN_PX = 6.0

#: Soft lit mounds inside the body, one per this many px.
MOUND_EVERY_PX = 30.0


def _seeded(hwnd: int, salt: int) -> random.Random:
    """The pile's own look, seeded from its window like :class:`snow.Cap`'s shape, but never from
    the cap's ``rng``: that one drives growth, and a repaint must not change how a pile grows."""
    return random.Random((int(hwnd) * 1_000_003) ^ salt)


def _smooth(values: list[float], passes: int = 2) -> list[float]:
    """A 1-2-1 smoothing of a height profile, for drawing only: a brushed edge is a step in the
    model, and a step drawn as one reads as a cut, not as snow."""
    out = list(values)
    for _ in range(passes):
        if len(out) < 3:
            return out
        out = [out[0]] + [(out[i - 1] + 2 * out[i] + out[i + 1]) / 4.0
                          for i in range(1, len(out) - 1)] + [out[-1]]
    return out


def _curve(path: QPainterPath, points: list[tuple[float, float]], start: bool) -> None:
    """Append a smooth curve through ``points``: quadratic segments between the midpoints, so
    every column top is a control point and the curve has no corners. Plain floats, not
    ``QPointF``s: a pile is a few hundred points, and the wrappers were a third of the render."""
    if not points:
        return
    x, y = points[0]
    if start:
        path.moveTo(x, y)
    else:
        path.lineTo(x, y)
    for (ax, ay), (bx, by) in zip(points[1:-1], points[2:]):
        path.quadTo(ax, ay, (ax + bx) / 2.0, (ay + by) / 2.0)
    if len(points) > 1:
        path.lineTo(*points[-1])


def _underside(cap: snow.Cap, i: int, h: float) -> float:
    """How far below the window's top edge column ``i``'s snow reaches: the lip inside the window,
    and outside it the curl - the overhang draping down over the corner and back up to its tip.
    Both scale with how much snow is there, so a thin layer does not hang."""
    s = cap.scale
    left, _top, right, _bottom = cap.rect
    cx = cap.column_center(i)
    if left <= cx < right:
        return cap.lip(i)
    over = cap.overhang[0] if cx < left else cap.overhang[1]
    d = (left - cx) if cx < left else (cx - right)
    t = min(1.0, max(0.0, d / max(1.0, over)))
    depth = min(CURL_PX * s, 0.45 * h)
    return depth * math.sqrt(max(0.0, 1.0 - t * t)) * (1.0 - 0.45 * t)


def _shape(cap: snow.Cap) -> tuple[list[float], list[float]]:
    """The drawn outline of a pile, per model column: how far its surface stands above the
    window's top edge, and how far its underside hangs below it."""
    s = cap.scale
    rng = _seeded(cap.hwnd, 0x1A4B)
    waves = [(a, lam * s, rng.uniform(0.0, math.tau)) for a, lam in LUMPINESS]
    tallest = snow.CAP_MAX_PX * s + 1.0
    ups, downs = [], []
    # Smoothed hard (about 6 px either way), so a brushed span ends in a slope, not a cliff.
    for i, h in enumerate(_smooth([max(0.0, v) for v in cap.heights], passes=6)):
        x = cap.column_center(i)
        bump = sum(a * math.sin(math.tau * x / lam + ph) for a, lam, ph in waves)
        ups.append(min(tallest, h * (1.0 + bump)))
        downs.append(_underside(cap, i, h))
    return ups, downs


def _shading_strip(stops: Iterable[tuple[float, QColor]], n: int = 128) -> QImage:
    """A 1-px-wide vertical gradient, stretched over each column so the shading follows the
    column's own depth (a thin layer is not grey just because a full pile would be there)."""
    img = QImage(1, n, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 0, n)
    for at, color in stops:
        g.setColorAt(at, color)
    p.fillRect(0, 0, 1, n, g)
    p.end()
    return img


_STRIPS: dict[str, QImage] = {}


def _strips() -> dict[str, QImage]:
    if not _STRIPS:
        # Above the window's edge: bright where the sky lights it, greying a little down the front.
        _STRIPS["face"] = _shading_strip((
            (0.0, SNOW_WHITE), (0.3, QColor(252, 253, 255)),
            (0.7, QColor(238, 243, 250)), (1.0, SNOW_FACE)))
        # Below it, the lip and the curl: in their own shadow, cool blue-grey, darkest underneath.
        _STRIPS["under"] = _shading_strip(((0.0, SNOW_SHADE), (0.55, SNOW_COOL),
                                           (1.0, SNOW_DEEP)))
    return _STRIPS


def render_cap(cap: snow.Cap) -> QImage:
    """One pile, drawn once for its shape, the size of :meth:`snow.Cap.extent` (its top-left is
    the extent's). Premultiplied ARGB with soft, anti-aliased edges; the view blits it.

    Composed rather than stroked or clipped (a wide pen along a pile, or a path clip, costs a few
    ms each): the shading is laid down column by column on a layer, the lights go on top of it,
    and the anti-aliased outline cuts the whole layer once (``DestinationIn``)."""
    left, top, right, bottom = cap.extent()
    w, h = max(1, right - left), max(1, bottom - top)
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    if cap.mass <= 0.0:
        return img
    s = cap.scale
    line = cap.top - top                       # the window's top edge, in pixmap y
    ups, downs = _shape(cap)
    xs = [cap.column_center(i) - left for i in range(cap.n)]
    x_l, x_r = cap.x0 - left, cap.x1 - left
    # The outline through every other column: 6 px apart at 96 DPI is still far finer than any
    # lump, and every fill of the outline costs in proportion to its segments.
    keep = [i for i in range(cap.n) if i % 2 == 0 or i == cap.n - 1]
    top_pts = [(x_l, line)] + [(xs[i], line - ups[i]) for i in keep] + [(x_r, line)]
    bot_pts = [(x_l, line)] + [(xs[i], line + downs[i]) for i in keep] + [(x_r, line)]
    body = QPainterPath()
    _curve(body, top_pts, True)
    _curve(body, bot_pts[::-1], False)
    body.closeSubpath()

    # The body's shading, two columns (6 px at 96 DPI) to a pixel on a narrow image that is then
    # stretched over the pile with smooth filtering: the shading changes far more slowly than
    # that, the stretch blends neighbouring slices instead of leaving 6 px bands, and this loop is
    # most of the render's Python. The outline's cut gives the edge its detail.
    strips = _strips()
    col = cap.col
    n = cap.n
    slices = (n + 1) // 2
    shading = QImage(slices, h, QImage.Format_ARGB32_Premultiplied)
    shading.fill(Qt.transparent)
    p = QPainter(shading)
    # Relief: light comes from the upper left, so a slope that faces right is in a little shade
    # and one that faces left catches a little more light, from the surface down about half the
    # depth. This is what turns a white band into lumps of snow.
    slopes = _smooth([(ups[min(n - 1, i + 1)] - ups[max(0, i - 1)]) / (2.0 * col)
                      for i in range(n)], passes=3)   # + rises to the right: faces left
    lit, shaded = SNOW_WHITE, SNOW_RELIEF
    for k in range(slices):
        i, j = 2 * k, min(n, 2 * k + 2)
        # A slice reaches past its neighbours' tops, so the curve between two slices is always
        # inside some slice's shading; the outline trims it.
        reach = max(ups[max(0, i - 1):j + 1]) + 1.5
        hang = max(downs[max(0, i - 1):j + 1]) + 1.5
        if reach <= 1.6 and hang <= 1.6:
            continue
        p.drawImage(QRectF(k, line - reach, 1.0, reach), strips["face"])
        p.drawImage(QRectF(k, line, 1.0, hang), strips["under"])
        u = sum(ups[i:j]) / (j - i)
        slope = sum(slopes[i:j]) / (j - i)
        alpha = min(70.0, -slope * 150.0) if slope < 0 else min(80.0, slope * 170.0)
        if u < 3.0 * s or alpha < 3.0:
            continue
        depth = min(u, 14.0 * s) * 0.8
        tint = QColor(shaded if slope < 0 else lit)
        g = QLinearGradient(0, line - u, 0, line - u + depth)
        tint.setAlpha(int(alpha))
        g.setColorAt(0.0, tint)
        tint.setAlpha(0)
        g.setColorAt(1.0, tint)
        p.fillRect(QRectF(k, line - u - 1.0, 1.0, depth + 1.0), g)
    p.end()

    # The body, on its own layer: the shading, then the lights, then the outline's cut.
    layer = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    layer.fill(Qt.transparent)
    p = QPainter(layer)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QRectF(cap.x0 - left, 0, slices * 2 * col, h), shading)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    # Soft lit mounds in the upper half, so the surface has some relief.
    rng = _seeded(cap.hwnd, 0x5EED)
    for _ in range(max(1, int((x_r - x_l) / (MOUND_EVERY_PX * s)))):
        i = rng.randrange(cap.n)
        r = rng.uniform(5.0, 11.0) * s
        cx, cy = xs[i] + rng.uniform(-0.5, 0.5) * col, line - ups[i] * rng.uniform(0.45, 0.75)
        if ups[i] < 6.0 * s:
            continue
        g = QRadialGradient(QPointF(cx - 0.3 * r, cy - 0.3 * r), r)
        g.setColorAt(0.0, QColor(255, 255, 255, 150))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), r * 1.3, r)
    # Sparkles near the surface, a few with a faint glint; on the layer, so the cut trims them.
    rng = _seeded(cap.hwnd, 0x5A4C)
    for _ in range(max(1, int((x_r - x_l) / (SPARKLE_EVERY_PX * s)))):
        i = rng.randrange(cap.n)
        # Not right at the crest: a white point on white is not there at all. Lower down the
        # face, where the snow is a shade off white, a sparkle shows.
        depth, u, big, warm = ups[i], rng.uniform(0.3, 0.85), rng.random() < 0.25, rng.random()
        cx = xs[i] + rng.uniform(-0.5, 0.5) * col
        if depth < SPARKLE_MIN_PX * s:
            continue
        cy = line - depth * (1.0 - u)
        r = (1.9 if big else 1.1) * s
        g = QRadialGradient(QPointF(cx, cy), r)
        g.setColorAt(0.0, QColor(255, 255, 255, 255))
        g.setColorAt(0.45, QColor(255, 250, 238, 190) if warm < 0.4 else QColor(225, 238, 255, 190))
        g.setColorAt(1.0, QColor(225, 238, 255, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), r, r)
        if big:
            p.setPen(QPen(QColor(255, 255, 255, 130), 0.5 * s))
            p.drawLine(QPointF(cx - 2.6 * r, cy), QPointF(cx + 2.6 * r, cy))
            p.drawLine(QPointF(cx, cy - 2.0 * r), QPointF(cx, cy + 2.0 * r))
            p.setPen(Qt.NoPen)
    # The cut: a mask of the outline over the whole layer. Filling the outline itself with
    # ``DestinationIn`` would leave every pixel outside it untouched, column tops and all.
    mask = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    mask.fill(Qt.transparent)
    m = QPainter(mask)
    m.setRenderHint(QPainter.Antialiasing)
    m.fillPath(body, QColor(0, 0, 0, 255))
    m.end()
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawImage(0, 0, mask)
    p.end()

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    # 1. The shadow the pile casts on the window under it, inside the window only.
    wl, wr = cap.rect[0] - left, cap.rect[2] - left
    shade = QPainterPath()
    lo = [(x, y + CONTACT_SHADOW_PX * s * min(1.0, ups[i] / (10.0 * s)))
          for (x, y), i in zip(bot_pts[1:-1], keep)]
    _curve(shade, bot_pts, True)
    _curve(shade, [bot_pts[-1]] + lo[::-1] + [bot_pts[0]], False)
    shade.closeSubpath()
    reach = (snow.LIP_PX + CONTACT_SHADOW_PX) * s
    g = QLinearGradient(0, line, 0, line + reach)
    g.setColorAt(0.0, QColor(16, 28, 52, CONTACT_SHADOW_ALPHA))
    g.setColorAt(1.0, QColor(16, 28, 52, 0))
    p.save()
    p.setClipRect(QRectF(wl, line, max(0.0, wr - wl), reach + 2.0))
    p.fillPath(shade, g)
    p.restore()
    # 2. Icicles, before the body so their roots disappear into it.
    for ic in cap.icicles:
        if ic.length > 0.5:
            _draw_icicle(p, cap, ic, (left, top), downs)
    # 3. A soft cool edge just above the outline (the outline filled once more, nudged up, under
    #    the body), so a pile still reads against something white behind it.
    p.fillPath(body.translated(0.0, -0.9 * s), QColor(92, 116, 156, 40))
    p.drawImage(0, 0, layer)
    p.end()
    return img


def _draw_icicle(p: QPainter, cap: snow.Cap, ic: snow.Icicle, origin: tuple[int, int],
                 downs: list[float]) -> None:
    """A translucent tapered icicle with a highlight streak and a bright drop at the tip."""
    s = cap.scale
    ox, oy = origin
    cx = ic.x - ox
    root = cap.top - oy + downs[ic.column] * 0.5 - 1.5 * s
    tip = cap.top - oy + max(ic.length, downs[ic.column] + 2.0 * s)
    if tip <= root + 2.0:
        return
    half = ic.width / 2.0
    rng = _seeded(cap.hwnd, int(ic.x * 7) & 0xFFFF)
    lean = rng.uniform(-0.12, 0.12) * ic.width
    length = tip - root
    shape = QPainterPath()
    shape.moveTo(cx - half, root)
    shape.cubicTo(QPointF(cx - half * 0.9, root + length * 0.35),
                  QPointF(cx - half * 0.3 + lean, root + length * 0.8),
                  QPointF(cx + lean, tip))
    shape.cubicTo(QPointF(cx + half * 0.3 + lean, root + length * 0.8),
                  QPointF(cx + half * 0.95, root + length * 0.3),
                  QPointF(cx + half, root))
    shape.closeSubpath()
    across = QLinearGradient(cx - half, 0, cx + half, 0)
    across.setColorAt(0.0, QColor(ICE_EDGE.red(), ICE_EDGE.green(), ICE_EDGE.blue(), 215))
    across.setColorAt(0.28, QColor(248, 251, 255, 230))
    across.setColorAt(0.5, QColor(ICE_BODY.red(), ICE_BODY.green(), ICE_BODY.blue(), 150))
    across.setColorAt(1.0, QColor(128, 160, 206, 200))
    p.fillPath(shape, across)
    p.setPen(QPen(QColor(255, 255, 255, 220), max(0.7, 0.8 * s), Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(cx - half * 0.4, root + 1.5 * s),
               QPointF(cx - half * 0.12 + lean * 0.7, root + length * 0.7))
    p.setPen(Qt.NoPen)
    drop = QRadialGradient(QPointF(cx + lean, tip - 0.9 * s), 1.5 * s)
    drop.setColorAt(0.0, QColor(255, 255, 255, 235))
    drop.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(drop)
    p.drawEllipse(QPointF(cx + lean, tip - 0.9 * s), 1.5 * s, 1.5 * s)
    p.setBrush(Qt.NoBrush)


def render_lump(d: int, seed: int = 0) -> QPixmap:
    """A soft lump of snow ``d`` px across: three overlapping rounded blobs lit from the top left,
    so a flying chunk is not a perfect disc."""
    size = d + 2
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    rng = random.Random(seed * 131 + d)
    c = size / 2.0
    blobs = [(c, c, d * 0.42)] + [(c + rng.uniform(-0.18, 0.18) * d,
                                   c + rng.uniform(-0.15, 0.2) * d,
                                   d * rng.uniform(0.24, 0.32)) for _ in range(2)]
    for bx, by, r in blobs:
        g = QRadialGradient(QPointF(bx - 0.35 * r, by - 0.4 * r), r * 1.35)
        g.setColorAt(0.0, QColor(255, 255, 255, 255))
        g.setColorAt(0.45, QColor(244, 248, 253, 250))
        g.setColorAt(0.8, QColor(206, 220, 238, 235))
        g.setColorAt(1.0, QColor(176, 196, 222, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(bx, by), r, r)
    if d >= 10:
        g = QRadialGradient(QPointF(c - 0.15 * d, c - 0.2 * d), 0.09 * d)
        g.setColorAt(0.0, QColor(255, 255, 255, 255))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(c - 0.15 * d, c - 0.2 * d), 0.09 * d, 0.09 * d)
    p.end()
    return QPixmap.fromImage(img)


def render_shard(d: int) -> QPixmap:
    """A broken-off icicle, ``d`` px long: a translucent sliver of ice."""
    w = max(3, int(d * 0.35))
    img = QImage(w + 2, d + 2, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    path = QPainterPath()
    cx = (w + 2) / 2.0
    path.moveTo(cx - w / 2.0, 1)
    path.lineTo(cx + w / 2.0, 1)
    path.quadTo(QPointF(cx + w * 0.3, d * 0.6), QPointF(cx, d + 1))
    path.quadTo(QPointF(cx - w * 0.3, d * 0.6), QPointF(cx - w / 2.0, 1))
    g = QLinearGradient(cx - w / 2.0, 0, cx + w / 2.0, 0)
    g.setColorAt(0.0, QColor(ICE_EDGE.red(), ICE_EDGE.green(), ICE_EDGE.blue(), 200))
    g.setColorAt(0.35, QColor(255, 255, 255, 230))
    g.setColorAt(1.0, QColor(150, 180, 220, 170))
    p.fillPath(path, g)
    p.end()
    return QPixmap.fromImage(img)


# --- the windows ---------------------------------------------------------------------------------


class _SnowWindow(QWidget):
    """What every snow window is: click-through from its creation, never focused or activated,
    out of the taskbar and Alt+Tab (``Tool``), not topmost (see the module docstring), and hidden
    from screenshots and screen shares."""

    FLAGS = (Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
             | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint)

    def __init__(self) -> None:
        super().__init__(None, self.FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)

    def hwnd(self) -> int:
        return int(self.winId())

    def protect(self) -> None:
        """Click-through and capture exclusion, belt and braces: the Qt flag already makes the
        window ``WS_EX_TRANSPARENT``, but a bit set only by hand would be lost when Qt re-creates
        the HWND, and the capture affinity is lost then whatever we do (WP0, plan 1.6)."""
        # Imported here rather than at the top: guard_overlay is the guard's, and the app only
        # loads it once a guard starts. The snow needs one small function of it, once it shows.
        from guard_overlay import WDA_EXCLUDEFROMCAPTURE, set_display_affinity

        hwnd = self.hwnd()
        win.set_click_through(hwnd, True)
        set_display_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def showEvent(self, event) -> None:  # noqa: N802
        self.protect()
        super().showEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.WinIdChange and self.testAttribute(Qt.WA_WState_Created):
            self.protect()
        return super().event(event)


class CapStrip(_SnowWindow):
    """One pile's window: its pre-rendered pixmap, drawn only where nothing hides it."""

    def __init__(self) -> None:
        super().__init__()
        self.cap_hwnd = 0
        self.pixmap: QPixmap | None = None
        self.region = QRegion()
        self.version = -1

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.pixmap is None or self.region.isEmpty():
            return
        p = QPainter(self)
        p.setClipRegion(self.region & QRegion(event.rect()))
        p.drawPixmap(0, 0, self.pixmap)
        p.end()


class FlightLayer(_SnowWindow):
    """One monitor's airborne snow, for as long as there is any. Painted only in the boxes the
    model reports; after its first paint it never asks for the whole window."""

    def __init__(self, index: int, rect: Rect, chunks: Callable[[], Iterable[snow.Chunk]],
                 sprite: Callable[[snow.Chunk], QPixmap]) -> None:
        super().__init__()
        self.index = index
        self.origin = (rect[0], rect[1])
        self._chunks = chunks
        self._sprite = sprite
        self.setGeometry(QRect(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]))

    def paintEvent(self, event) -> None:  # noqa: N802
        area = event.rect()
        ox, oy = self.origin
        p = QPainter(self)
        for c in self._chunks():
            pm = self._sprite(c)
            x = int(round(c.x - pm.width() / 2.0)) - ox
            y = int(round(c.y - pm.height() / 2.0)) - oy
            if area.intersects(QRect(x, y, pm.width(), pm.height())):
                p.drawPixmap(x, y, pm)
        p.end()


# --- the controller ------------------------------------------------------------------------------


class Monitor:
    """What the overlay needs of a monitor: its rectangle, where falling snow stops (the work
    area's bottom, so it lands on the taskbar), its DPI scale, and the ``win_integration``
    record the fullscreen test asks about."""

    __slots__ = ("rect", "floor", "scale", "info")

    def __init__(self, rect: Rect, floor: float, scale: float = 1.0, info=None) -> None:
        self.rect = tuple(int(v) for v in rect)
        self.floor = float(floor)
        self.scale = float(scale)
        self.info = info

    def key(self) -> tuple:
        return (self.rect, self.floor, self.scale)


def desktop_monitors() -> list[Monitor]:
    """The real monitors, from ``win_integration``."""
    out = []
    for m in win.list_monitors():
        scale = win.get_dpi_for_monitor(m) / float(win.DEFAULT_DPI)
        out.append(Monitor(m.rect, m.work_rect[3], scale, m))
    return out


def foreground_fullscreen(monitor: Monitor) -> bool:
    """``win_integration.is_fullscreen_on_monitor``: the foreground window covers it. The stack
    pass has its own test for any other window that does (``StackSync.fullscreen``)."""
    return monitor.info is not None and win.is_fullscreen_on_monitor(monitor.info)


class SnowOverlay(QObject):
    """The snow on screen: one :class:`snow.SnowField`, its strips and its flight layers, and the
    timers that drive them. ``main`` decides *whether* there is snow and in what state
    (:meth:`set_state`); this decides everything else.

    Every outside source is injectable, for ``tests/smoke_snow_overlay.py``: the window stack,
    the monitors, the fullscreen test, the cursor and the clock."""

    #: Emitted once, the first time a pile is actually on screen (for the one-time toast).
    appeared = Signal()

    def __init__(self, *, stack: Callable[[], list | None] = win.list_window_stack,
                 monitors: Callable[[], list[Monitor]] = desktop_monitors,
                 fullscreen: Callable[[Monitor], bool] = foreground_fullscreen,
                 cursor: Callable[[], tuple[int, int]] = win.get_cursor_pos,
                 clock: Callable[[], float] = time.monotonic,
                 rng: random.Random | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stack_source = stack
        self._monitor_source = monitors
        self._fullscreen_test = fullscreen
        self._cursor = cursor
        self._clock = clock
        self._rng = rng
        self.field: snow.SnowField | None = None
        self.monitors: list[Monitor] = []
        self.strips: dict[int, CapStrip] = {}
        self._spare: list[CapStrip] = []
        self.flights: dict[int, FlightLayer] = {}
        self._sprites: dict[tuple[str, int], QPixmap] = {}
        self.fullscreen: set[int] = set()
        self._front: tuple | None = None
        self._order: dict[int, int] = {}
        self._near_until = 0.0
        self._appeared = False
        #: Piles changed mid-flight but not re-rendered yet (``RENDER_GAP_MS``), and when each
        #: pile was last rendered.
        self._stale: set[int] = set()
        self._rendered_at: dict[int, float] = {}
        #: Every strip to be re-fitted on the next pass: the snow is coming back from hidden.
        self._refit_all = False
        #: What Nib will react to (WP5): where snow landed (``where == "nib"`` on him) and the
        #: brushes and flicks, newest last. Bounded, in case nothing collects them.
        self.landed: deque[snow.Landing] = deque(maxlen=LANDINGS_KEPT)
        self.gestures: deque[snow.Gesture] = deque(maxlen=LANDINGS_KEPT)
        # The state main hands in (set_state).
        self.active = False
        self.hidden = False
        self.frozen = False
        self.growing = True
        self.on_battery = False

        self.stack_timer = QTimer(self)
        self.stack_timer.setInterval(STACK_POLL_MS)
        self.stack_timer.timeout.connect(self._tick_stack)
        self.anim_timer = QTimer(self)
        self.anim_timer.setTimerType(Qt.PreciseTimer)
        self.anim_timer.setInterval(snow.SnowField.frame_ms(False))
        self.anim_timer.timeout.connect(self._tick_anim)
        self.pointer_timer = QTimer(self)
        self.pointer_timer.setTimerType(Qt.PreciseTimer)
        self.pointer_timer.setInterval(POINTER_POLL_MS)
        self.pointer_timer.timeout.connect(self._tick_pointer)
        self.linger_timer = QTimer(self)
        self.linger_timer.setSingleShot(True)
        self.linger_timer.setInterval(FLIGHT_LINGER_MS)
        self.linger_timer.timeout.connect(self._close_flights)

    # --- state -----------------------------------------------------------------------------

    @property
    def showing(self) -> bool:
        return self.active and not self.hidden

    def _now_ms(self) -> float:
        return self._clock() * 1000.0

    def set_state(self, *, active: bool, hidden: bool = False, frozen: bool = False,
                  growing: bool = True, on_battery: bool = False) -> None:
        """``active``: snow at all (the setting, the date, not switched off for today).
        ``hidden``: every window off the screen and every timer stopped (snoozed, a guard).
        ``frozen``: piles shown, but no growth and no brushing (focus, paused, ghost mode).
        ``growing``: the piles may grow (not on battery). ``on_battery``: animate at
        ``snow.FPS_BATTERY``. Cheap to call with nothing changed: ``main`` calls it on its
        700 ms poll."""
        was_showing = self.showing
        self.active, self.hidden = bool(active), bool(hidden)
        self.frozen, self.on_battery = bool(frozen), bool(on_battery)
        self.growing = bool(growing) and not self.frozen
        frame = snow.SnowField.frame_ms(self.on_battery)
        if self.anim_timer.interval() != frame:
            self.anim_timer.setInterval(frame)
        if not self.active:
            if self.field is not None or self.strips or self._spare:
                self._teardown()
            return
        if self.field is None:
            self.field = snow.SnowField(rng=self._rng)
        if self.frozen:
            self.pointer_timer.stop()
        if not self.showing:
            if was_showing:
                self._hide_all()
            return
        if not was_showing:
            self._refit_all = True
            self.stack_timer.start()
            self._tick_stack()
            if self.field is not None and self.field.airborne:
                self._start_anim()

    def set_nib(self, rect: Rect | None) -> None:
        """Nib's rectangle (or ``None`` while he is hidden): snow falling through it lands on him
        (:attr:`landed`, ``where == "nib"``)."""
        if self.field is not None:
            self.field.set_nib(rect)

    def take_landed(self) -> list[snow.Landing]:
        out = list(self.landed)
        self.landed.clear()
        return out

    def take_gestures(self) -> list[snow.Gesture]:
        out = list(self.gestures)
        self.gestures.clear()
        return out

    def shutdown(self) -> None:
        self.active = False
        self._teardown()

    def _teardown(self) -> None:
        """No snow at all: every timer stopped and every window gone, so it costs nothing."""
        for t in (self.stack_timer, self.anim_timer, self.pointer_timer, self.linger_timer):
            t.stop()
        self._close_flights()
        for strip in list(self.strips.values()) + self._spare:
            strip.hide()
            strip.deleteLater()
        self.strips.clear()
        self._spare.clear()
        self.field = None
        self.monitors = []
        self._front = None
        self.fullscreen = set()

    def _hide_all(self) -> None:
        """Hidden, not empty (plan section 0 rule 5): the piles keep their snow, every window
        is off the screen and every timer is stopped."""
        for t in (self.stack_timer, self.anim_timer, self.pointer_timer, self.linger_timer):
            t.stop()
        self._close_flights()
        for strip in self.strips.values():
            strip.hide()
        self._front = None

    # --- the 2 Hz tick ---------------------------------------------------------------------

    def _refresh_monitors(self) -> None:
        mons = self._monitor_source()
        if not mons:
            return
        if [m.key() for m in mons] != [m.key() for m in self.monitors]:
            self.field.set_monitors([m.rect for m in mons], [m.floor for m in mons],
                                    [m.scale for m in mons])
            self._close_flights()   # a layer is the size of its monitor
        self.monitors = mons

    def _tick_stack(self) -> None:
        field = self.field
        if field is None or not self.showing:
            self.stack_timer.stop()
            return
        now = self._now_ms()
        self._refresh_monitors()
        stack = self._stack_source()
        front = None
        if stack is not None:
            own = self._own_windows()
            # What is at the front comes from the real stack: an own window always heads the one
            # handed to sync, so with Settings open the front would never change again. Our own
            # windows coming or going counts as a change too: they start above the strips.
            front = (next((int(entry[0]) for entry in stack if not entry[3]), 0),
                     tuple(int(entry[0]) for entry in own))
            stack = own + list(stack)
        sync = field.sync(stack, now)
        fullscreen = set(sync.fullscreen)
        fullscreen.update(idx for idx, mon in enumerate(self.monitors)
                          if self._fullscreen_test(mon))
        refit = fullscreen != self.fullscreen or self._refit_all
        self._refit_all = False
        self.fullscreen = fullscreen
        for idx in fullscreen:
            self._close_flight(idx)
        if stack is not None:
            self._order = {int(entry[0]): k for k, entry in enumerate(stack)}
        for cap in field.caps.values() if fullscreen else ():
            if self.monitor_of(cap) in fullscreen:
                # Behind a fullscreen app it is hidden entirely (plan 1.4), growth included:
                # its schedule is held a period ahead, so it carries on from there afterwards.
                cap.next_grow_ms = max(cap.next_grow_ms, now + snow.GROWTH_STEP_MS)
                cap.next_icicle_ms = max(cap.next_icicle_ms, now + snow.ICICLE_STEP_MS)
        grown = field.grow(now) if self.growing else set()

        for hwnd in sync.removed:
            self._release(hwnd)
        render = sync.caps | grown | (self._stale & field.caps.keys())
        self._stale.clear()
        for hwnd in list(field.caps):
            if (hwnd in render or hwnd in sync.moved or hwnd in sync.clipped or refit
                    or hwnd not in self.strips):
                self._refresh(hwnd, render=hwnd in render)
        if front is not None:
            if front != self._front:
                # The window at the front changed: something was clicked to the front, and it
                # may have risen over strips it used to be under. They go back to the top of the
                # normal band, back-most first, so the front window's pile ends up on top.
                self._front = front
                self._raise_strips()
        if sync.chunks or field.airborne:
            self._start_anim()
        self._check_near(now)

    def _raise_strips(self) -> None:
        shown = [(self._order.get(h, 1 << 30), strip) for h, strip in self.strips.items()
                 if strip.isVisible()]
        for _k, strip in sorted(shown, key=lambda kv: -kv[0]):
            strip.raise_()
        # Snow in the air passes in front of every pile, not behind the ones just raised.
        for layer in self.flights.values():
            layer.raise_()

    @staticmethod
    def _own_windows() -> list[tuple[int, Rect, bool, bool]]:
        """Nib's own ordinary windows (Settings, a file dialog, a message box), as stack entries
        in front of everything. ``list_window_stack`` leaves this process out, but these sit in
        the same band as the strips, so a strip raised to the top of it would paint snow over
        them. Marked maximised, so they hide the piles under them but never carry one; his
        topmost windows (the mascot, the bubble, the wheel) are above every strip anyway."""
        out = []
        for widget in QApplication.topLevelWidgets():
            if (not widget.isVisible() or isinstance(widget, _SnowWindow)
                    or widget.windowFlags() & Qt.WindowStaysOnTopHint
                    or widget.windowType() in (Qt.Popup, Qt.ToolTip)):
                continue
            g = widget.frameGeometry()
            if g.width() > 0 and g.height() > 0:
                out.append((int(widget.winId()), (g.left(), g.top(), g.left() + g.width(),
                                                  g.top() + g.height()), True, False))
        return out

    # --- strips ----------------------------------------------------------------------------

    def monitor_of(self, cap: snow.Cap) -> int:
        """The monitor a pile is on: the one holding most of its window's top edge (the model's
        rule), or -1."""
        left, top, right, _bottom = cap.rect
        best, best_w = -1, 0
        for idx, mon in enumerate(self.monitors):
            m_left, m_top, m_right, m_bottom = mon.rect
            if m_top <= top < m_bottom:
                width = min(right, m_right) - max(left, m_left)
                if width > best_w:
                    best, best_w = idx, width
        return best

    def _release(self, hwnd: int) -> None:
        self._stale.discard(hwnd)
        self._rendered_at.pop(hwnd, None)
        strip = self.strips.pop(hwnd, None)
        if strip is not None:
            strip.hide()
            strip.pixmap, strip.version, strip.cap_hwnd = None, -1, 0
            self._spare.append(strip)

    def _refresh(self, hwnd: int, render: bool) -> None:
        """Bring one pile's strip up to date: its pixmap (re-rendered when ``render`` or when
        its version moved on), its place and size, its clip, and whether it shows at all."""
        cap = self.field.caps[hwnd]
        strip = self.strips.get(hwnd)
        if strip is None:
            strip = self._spare.pop() if self._spare else CapStrip()
            strip.cap_hwnd = hwnd
            self.strips[hwnd] = strip
        ext = cap.extent()
        region = QRegion()
        for left, top, right, bottom in snow.clip(ext, cap.occluders):
            region += QRect(left - ext[0], top - ext[1], right - left, bottom - top)
        strip.region = region
        show = (self.showing and cap.mass > 0.0 and not region.isEmpty()
                and self.monitor_of(cap) not in self.fullscreen)
        if not show:
            strip.hide()
            return
        if render or strip.pixmap is None or strip.version != cap.version:
            strip.pixmap = QPixmap.fromImage(render_cap(cap))
            strip.version = cap.version
            self._rendered_at[hwnd] = self._now_ms()
            self._stale.discard(hwnd)
        geometry = QRect(ext[0], ext[1], ext[2] - ext[0], ext[3] - ext[1])
        if strip.geometry() != geometry:
            strip.setGeometry(geometry)
        if strip.isVisible():
            # Synchronous (~0.5 ms, plan 1.6), so a strip the same pass then raises never shows
            # its old clip over the window that came to the front.
            strip.repaint()
        else:
            strip.show()
            if not self._appeared:
                self._appeared = True
                self.appeared.emit()

    # --- the pointer -----------------------------------------------------------------------

    def _near(self, x: float, y: float) -> bool:
        """Near a pile that is on screen: not one behind a fullscreen app (plan 1.4, hidden
        entirely), and not with the pointer where it cannot brush (:meth:`_blocked`)."""
        if self._blocked(x, y):
            return False
        margin = NEAR_MARGIN_PX * max([m.scale for m in self.monitors] or [1.0])
        for cap in self.field.caps.values():
            left, top, right, bottom = cap.extent()
            if (left - margin <= x < right + margin and top - margin <= y < bottom + margin
                    and self.monitor_of(cap) not in self.fullscreen):
                return True
        return False

    def _blocked(self, x: float, y: float) -> bool:
        """The pointer cannot brush here: it is over a window of Nib's, or on a monitor whose
        snow a fullscreen app hides."""
        for idx in self.fullscreen:
            if 0 <= idx < len(self.monitors):
                left, top, right, bottom = self.monitors[idx].rect
                if left <= x < right and top <= y < bottom:
                    return True
        return self._over_own(x, y)

    @staticmethod
    def _over_own(x: float, y: float) -> bool:
        """Over one of Nib's topmost windows (the mascot, his bubble, the wheel, a toast, the
        focus timer): above every strip, so the snow under them is not there to brush. The
        sync stack never has them (``list_window_stack`` leaves this process out, and
        :meth:`_own_windows` only adds his ordinary ones)."""
        point = QPoint(int(x), int(y))
        for widget in QApplication.topLevelWidgets():
            if (widget.isVisible() and not isinstance(widget, _SnowWindow)
                    and (widget.windowFlags() & Qt.WindowStaysOnTopHint
                         or widget.windowType() in (Qt.Popup, Qt.ToolTip))
                    and widget.frameGeometry().contains(point)):
                return True
        return False

    def _check_near(self, now: float) -> None:
        """From the 2 Hz tick: sample the cursor quickly only while it is near a pile."""
        if self.frozen or self.field is None or not self.field.caps:
            return
        x, y = self._cursor()
        if self._near(x, y):
            self._near_until = now + NEAR_LINGER_MS
            if not self.pointer_timer.isActive():
                self.pointer_timer.start()

    def _tick_pointer(self) -> None:
        field = self.field
        if field is None or not self.showing or self.frozen:
            self.pointer_timer.stop()
            return
        now = self._now_ms()
        x, y = self._cursor()
        if self._blocked(x, y):
            # Not fed while over hidden or covered snow, and the movement broken there, so a
            # fast pass across Nib or a game's monitor never brushes what nobody can see.
            field.lift()
        elif field.pointer(x, y, now):
            self._start_anim()
        if self._near(x, y):
            self._near_until = now + NEAR_LINGER_MS
        elif now >= self._near_until:
            self.pointer_timer.stop()

    # --- the flight ------------------------------------------------------------------------

    def _start_anim(self) -> None:
        self.linger_timer.stop()
        if not self.anim_timer.isActive():
            self.anim_timer.start()

    def _tick_anim(self) -> None:
        field = self.field
        if field is None or not self.showing:
            self.anim_timer.stop()
            return
        now = self._now_ms()
        changes = field.step(now)
        for hwnd in changes.removed:
            self._release(hwnd)
        self._stale |= changes.caps
        self._stale &= field.caps.keys()
        for hwnd in changes.moved & field.caps.keys():
            self._refresh(hwnd, render=True)
        for hwnd in list(self._stale):
            if now - self._rendered_at.get(hwnd, -RENDER_GAP_MS) >= RENDER_GAP_MS:
                self._refresh(hwnd, render=True)
        for idx, boxes in changes.boxes.items():
            layer = self._flight(idx)
            if layer is None:
                continue
            ox, oy = layer.origin
            # One synchronous repaint per box, deliberately not update(): Qt would merge the boxes
            # into one region, and Windows flushes a region's whole bounding box - two bursts at
            # opposite ends of a monitor would cost a full-monitor flush (as in guard_overlay).
            for left, top, right, bottom in boxes:
                layer.repaint(QRect(left - ox, top - oy, right - left, bottom - top))
        self.landed.extend(changes.landed)
        self.gestures.extend(changes.gestures)
        if not field.airborne:
            for hwnd in list(self._stale):     # the last change of the flight, drawn now
                self._refresh(hwnd, render=True)
            self.anim_timer.stop()
            if self.flights:
                self.linger_timer.start()

    def _flight(self, idx: int) -> FlightLayer | None:
        """Monitor ``idx``'s flight layer, created now if it is not there - but never on a monitor
        a fullscreen app has (plan section 5: it could cost the game its direct path to the
        display). Chunks over such a monitor still fly and land; they are just not drawn."""
        layer = self.flights.get(idx)
        if layer is not None:
            return layer
        if idx in self.fullscreen or not 0 <= idx < len(self.monitors) or not self.showing:
            return None
        layer = FlightLayer(idx, self.monitors[idx].rect, self._chunks_on, self._sprite)
        self.flights[idx] = layer
        layer.show()
        return layer

    def _chunks_on(self) -> list[snow.Chunk]:
        return list(self.field.chunks) if self.field is not None else []

    def _sprite(self, chunk: snow.Chunk) -> QPixmap:
        d = max(3, int(round(chunk.size)))
        key = (chunk.kind, d)
        pm = self._sprites.get(key)
        if pm is None:
            pm = render_shard(d) if chunk.kind == "shard" else render_lump(d, seed=d)
            self._sprites[key] = pm
        return pm

    def _close_flight(self, idx: int) -> None:
        layer = self.flights.pop(idx, None)
        if layer is not None:
            layer.hide()
            layer.deleteLater()

    def _close_flights(self) -> None:
        self.linger_timer.stop()
        for idx in list(self.flights):
            self._close_flight(idx)
