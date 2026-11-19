"""His own speech bubble: the lines he says, drawn in the same pixel art he is drawn in.

The plain toast (``bubble.Toast``) is how every tool talks - update progress, the calculator's
answer, tips. His own lines used to go through that same rectangle, and nobody read them as *him*
talking: no tail, no link to his head, left floating in place when he walked off. This widget is
for his lines only, and everything about it says "this is coming out of him":

* drawn in his pixel style - hard edges, no anti-aliasing, his 1px dark outline tone and a light
  fill, corners clipped in steps the way his head is - at his own integer sprite scale, so one
  bubble pixel is exactly one of his pixels;
* a pixel tail pointing down at his head (or up, when there is no room above him);
* it pops in over a few hard steps rather than fading, then the words type out letter by letter;
* it follows him while he walks, and keeps itself on screen at the edges.

The text itself is a normal, anti-aliased font. Pixel text at this size is a puzzle to read, and
the bubble is only worth having if it is read.

It knows nothing about behaviour, sound or the config: ``main`` listens to its signals and decides
what a blip or a click means, so this module stays a widget and nothing else.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QWidget

# --- look -----------------------------------------------------------------------------------

#: His outline tone ("O" in tools/gen_sprites.py's palette) - the bubble's border and its text.
OUTLINE = "#2C1206"
#: The fill: close to his "L" light-detail tone, a touch whiter so dark text sits well on it.
FILL = "#FFF8F2"
#: One row of shade along the inside of the bottom edge - the same trick his body uses to read as
#: a solid thing rather than a flat cut-out.
SHADE = "#EADBCF"

#: Corner steps, top row first - the same blocky clipped corner as his head (TOP_INSETS there).
CORNER_INSETS = (2, 1)
#: The tail is this many bubble pixels long, below (or above) the body.
TAIL_ROWS = 3
#: Gap, in bubble pixels, between the tail's tip and the top of his head.
TAIL_GAP = 1
#: Smallest body, in bubble pixels. 11 wide is the least that fits both clipped corners plus the
#: five-pixel tail opening between them - narrower and the tail would cut into a corner.
MIN_COLS = 11
MIN_ROWS = 5

#: Where the top of his head sits in his frame: the body's top outline is row 18 of 48 (BODY_H in
#: gen_sprites puts the body top at row 19). Pointing at the frame's top edge instead would leave
#: the bubble floating a third of a sprite above him.
HEAD_TOP_FRACTION = 18 / 48

#: Longest a line of text gets before it wraps, in UI pixels at 100%.
MAX_TEXT_WIDTH = 230
FONT_PX = 13
#: The optional second line (what a click does, or a short note), smaller and muted.
SUB_FONT_PX = 11
SUB_COLOUR = "#7A6E66"
SUB_GAP = 3
#: Text inset from the outline, in UI pixels at 100%.
PAD_X = 10
PAD_Y = 7

# --- timing -----------------------------------------------------------------------------------

#: Typewriter speed. ~40 letters a second: quick enough that a line never feels slow to arrive,
#: slow enough that it visibly *types* rather than flickering in.
CHAR_MS = 25
#: Extra pause after a sentence or clause ends, so the rhythm reads as speech rather than a ticker.
PAUSE_AFTER = {".": 180, "!": 180, "?": 180, ",": 90, ";": 90, ":": 90, "…": 220}
#: Pop-in steps, as fractions of the full body size - three hard steps, no blur, no fade.
POP_IN = (0.45, 0.75, 1.0)
POP_OUT = (0.7, 0.4)
POP_STEP_MS = 45
#: One blip per this many voiced (non-space, non-punctuation) letters, and never closer than
#: BLIP_MIN_GAP_MS - about twelve a second at most, a murmur rather than a machine.
BLIP_EVERY = 3
BLIP_MIN_GAP_MS = 85

#: Reading time on top of the typing: a fixed glance plus a little per letter. Readers manage about
#: 15-20 letters a second; most of a line is read while it types, so the per-letter share is low.
READ_BASE_MS = 1400
READ_PER_CHAR_MS = 32


def type_ms(text: str) -> int:
    """How long ``text`` takes to type out, pauses included."""
    return sum(CHAR_MS + PAUSE_AFTER.get(ch, 0) for ch in text)


def hold_ms(text: str, minimum_ms: int) -> int:
    """How long the bubble stays up in all: never less than ``minimum_ms``, and always long enough
    to type the line out and then read it - a long line given the same few seconds as "Hi!" would
    be gone before the end of it was read."""
    return max(int(minimum_ms), type_ms(text) + READ_BASE_MS + READ_PER_CHAR_MS * len(text))


def _voiced(ch: str) -> bool:
    """A letter that gets a blip: not a space, not punctuation."""
    return ch.isalnum()


def pixel_rows(width: int, height: int, tail_x: int, tail_down: bool = True) -> list[tuple]:
    """The bubble as horizontal runs in bubble pixels: ``(y, x, length, kind)``.

    ``kind`` is ``"o"`` (outline), ``"f"`` (fill) or ``"s"`` (shade). ``width`` x ``height`` is the
    body including its outline; the tail adds TAIL_ROWS rows below it (above it, flipped, when
    ``tail_down`` is False), centred on column ``tail_x``. Pure, so the shape can be reasoned about
    - and checked - without a screen.
    """
    runs: list[tuple] = []
    w, h = max(MIN_COLS, width), max(MIN_ROWS, height)
    tx = max(len(CORNER_INSETS) + 3, min(w - len(CORNER_INSETS) - 4, tail_x))
    # The tail opens through the body's edge: five fill pixels wide there, narrowing a step a row.
    for y in range(h):
        edge = min(y, h - 1 - y)
        inset = CORNER_INSETS[edge] if edge < len(CORNER_INSETS) else 0
        left, right = inset, w - 1 - inset
        top_or_bottom = y == 0 or y == h - 1
        opens = (y == h - 1) if tail_down else (y == 0)
        if top_or_bottom:
            if opens:
                runs.append((y, left, tx - 2 - left, "o"))
                runs.append((y, tx - 2, 5, "f"))
                runs.append((y, tx + 3, right - tx - 2, "o"))
            else:
                runs.append((y, left, right - left + 1, "o"))
            continue
        runs.append((y, left, 1, "o"))
        runs.append((y, right, 1, "o"))
        inner_kind = "s" if (y == h - 2) else "f"
        if inner_kind == "s" and tail_down:
            # The shade breaks where the tail opens, so the tail reads as part of the fill.
            runs.append((y, left + 1, tx - 2 - (left + 1), "s"))
            runs.append((y, tx - 2, 5, "f"))
            runs.append((y, tx + 3, right - tx - 3, "s"))
        else:
            runs.append((y, left + 1, right - left - 1, inner_kind))
    for i in range(TAIL_ROWS):
        y = h + i if tail_down else -1 - i
        half = 2 - i
        if half > 0:
            runs.append((y, tx - half, 1, "o"))
            runs.append((y, tx - half + 1, 2 * half - 1, "f"))
            runs.append((y, tx + half, 1, "o"))
        else:
            runs.append((y, tx, 1, "o"))
    if not tail_down:
        runs = [(y + TAIL_ROWS, x, n, k) for (y, x, n, k) in runs]
    return [r for r in runs if r[2] > 0]


def _wrap(text: str, metrics: QFontMetrics, max_width: int) -> list[str]:
    """Greedy word wrap of the WHOLE line, done once before typing starts.

    Wrapping the half-typed text instead would move a word to the next line the moment it grew too
    long for this one - words jumping about mid-sentence - and resize the bubble as it typed.
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else f"{current} {word}"
            if current and metrics.horizontalAdvance(candidate) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


