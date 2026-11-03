"""The speech-bubble prompt input, plus a small toast for launch failures.

``AskBubble`` is a ``Qt.Popup``, which buys us click-away dismissal and a keyboard grab for free.
It reports its outcome precisely, because the personality model depends on the distinction: a
bubble that closes without sending is a *poke* and annoys the mascot, whereas a bubble that sends
is not.

Both widgets take a ``ui_scale``. The app runs Qt in physical pixels (see ``main``) so that the
mascot can be aligned to the taskbar exactly and scaled by an integer factor, which means ordinary
chrome does not grow on a high-DPI display unless we scale it ourselves.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPalette,
    QPolygonF,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

from calculator import evaluate as calculate
from launcher import MAX_PROMPT_CHARS, get_provider

BUBBLE_WIDTH = 360
TAIL_H = 10
TAIL_W = 18
RADIUS = 13
MAX_VISIBLE_LINES = 4

ACCENT = "#D9552E"


def _is_dark() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return app.palette().color(QPalette.Window).lightness() < 128


def _theme() -> dict[str, str]:
    if _is_dark():
        return {
            "bg": "#2A2725",
            "border": "#4A443F",
            "text": "#F2EDE9",
            "muted": "#A9A09A",
            "field_bg": "#1F1D1B",
            "seg_bg": "#211F1D",
        }
    return {
        "bg": "#FFFFFF",
        "border": "#E4DAD3",
        "text": "#2C1206",
        "muted": "#8A7F78",
        "field_bg": "#F8F4F1",
        "seg_bg": "#F1EAE5",
    }


class _PromptEdit(QPlainTextEdit):
    """Auto-growing text box where Enter submits and Shift+Enter makes a new line.

    Up and Down walk back through recent prompts, the way a shell does. The binding is
    *conditional*: Up only recalls when the caret is already on the first line, so a multi-line
    prompt can still be edited normally. Unconditionally stealing the arrow keys would make the
    box unusable for exactly the long prompts most worth recalling.
    """

    submit = Signal()
    cancel = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._history_index = -1   # -1 means "not browsing"; what you had typed lives in _draft
        self._draft = ""
        self.setPlaceholderText("Summarize my notes…")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setTabChangesFocus(True)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self.refresh_height()
        )

    def refresh_height(self) -> None:
        """Grow with the text up to ``MAX_VISIBLE_LINES``, then scroll instead.

        Note ``QPlainTextDocumentLayout.documentSize().height()`` is a *line count*, not a pixel
        height - one of Qt's sharper edges, and the reason this is not simple pixel arithmetic.
        """
        line = max(1, self.fontMetrics().lineSpacing())
        line_count = float(self.document().documentLayout().documentSize().height())
        visible = max(1.0, min(float(MAX_VISIBLE_LINES), line_count))
        margins = self.contentsMargins()
        chrome = margins.top() + margins.bottom() + self.frameWidth() * 2 + 6
        target = int(visible * line + chrome)
        if target != self.height():
            self.setFixedHeight(target)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if line_count > MAX_VISIBLE_LINES else Qt.ScrollBarAlwaysOff
        )

    # --- history ------------------------------------------------------------------

    def set_history(self, entries: list[str]) -> None:
        """Adopt the recent prompts, newest first, and stop browsing whatever was open."""
        self._history = [e for e in entries if e]
        self._history_index = -1
        self._draft = ""

    def _on_first_line(self) -> bool:
        cursor = self.textCursor()
        return cursor.blockNumber() == 0

    def _on_last_line(self) -> bool:
        return self.textCursor().blockNumber() == self.document().blockCount() - 1

    def _recall(self, delta: int) -> bool:
        """Step through the history. Returns False when there is nothing to step to."""
        if not self._history:
            return False
        if self._history_index < 0:
            if delta < 0:
                return False           # already at the newest end, which is your own draft
            self._draft = self.toPlainText()
        index = self._history_index + delta
        if index < -1:
            index = -1
        if index >= len(self._history):
            return False               # walked off the old end; stay where you are
        self._history_index = index
        # -1 puts back what you had typed before you started browsing, which is what makes this
        # safe to explore: nothing you were in the middle of writing is ever lost.
        self.setPlainText(self._draft if index < 0 else self._history[index])
        self.moveCursor(QTextCursor.End)
        return True

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            event.accept()
            return
        if key == Qt.Key_Escape:
            self.cancel.emit()
            event.accept()
            return
        if key == Qt.Key_Up and self._on_first_line() and self._recall(+1):
            event.accept()
            return
        if key == Qt.Key_Down and self._on_last_line() and self._recall(-1):
            event.accept()
            return
        # Any ordinary edit abandons the browse, so the next Up starts from the newest again
        # rather than from wherever you had scrolled to.
        if event.text() and self._history_index >= 0:
            self._history_index = -1
            self._draft = ""
        super().keyPressEvent(event)


class AskBubble(QWidget):
    """Prompt input anchored above the mascot."""

    submitted = Signal(str, str)  # mode, text
    calculated = Signal(str, str, bool)  # expression, result, ok
    dismissed = Signal()

    def __init__(self, ui_scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._s = max(1.0, float(ui_scale))
        self._width = int(BUBBLE_WIDTH * self._s)
        self._tail_h = int(TAIL_H * self._s)
        self._tail_w = int(TAIL_W * self._s)
        self._radius = int(RADIUS * self._s)
        self.setFixedWidth(self._width)

        self._submitting = False
        self._suppress_dismiss = False
        self._clipboard_text = ""
        self._provider = get_provider(None)
        self._tail_x = self._width // 2
        self._theme = _theme()
        #: Reopened fresh on every ``show_for`` - see that method's own docstring on why this is
        #: handed in each time rather than owned. ``None`` until then, so a stray textChanged
        #: during construction cannot try to reposition a bubble that has never been placed.
        self._anchor: QRect | None = None
        self._screen: QRect | None = None
        self._calculator_enabled = True
        self._base_hint = "Enter to send"

        pad = int(14 * self._s)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(pad, int(12 * self._s), pad, int(12 * self._s) + self._tail_h)
        outer.setSpacing(int(9 * self._s))

        header = QHBoxLayout()
        header.setSpacing(int(8 * self._s))
        self._segment = QWidget(self)
        seg_layout = QHBoxLayout(self._segment)
        gap = max(2, int(3 * self._s))
        seg_layout.setContentsMargins(gap, gap, gap, gap)
        seg_layout.setSpacing(gap)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._seg_layout = seg_layout
        header.addWidget(self._segment, 0, Qt.AlignLeft)
        header.addStretch(1)
        # Offered only when there is actually something on the clipboard, and read only at the
        # moment the bubble opens - nothing polls or stores your clipboard.
        self._clip = QPushButton("", self)
        self._clip.setObjectName("clip")
        self._clip.setCursor(Qt.PointingHandCursor)
        self._clip.setFocusPolicy(Qt.NoFocus)
        self._clip.clicked.connect(self._paste_clipboard)
        self._clip.hide()
        header.addWidget(self._clip, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._hint = QLabel("Enter to send", self)
        self._hint.setObjectName("hint")
        header.addWidget(self._hint, 0, Qt.AlignRight | Qt.AlignVCenter)
        outer.addLayout(header)

        self._input = _PromptEdit(self)
        self._input.submit.connect(self._on_submit)
        self._input.cancel.connect(self.close)
        self._input.textChanged.connect(self._on_text_changed)
        outer.addWidget(self._input)

        # The live calculator preview - see _on_text_changed. Built hidden and toggled by
        # setVisible rather than added/removed from the layout, and every toggle goes through
        # _reposition(): the bubble is positioned bottom-up from its own height (see show_for),
        # so this widget appearing after that math has already run would otherwise push the tail
        # down and off Nib's head without moving the popup back up to compensate.
        self._preview = QLabel("", self)
        self._preview.setObjectName("preview")
        self._preview.setWordWrap(True)
        self._preview.hide()
        outer.addWidget(self._preview)

        self._apply_style()
        self._input.refresh_height()

    # --- styling ------------------------------------------------------------------

    def _max_chars(self) -> int:
        """The selected provider's own cap - they differ, and a browser URL is the tighter one."""
        return min(MAX_PROMPT_CHARS, self._provider.max_chars)

    def _px(self, value: float) -> int:
        return max(1, int(round(value * self._s)))

    def _apply_style(self) -> None:
        t = self._theme = _theme()
        self._segment.setStyleSheet(
            f"""
            QWidget {{ background: {t['seg_bg']}; border-radius: {self._px(9)}px; }}
            QPushButton {{
                background: transparent; border: none; border-radius: {self._px(7)}px;
                padding: {self._px(4)}px {self._px(13)}px; color: {t['muted']};
                font-size: {self._px(12)}px; font-weight: 600;
            }}
            QPushButton:hover {{ color: {t['text']}; }}
            QPushButton:checked {{ background: {ACCENT}; color: #FFFFFF; }}
            """
        )
        self.setStyleSheet(
            f"""
            QLabel#hint {{ color: {t['muted']}; font-size: {self._px(11)}px;
                           background: transparent; }}
            QLabel#preview {{ font-size: {self._px(13)}px; font-weight: 600;
                               background: transparent; padding: 0 {self._px(2)}px; }}
            QPushButton#clip {{
                background: {t['seg_bg']}; border: 1px solid {t['border']};
                border-radius: {self._px(7)}px; padding: {self._px(3)}px {self._px(9)}px;
                color: {t['muted']}; font-size: {self._px(11)}px;
            }}
            QPushButton#clip:hover {{ color: {t['text']}; border-color: {ACCENT}; }}
            QPlainTextEdit {{
                background: {t['field_bg']};
                border: {max(1, self._px(1))}px solid {t['border']};
                border-radius: {self._px(9)}px;
                padding: {self._px(6)}px {self._px(8)}px;
                color: {t['text']};
                font-size: {self._px(13)}px;
                selection-background-color: {ACCENT};
            }}
            QPlainTextEdit:focus {{ border: {max(1, self._px(1))}px solid {ACCENT}; }}
            """
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        t = self._theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        body = QRect(0, 0, self.width() - 1, self.height() - self._tail_h - 1)

        path = QPainterPath()
        path.addRoundedRect(
            float(body.x()),
            float(body.y()),
            float(body.width()),
            float(body.height()),
            self._radius,
            self._radius,
        )
        low = self._radius + self._tail_w
        tail_x = max(low, min(self.width() - low, self._tail_x))
        path.addPolygon(
            QPolygonF(
                [
                    QPointF(tail_x - self._tail_w / 2, body.bottom() - 1),
                    QPointF(tail_x + self._tail_w / 2, body.bottom() - 1),
                    QPointF(tail_x, body.bottom() + self._tail_h),
                ]
            )
        )
        painter.setPen(QColor(t["border"]))
        painter.setBrush(QColor(t["bg"]))
        painter.drawPath(path.simplified())

    # --- behaviour ----------------------------------------------------------------

    def _build_modes(self, provider) -> None:
        """Rebuild the mode buttons for whichever assistant is selected.

        Claude has Chat and Cowork; ChatGPT has one mode and therefore no choice to make, so the
        picker is hidden entirely rather than shown with a single button nobody can unpick.
        """
        if list(self._buttons) == list(provider.modes):
            return
        for button in self._buttons.values():
            self._group.removeButton(button)
            self._seg_layout.removeWidget(button)
            button.deleteLater()
        self._buttons = {}
        for mode in provider.modes:
            button = QPushButton(provider.label_for(mode), self._segment)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            self._seg_layout.addWidget(button)
            self._group.addButton(button)
            self._buttons[mode] = button
        self._segment.setVisible(len(self._buttons) > 1)

    @property
    def mode(self) -> str:
        for mode, button in self._buttons.items():
            if button.isChecked():
                return mode
        return next(iter(self._buttons), "chat")

    def set_mode(self, mode: str) -> None:
        button = self._buttons.get(mode)
        if button is None and self._buttons:
            button = next(iter(self._buttons.values()))
        if button is not None:
            button.setChecked(True)

    def show_for(
        self,
        anchor: QRect,
        screen: QRect,
        mode: str,
        prefill: str = "",
        history: list[str] | None = None,
        provider: str | None = None,
        calculator_enabled: bool = True,
    ) -> None:
        """Pop up centred above ``anchor``, clamped inside ``screen``.

        ``prefill`` seeds the field - used when text is dropped on the mascot, where he has the
        content but not yet the instruction. ``history`` is the recent prompts, newest first, for
        the Up/Down recall; it is handed in fresh on every open rather than held, so the bubble
        never owns a stale copy of what you have asked. ``calculator_enabled`` follows the same
        rule - the live-computed setting, not a config object the bubble would otherwise have to
        hold onto and keep in sync.
        """
        self._submitting = False
        # Set before anything below that can trigger _on_text_changed (setPlainText included), so
        # a reposition mid-setup - if one fires - has real geometry to work with rather than None.
        self._anchor, self._screen = anchor, screen
        self._calculator_enabled = calculator_enabled
        self._apply_style()
        self._provider = get_provider(provider)
        self._build_modes(self._provider)
        self.set_mode(mode)
        self._input.set_history(history or [])
        self._base_hint = "↑ recent · Enter to send" if history else "Enter to send"
        self._hint.setText(self._base_hint)
        self._preview.hide()
        self._input.setPlainText(prefill)
        if prefill:
            self._input.moveCursor(QTextCursor.Start)
        self._offer_clipboard()
        self._input.refresh_height()
        self.setFixedWidth(self._width)
        self._reposition()

        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus(Qt.OtherFocusReason)

    def _reposition(self) -> None:
        """(Re)place the popup from its CURRENT size - the tail always meets ``self._anchor``.

        Split out of ``show_for`` because the calculator preview changes the bubble's height
        *after* it has already been opened and positioned once (see ``_on_text_changed``), and
        that first placement was computed bottom-up from the height at the time. Without redoing
        the same math on every height change, the popup's top stays put while it grows downward,
        and the tail drifts away from Nib's head instead of staying planted on it.
        """
        if self._anchor is None or self._screen is None:
            return
        anchor, screen = self._anchor, self._screen
        self.adjustSize()
        height = self.sizeHint().height()
        x = anchor.center().x() - self._width // 2
        x = max(screen.left() + 6, min(screen.right() - self._width - 6, x))
        y = anchor.top() - height + self._tail_h // 2
        if y < screen.top() + 6:  # not enough room above - sit below instead
            y = min(anchor.bottom() + 6, screen.bottom() - height - 6)
        self.move(x, y)
        self._tail_x = anchor.center().x() - x
        self.update()

    def _on_text_changed(self) -> None:
        """Live preview: answer a recognisable sum or conversion before you even press Enter.

        Purely visual - nothing here sends anything anywhere, and nothing here is logged (see
        ``calculator.evaluate`` and docs/PRIVACY.md). ``_on_submit`` re-evaluates independently at
        send time rather than trusting whatever this last computed, so the two can never disagree.
        """
        was_visible = self._preview.isVisible()
        calc = None
        if self._calculator_enabled:
            text = self._input.toPlainText()
            if text.strip():
                calc = calculate(text)
        if calc is None:
            self._preview.hide()
            self._hint.setText(self._base_hint)
        else:
            self._preview.setText(calc.result)
            self._preview.setStyleSheet(f"color: {ACCENT if calc.ok else self._theme['muted']};")
            self._preview.show()
            self._hint.setText("Enter to calculate")
        if self._preview.isVisible() != was_visible:
            self._reposition()

    def _offer_clipboard(self) -> None:
        """Show a one-tap chip if the clipboard holds usable text."""
        text = ""
        app = QApplication.instance()
        if app is not None:
            try:
                text = (app.clipboard().text() or "").strip()
            except Exception:
                log.debug("could not read the clipboard", exc_info=True)
        self._clipboard_text = text
        if not text:
            self._clip.hide()
            return
        summary = f"{len(text)} chars" if len(text) > 60 else text.splitlines()[0][:28]
        self._clip.setText(f"Paste clipboard ({summary})")
        self._clip.show()

    def _paste_clipboard(self) -> None:
        if not self._clipboard_text:
            return
        existing = self._input.toPlainText()
        joined = f"{existing.rstrip()}\n{self._clipboard_text}" if existing.strip() else self._clipboard_text
        self._input.setPlainText(joined[:self._max_chars()])
        self._input.moveCursor(QTextCursor.End)
        self._input.setFocus(Qt.OtherFocusReason)
        self._clip.hide()

    def _on_submit(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return  # empty send is a no-op; keep the bubble open
        calc = calculate(text) if self._calculator_enabled else None
        if calc is not None:
            # Never reaches the assistant at all - see calculator.evaluate's own docstring on why
            # a recognised-but-broken calculation is answered inline rather than being sent as a
            # chat prompt. _submitting suppresses the dismissed-as-a-poke signal exactly the way a
            # real send does: getting an answer is not walking away empty-handed.
            self._submitting = True
            self.close()
            self.calculated.emit(calc.text, calc.result, calc.ok)
            return
        mode = self.mode
        self._submitting = True
        # Close BEFORE handing off, so the popup's mouse/keyboard grab is released first. The
        # handler shells out to launch the deep link, and that must not run while a grab is held.
        self.close()
        self.submitted.emit(mode, text)

    def close_silently(self) -> None:
        """Close without reporting a dismissal.

        For when the *app* hides the bubble (a fullscreen app appeared, we are shutting down)
        rather than the user dismissing it. A programmatic close is not a poke, and treating it as
        one would anger the mascot for something the user never did.
        """
        if self.isVisible():
            self._suppress_dismiss = True
            self.close()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        suppressed = self._suppress_dismiss
        self._suppress_dismiss = False
        if not self._submitting and not suppressed:
            self.dismissed.emit()


class Toast(QWidget):
    """Small transient message; clicking it runs an optional callback."""

    def __init__(self, ui_scale: float = 1.0) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._s = max(1.0, float(ui_scale))

        layout = QVBoxLayout(self)
        pad = int(12 * self._s)
        layout.setContentsMargins(pad, int(9 * self._s), pad, int(9 * self._s))
        layout.setSpacing(int(2 * self._s))
        self._label = QLabel("", self)
        self._label.setWordWrap(True)
        self._sub = QLabel("", self)
        self._sub.setObjectName("sub")
        self._sub.setWordWrap(True)
        layout.addWidget(self._label)
        layout.addWidget(self._sub)

        self._on_click = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(
        self,
        text: str,
        anchor: QRect,
        screen: QRect,
        *,
        sub: str = "",
        duration_ms: int = 6000,
        on_click=None,
    ) -> None:
        t = _theme()
        s = self._s
        self.setStyleSheet(
            f"""
            QWidget {{ background: {t['bg']}; color: {t['text']};
                       border: 1px solid {t['border']};
                       border-radius: {int(10 * s)}px; font-size: {int(12 * s)}px; }}
            QLabel {{ border: none; background: transparent; }}
            QLabel#sub {{ color: {t['muted']}; font-size: {int(11 * s)}px; }}
            """
        )
        self._label.setText(text)
        self._sub.setText(sub)
        self._sub.setVisible(bool(sub))
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor if on_click else Qt.ArrowCursor)
        self.setFixedWidth(int(260 * s))
        self.adjustSize()

        x = anchor.center().x() - self.width() // 2
        x = max(screen.left() + 6, min(screen.right() - self.width() - 6, x))
        y = max(screen.top() + 6, anchor.top() - self.height() - 6)
        self.move(x, y)
        self.show()
        self.raise_()
        self._timer.start(max(500, duration_ms))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        callback = self._on_click
        self.hide()
        if callback is not None:
            try:
                callback()
            except Exception:
                log.exception("toast click handler failed")
        event.accept()