class SpeechBubble(QWidget):
    """His speech bubble. ``say`` it, ``follow`` him with it, ``dismiss`` it.

    Never takes focus or activates (Qt.ToolTip + WA_ShowWithoutActivating, like the toast), and
    masks itself to its own pixels, so only the drawn bubble catches the mouse.
    """

    #: The bubble was clicked - you noticed him.
    clicked = Signal()
    #: Time for one syllable of his voice.
    blip = Signal()
    #: The last letter is out; his mouth can stop.
    typed = Signal()
    #: Gone from the screen, whatever the reason.
    finished = Signal()

    def __init__(self, ui_scale: float = 1.0) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.PointingHandCursor)
        self._ui = max(1.0, float(ui_scale))
        self._font = QFont(self.font())
        self._font.setPixelSize(max(10, int(round(FONT_PX * self._ui))))
        self._font.setWeight(QFont.Weight.DemiBold)
        self._sub_font = QFont(self.font())
        self._sub_font.setPixelSize(max(9, int(round(SUB_FONT_PX * self._ui))))
        self._sub = ""
        self._sub_lines: list[str] = []

        self._text = ""
        self._lines: list[str] = []
        self._shown = 0          # letters typed so far
        self._voiced = 0         # voiced letters typed since the last blip
        self._last_blip = -BLIP_MIN_GAP_MS
        self._elapsed_ms = 0     # typewriter clock, for the blip spacing
        self._p = 1              # one bubble pixel, in screen pixels
        self._cols = self._rows = 0
        self._tail_col = 0
        self._tail_down = True
        self._step = 1.0         # pop scale of the body, 1.0 when fully open
        self._closing = False
        self._typing = False
        self._body_cache: dict[tuple, QPixmap] = {}
        self._anchor: tuple | None = None
        self._screen: QRect | None = None

        self._type_timer = QTimer(self)
        self._type_timer.timeout.connect(self._type_next)
        self._type_timer.setTimerType(Qt.PreciseTimer)
        self._pop_timer = QTimer(self)
        self._pop_timer.setInterval(POP_STEP_MS)
        self._pop_timer.timeout.connect(self._pop_next)
        self._pop_steps: list[float] = []
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self.pop_out)

    # --- public -----------------------------------------------------------------------------

    def text(self) -> str:
        """The whole line being said (not just what has typed out so far)."""
        return self._text

    @property
    def typing(self) -> bool:
        """Still popping in or typing out - letters (and so blips) still to come."""
        return self._typing

    def say(self, text: str, anchor: QRect, screen: QRect, pixel: int, minimum_ms: int,
            sub: str = "") -> int:
        """Pop up over his head with ``text`` and start typing it. Returns the ms until the last
        letter is out (pop-in included).

        ``anchor`` is his whole sprite frame on screen (full sprite height, not a peek's cropped
        one); ``pixel`` is his integer sprite scale, so the bubble's pixels are his pixels.
        ``sub`` is an optional smaller second line (say, "Click to copy"); it shows, whole, once
        the main line has typed out, and is never voiced.
        """
        self._stop_timers()
        self._text = text
        self._p = max(1, int(pixel))
        metrics = QFontMetrics(self._font)
        self._lines = _wrap(text, metrics, int(MAX_TEXT_WIDTH * self._ui))
        self._sub = sub
        sub_metrics = QFontMetrics(self._sub_font)
        self._sub_lines = _wrap(sub, sub_metrics, int(MAX_TEXT_WIDTH * self._ui)) if sub else []
        text_w = max([metrics.horizontalAdvance(line) for line in self._lines]
                     + [sub_metrics.horizontalAdvance(line) for line in self._sub_lines] + [0])
        text_h = metrics.lineSpacing() * len(self._lines)
        if self._sub_lines:
            text_h += int(SUB_GAP * self._ui) + sub_metrics.lineSpacing() * len(self._sub_lines)
        p = self._p
        # Body size in bubble pixels: the text plus padding plus a pixel of outline either side,
        # rounded UP to whole pixels - a part-pixel would be exactly the blur this is avoiding.
        self._cols = max(MIN_COLS, math.ceil((text_w + 2 * PAD_X * self._ui) / p) + 2)
        self._rows = max(MIN_ROWS, math.ceil((text_h + 2 * PAD_Y * self._ui) / p) + 2)
        self.setFixedSize(self._cols * p, (self._rows + TAIL_ROWS) * p)
        self._shown = 0
        self._voiced = BLIP_EVERY - 1  # so the very first letter blips
        self._elapsed_ms = 0
        self._last_blip = -BLIP_MIN_GAP_MS
        self._closing = False
        # True from now, through the pop-in, until the last letter: "has words still to come".
        self._typing = bool(text)
        self._body_cache.clear()
        self._anchor = None
        self._place(anchor, screen)
        self._pop_steps = list(POP_IN)
        self._step = self._pop_steps.pop(0)
        self._apply_mask()
        self.show()
        self.raise_()
        self.update()
        self._pop_timer.start()
        reading = text + (" " + sub if sub else "")
        self._hold_timer.start(hold_ms(reading, minimum_ms) + POP_STEP_MS * len(POP_IN))
        # Counted from now, so it includes the pop-in before the first letter: the caller uses it
        # to time his mouth, and a mouth that stops before the last word looks dubbed.
        return type_ms(text) + POP_STEP_MS * len(POP_IN)

    def follow(self, anchor: QRect, screen: QRect) -> None:
        """Keep the tail on his head as he moves. A no-op unless his rect actually changed, so it
        is safe to call from every tick: no move, no repaint, nothing while he stands still."""
        if self.isVisible():
            self._place(anchor, screen)

    def dismiss(self) -> None:
        """Gone now - no pop-out. For when the app puts him away (a guard, a fullscreen app, ...)."""
        if not self.isVisible() and not self._type_timer.isActive():
            return
        self._stop_timers()
        was_visible = self.isVisible()
        self.hide()
        self._typing = False
        if was_visible:
            self.finished.emit()

    def pop_out(self) -> None:
        """Time's up (or you clicked): shrink away in a couple of hard steps, then hide."""
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._hold_timer.stop()
        if self._typing:
            self._type_timer.stop()
            self._typing = False
            self.typed.emit()
        self._pop_steps = list(POP_OUT)
        self._pop_timer.start()

    # --- placing ----------------------------------------------------------------------------

    def _place(self, anchor: QRect, screen: QRect) -> None:
        key = (anchor.x(), anchor.y(), anchor.width(), anchor.height(),
               screen.x(), screen.y(), screen.width(), screen.height())
        if key == self._anchor:
            return
        self._anchor = key
        self._screen = QRect(screen)
        p = self._p
        width, height = self.width(), self.height()
        head_x = anchor.center().x()
        head_y = anchor.top() + int(round(anchor.height() * HEAD_TOP_FRACTION))
        margin = 4
        x = head_x - width // 2
        x = max(screen.left() + margin, min(screen.right() - width - margin, x))
        # Above his head, tail down - unless that runs off the top of the screen (he is held up
        # there, or perched on a window near the top), in which case below his feet, tail up.
        y = head_y - TAIL_GAP * p - height
        tail_down = y >= screen.top() + margin
        if not tail_down:
            y = min(anchor.bottom() + TAIL_GAP * p, screen.bottom() - height - margin)
        # The tail column is re-derived from where the bubble actually landed, so clamping at a
        # screen edge slides the tail along to stay over him rather than pointing at empty desk.
        tail_col = (head_x - x) // p
        if tail_down != self._tail_down or tail_col != self._tail_col:
            self._tail_down = tail_down
            self._tail_col = tail_col
            self._apply_mask()
            self.update()
        if self.pos() != QPoint(x, y):
            self.move(x, y)

    # --- drawing ----------------------------------------------------------------------------

    def _body_size(self) -> tuple[int, int, int]:
        """Columns, rows and column offset of the body at the current pop step - grown from the
        tail, so a popping bubble swells out of the point over his head rather than its corner."""
        if self._step >= 1.0:
            return self._cols, self._rows, 0
        cols = max(MIN_COLS, int(round(self._cols * self._step)))
        rows = max(MIN_ROWS, int(round(self._rows * self._step)))
        tail = max(5, min(self._cols - 6, self._tail_col))
        offset = max(0, min(self._cols - cols, tail - cols // 2))
        return cols, rows, offset

    def _runs(self) -> list[tuple]:
        cols, rows, offset = self._body_size()
        runs = pixel_rows(cols, rows, self._tail_col - offset, self._tail_down)
        # The body sits against the tail end of the window; a shrunken one is centred on the tail.
        dy = (self._rows - rows) if self._tail_down else 0
        return [(y + dy, x + offset, n, k) for (y, x, n, k) in runs]

    def _apply_mask(self) -> None:
        """Mask the window to the drawn pixels, so the transparent corners and the space either side
        of the tail let clicks through to whatever is underneath."""
        p = self._p
        region = QRegion()
        for y, x, n, _k in self._runs():
            region = region.united(QRegion(x * p, y * p, n * p, p))
        self.setMask(region)

    def _body_pixmap(self) -> QPixmap:
        key = (self._step, self._tail_col, self._tail_down, self._cols, self._rows, self._p)
        cached = self._body_cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, False)
        colours = {"o": QColor(OUTLINE), "f": QColor(FILL), "s": QColor(SHADE)}
        p = self._p
        for y, x, n, kind in self._runs():
            painter.fillRect(x * p, y * p, n * p, p, colours[kind])
        painter.end()
        self._body_cache[key] = pixmap
        return pixmap

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._body_pixmap())
        if self._step < 1.0 or not self._shown:
            return
        p = self._p
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setFont(self._font)
        painter.setPen(QColor(OUTLINE))
        metrics = QFontMetrics(self._font)
        body_top = 0 if self._tail_down else TAIL_ROWS * p
        sub_metrics = QFontMetrics(self._sub_font)
        text_w = max([metrics.horizontalAdvance(line) for line in self._lines]
                     + [sub_metrics.horizontalAdvance(line) for line in self._sub_lines] + [0])
        text_h = metrics.lineSpacing() * len(self._lines)
        if self._sub_lines:
            text_h += int(SUB_GAP * self._ui) + sub_metrics.lineSpacing() * len(self._sub_lines)
        left = (self._cols * p - text_w) // 2
        top = body_top + (self._rows * p - text_h) // 2
        remaining = self._shown
        for i, line in enumerate(self._lines):
            if remaining <= 0:
                break
            part = line[:remaining]
            painter.drawText(left, top + i * metrics.lineSpacing() + metrics.ascent(), part)
            remaining -= len(line) + 1  # +1 for the space (or newline) the wrap swallowed
        if self._sub_lines and self._shown >= len(self._text):
            painter.setFont(self._sub_font)
            painter.setPen(QColor(SUB_COLOUR))
            y = top + metrics.lineSpacing() * len(self._lines) + int(SUB_GAP * self._ui)
            for i, line in enumerate(self._sub_lines):
                painter.drawText(left, y + i * sub_metrics.lineSpacing() + sub_metrics.ascent(),
                                 line)

    # --- clocks -----------------------------------------------------------------------------

    def _pop_next(self) -> None:
        if not self._pop_steps:
            self._pop_timer.stop()
            if self._closing:
                self.dismiss()
                return
            # Fully open: now the words.
            if self._text:
                self._typing = True
                self._schedule(0)
            return
        self._step = self._pop_steps.pop(0)
        self._apply_mask()
        self.update()

    def _schedule(self, delay: int) -> None:
        self._type_timer.setSingleShot(True)
        self._type_timer.start(max(1, delay))

    def _type_next(self) -> None:
        if not self._typing or self._shown >= len(self._text):
            return
        ch = self._text[self._shown]
        self._shown += 1
        if _voiced(ch):
            # Counted since the last blip rather than as a running multiple of three, so a blip
            # the spacing held back comes on the next letter instead of three letters later.
            self._voiced += 1
            if (self._voiced >= BLIP_EVERY
                    and self._elapsed_ms - self._last_blip >= BLIP_MIN_GAP_MS):
                self._voiced = 0
                self._last_blip = self._elapsed_ms
                self.blip.emit()
        self.update()
        if self._shown >= len(self._text):
            self._typing = False
            self.typed.emit()
            return
        delay = CHAR_MS + PAUSE_AFTER.get(ch, 0)
        self._elapsed_ms += delay
        self._schedule(delay)

    def _stop_timers(self) -> None:
        self._type_timer.stop()
        self._pop_timer.stop()
        self._hold_timer.stop()

    # --- input ------------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._closing:
            self.clicked.emit()
        self.pop_out()
        event.accept()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        # Whatever hid it, no clock keeps running for a bubble nobody can see - and with the type
        # timer stopped no more letters are coming, so ``typing`` must say so, or the app would
        # keep its fast talking tick for a line that is gone.
        self._stop_timers()
        self._typing = False
