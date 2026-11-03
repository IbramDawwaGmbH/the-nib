"""The settings window.

There are 66 settings and, until this existed, five of them had a control. Everything else meant
finding ``%APPDATA%\\NibMascot\\config.json``, editing JSON by hand, and restarting - which is
fine for the person who wrote it and hostile to everybody else.

**Schema-driven, deliberately.** The form is built from ``config.DEFAULTS`` and ``config._BOUNDS``
plus the ``GROUPS`` table below, so a new setting appears here by virtue of existing. The
alternative - a hand-built form - drifts the moment anyone adds a key, and this project already
checks that every key has a README row precisely because that class of drift is the norm.

Types come from the default *value*: a bool becomes a checkbox, an int or float with bounds becomes
a spin box clamped to them, a key with a choice list becomes a combo, and text becomes a line edit.
Nothing here needs to know what any individual setting means.

**What applies immediately.** Almost everything, because ``Behavior`` and ``World`` read the config
live on every tick rather than caching it. The exceptions are listed in ``NEEDS_RESTART`` and
labelled in the window, which is more honest and an order of magnitude less code than trying to
rebuild the sprite sheet and re-register the hotkey from under a running mascot.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import config as cfg_module

log = logging.getLogger(__name__)

#: Settings that only take effect next launch. Shown with a note rather than silently ignored.
NEEDS_RESTART = frozenset({"sprite_scale", "hotkey", "come_hotkey", "pocket_hotkey",
                           "mic_toggle_hotkey", "guard_hotkey", "log_level"})

#: Keys with a fixed set of values -> the values. Anything here becomes a combo box.
CHOICES: dict[str, tuple[str, ...]] = {
    "provider": cfg_module._VALID_PROVIDERS,
    "default_mode": cfg_module._VALID_MODES,
    "personality": cfg_module._VALID_PERSONALITIES,
    "guard_auth": cfg_module._VALID_GUARD_AUTH,
    "guard_scene": cfg_module._VALID_GUARD_SCENES,
    "log_level": ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
}

#: Never shown. Latches he writes himself, remembered positions, and the two mood counters - all
#: of them meaningless to set by hand, and three of them actively confusing (clearing a latch
#: replays a once-a-day moment). ``prompt_history`` and ``costume_recents`` are here because they
#: are lists: the history gets a proper Clear button below instead of a text box full of your own
#: prompts, and the recents are the wardrobe's to write. ``persona_apps`` because it is a free-form
#: exe-name -> animation map with no sensible generic control, and an empty "Other" tab claiming
#: to hold it is worse than the README telling you to edit the file.
HIDDEN = frozenset({
    "config_version", "last_x", "last_monitor", "last_prompt", "prompt_history",
    "costume_recents", "persona_apps", "last_seen_date", "last_clock_off_date", "last_week_over_week",
    "mood_spirits", "mood_saved_at", "paused", "launch_on_startup",
    # A scrypt hash, not something anyone can type - the first guard sets it, clearing it in
    # config.json resets it. A text box holding it would only invite pasting a PIN in plain text.
    "guard_pin_hash",
})

#: Tab -> the keys on it, in order. Anything in DEFAULTS that is neither here nor in HIDDEN turns
#: up on the last tab, so a newly added setting is never silently unreachable.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Behaviour", (
        "wander_enabled", "wander_min_interval_s", "wander_max_interval_s",
        "walk_min_px", "walk_max_px", "walk_speed_px_s", "home_fraction",
        "roam_all_monitors", "hide_on_fullscreen",
    )),
    ("Antics", (
        "antics_enabled", "antic_chance", "fidgets_enabled",
        "eye_track_distance_px", "hover_wave_distance_px",
        "petting_enabled", "pet_hold_ms",
        "react_to_apps", "prompt_reactions", "calculator_enabled",
        "away_after_s", "focus_duration_min", "attention_enabled", "attention_after_s",
        "injury_enabled", "injury_duration_s",
        "mic_indicator_enabled", "camera_indicator_enabled", "talking_while_muted_after_s",
    )),
    ("World", (
        "world_enabled", "workday_end", "seasonal_enabled", "southern_seasons",
        "cpu_reaction_enabled", "cpu_busy_percent", "persona_costumes",
        "play_with_windows",
    )),
    ("Personality", (
        "personality", "annoyance_threshold", "annoyance_window_s", "annoyance_decay_s",
        "sleep_cooldown_s", "mood_enabled", "mood_half_life_h",
    )),
    ("Look and sound", (
        "sprite_scale", "costume", "skin", "shadow_enabled",
        "ghost_mode", "ghost_opacity", "sound_enabled", "sound_volume",
    )),
    ("Guard", (
        "guard_hotkey", "guard_auth", "guard_peek_minutes", "guard_scene", "guard_fog_strength",
        "guard_background_fx", "guard_blackout_after_attempts", "guard_sounds", "guard_auto_after_idle_min",
        "guard_lock_fallback", "windows_unlock_clears_guard",
    )),
    ("Shortcuts", (
        "provider", "default_mode", "hotkey", "come_hotkey", "pocket_hotkey",
        "mic_toggle_hotkey", "tool_wheel_enabled", "quick_prompt",
        "prompt_history_max", "intro_seen", "log_level",
    )),
)

#: One line each, shown under the control. Kept short - the README is the reference, this is the
#: reminder. Missing entries simply show nothing.
HELP: dict[str, str] = {
    "wander_min_interval_s": "The beat between activities, not a rest. Raise it to calm him down.",
    "walk_speed_px_s": "0 derives it from his stride, which is what stops his feet sliding.",
    "home_fraction": "Where he stands with no remembered position. 0 = far left, 1 = far right.",
    "antic_chance": "Chance of an antic rather than a walk after each beat.",
    "fidgets_enabled": "The very short beats: a scratch, a foot tap, a glance at a watch.",
    "petting_enabled": "Rest the pointer on him and he leans into it. The only way to be kind.",
    "pet_hold_ms": "How long the pointer must sit still on him first.",
    "prompt_reactions": "React to the shape of what you asked. Read from your own bubble only.",
    "calculator_enabled": "Answer a plain sum or unit conversion inline, never sent anywhere.",
    "away_after_s": "Idle seconds before he sits down to wait. 0 disables the greeting too.",
    "focus_duration_min": "How long a session runs, started from the tray. Read once when you "
                          "start it - changing this mid-session only affects the next one.",
    "attention_after_s": "First stage. The wave is at 2x this and giving up at 3.5x.",
    "mic_indicator_enabled": "Show when your microphone is live or muted. State only - never "
                             "audio, and never which app.",
    "camera_indicator_enabled": "Show when your camera is live. An indicator only: Windows has "
                                "no global camera mute to offer.",
    "talking_while_muted_after_s": "How long you must be talking into a muted mic before he says "
                                   "so. He then holds his tongue for a minute.",
    "tool_wheel_enabled": "Right-click him for a wheel of his tools. Off, or Shift+right-click, "
                          "gives the full menu instead.",
    "mic_toggle_hotkey": "Mutes and unmutes your microphone. Empty by default so it cannot "
                         "collide with a shortcut your call app already uses.",
    "workday_end": '"16:00", or "" to disable knocking-off and the Friday flop.',
    "cpu_busy_percent": "Aggregate load across all cores, so one pegged core is far below this.",
    "play_with_windows": "Peeking over the taskbar, and heaving at a window overhead.",
    "personality": "A lens over the numbers on this page. Your own values are kept.",
    "mood_enabled": "Let a good or bad stretch carry over between sessions.",
    "mood_half_life_h": "How long a good or bad turn takes to half fade.",
    "sprite_scale": "0 picks an integer scale from your monitor's DPI.",
    "shadow_enabled": "The soft contact shadow under his feet.",
    "ghost_mode": "Faded and click-through. Visible, and completely out of the way.",
    "sound_enabled": "Nothing plays until you run tools/gen_sounds.py.",
    "provider": "Which assistant he launches. Claude uses its desktop app; the others open in "
                "your browser.",
    "default_mode": "Only Claude has more than one mode.",
    "hotkey": 'Global shortcut for the bubble. "" disables it.',
    "come_hotkey": 'Calls him to your pointer. "" disables it; the tray entry always works.',
    "pocket_hotkey": 'Grabs the clipboard into his pocket; a click or the main hotkey empties it '
                      'into the bubble. "" disables it.',
    "quick_prompt": "Double-click sends this. Empty falls back to your last prompt.",
    "prompt_history_max": "How far Up in the bubble reaches back. 0 disables it and clears it.",
    "intro_seen": "Untick to see the first-run tips again next launch.",
    "guard_hotkey": 'Covers every screen until you unlock. "" disables it; the wheel and tray '
                    'still work.',
    "guard_auth": "hello uses Windows Hello when this PC has it, with your guard PIN as backup.",
    "guard_peek_minutes": "How long Peek lifts the guard before it comes back on its own.",
    "guard_scene": "both, eyes (no Nib on stage) or nib (no giant eyes).",
    "guard_fog_strength": "1-5. Even 1 blurs text past reading; 5 leaves only colours.",
    "guard_background_fx": "Worker bots, dust, a camera scan line and REC in the fog. Off = plain fog.",
    "guard_blackout_after_attempts": "Wrong PINs before every screen goes black.",
    "guard_auto_after_idle_min": "Guard by itself after this long idle. 0 = never.",
    "guard_lock_fallback": "Ten wrong PINs locks Windows. Background work keeps running.",
    "windows_unlock_clears_guard": "Unlocking Windows ends the guard - the way in if you forget "
                                   "the PIN.",
}

#: Prettier than the raw key, and computed rather than tabled so a new setting still reads well.
_RENAMES = {
    "px": "px", "ms": "ms", "s": "seconds", "h": "hours", "cpu": "CPU", "dpi": "DPI",
    "min": "minutes",
}


def _label_for(key: str) -> str:
    words = key.split("_")
    if words[-1] in _RENAMES and len(words) > 1:
        words[-1] = _RENAMES[words[-1]]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


class _Field:
    """One setting's widget, plus how to read and write it."""

    def __init__(self, key: str, widget: QWidget, read, write) -> None:
        self.key = key
        self.widget = widget
        self.read = read
        self.write = write


class SettingsWindow(QDialog):
    """The whole settings file, as a form.

    Emits :attr:`changed` after applying, with the set of keys that actually moved, so the app can
    react to the handful that need more than a config write (the costume, the colour, ghost mode).
    """

    changed = Signal(set)
    history_cleared = Signal()

    def __init__(self, config, *, costumes: list[str], skins: list[str], parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Nib settings")
        self.setMinimumSize(520, 560)
        self._fields: dict[str, _Field] = {}

        # Options that come from what is actually installed rather than from a fixed table.
        self._choices = dict(CHOICES)
        self._choices["costume"] = ("", *costumes)
        self._choices["skin"] = tuple(skins) or ("default",)

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs, 1)

        placed: set[str] = set()
        for title, keys in GROUPS:
            shown = [k for k in keys if k in cfg_module.DEFAULTS and k not in HIDDEN]
            placed.update(shown)
            tabs.addTab(self._build_tab(shown), title)

        # Anything added to DEFAULTS but not to GROUPS. Not an error - it just has to be
        # reachable, or the next person to add a setting quietly ships one nobody can find.
        # Only if it produces actual controls, though: a key with no generic widget (a dict, a
        # list) would otherwise mint an empty tab, which claims to hold something and does not.
        leftover = sorted(set(cfg_module.DEFAULTS) - placed - HIDDEN)
        if leftover:
            before = set(self._fields)
            page = self._build_tab(leftover)
            if set(self._fields) - before:
                log.info("settings not assigned to a tab, showing under Other: %s", leftover)
                tabs.addTab(page, "Other")
            else:
                log.info("settings with no editable control: %s", leftover)
                page.deleteLater()

        note = QLabel(
            "Changes apply as soon as you click Apply. The sprite scale, the shortcuts and the "
            "log level take effect next launch."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        row = QHBoxLayout()
        clear = QPushButton("Clear prompt history…", self)
        clear.clicked.connect(self._on_clear_history)
        row.addWidget(clear)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Apply | QDialogButtonBox.Cancel
            | QDialogButtonBox.RestoreDefaults,
            self,
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._on_restore)
        layout.addWidget(buttons)

        self.reload()

    # --- building -------------------------------------------------------------------

    def _build_tab(self, keys: list[str]) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        for key in keys:
            field = self._build_field(key)
            if field is None:
                continue
            self._fields[key] = field
            label = _label_for(key)
            if key in NEEDS_RESTART:
                label += " *"
            help_text = HELP.get(key, "")
            if help_text:
                field.widget.setToolTip(help_text)
            form.addRow(label, field.widget)
            if help_text:
                # A row of its own, spanning BOTH columns. Nesting the hint under the control in a
                # QVBoxLayout was the obvious thing and it laid out wrong: a word-wrapped QLabel's
                # height depends on its width, that dependency does not survive the nested layout,
                # and every hint ended up clipped to one line and overlapping the field below it.
                # Spanning the full width also means most hints no longer need to wrap at all.
                form.addRow(self._hint(help_text))

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        label.setIndent(6)
        font = QFont(label.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        label.setFont(font)
        label.setStyleSheet("color: palette(mid); margin-bottom: 4px;")
        return label

    def _build_field(self, key: str) -> _Field | None:
        """One widget, chosen from the *type of the default value*, not from a per-key table."""
        default = cfg_module.DEFAULTS[key]
        low, high = cfg_module._BOUNDS.get(key, (None, None))

        if key in self._choices:
            widget = QComboBox(self)
            for value in self._choices[key]:
                widget.addItem(value or "(none)", value)
            return _Field(
                key, widget,
                lambda w=widget: w.currentData(),
                lambda v, w=widget: w.setCurrentIndex(max(0, w.findData(str(v)))),
            )

        if isinstance(default, bool):
            widget = QCheckBox(self)
            return _Field(key, widget, widget.isChecked, lambda v, w=widget: w.setChecked(bool(v)))

        if isinstance(default, float):
            widget = QDoubleSpinBox(self)
            widget.setDecimals(2)
            widget.setSingleStep(0.05 if (high or 1) <= 2 else 0.5)
            widget.setRange(float(low) if low is not None else -1e6,
                            float(high) if high is not None else 1e6)
            return _Field(key, widget, widget.value, lambda v, w=widget: w.setValue(float(v)))

        if isinstance(default, int):
            widget = QSpinBox(self)
            widget.setRange(int(low) if low is not None else -1_000_000,
                            int(high) if high is not None else 1_000_000)
            return _Field(key, widget, widget.value, lambda v, w=widget: w.setValue(int(v)))

        if isinstance(default, str):
            widget = QLineEdit(self)
            return _Field(key, widget, widget.text, lambda v, w=widget: w.setText(str(v)))

        # Dicts and lists (persona_apps, prompt_history) have no sensible generic control. They
        # stay editable in the file, and this window simply does not claim otherwise.
        return None

    # --- values ---------------------------------------------------------------------

    def reload(self) -> None:
        """Pull every control's value back from the config."""
        for key, field in self._fields.items():
            try:
                field.write(self._config[key])
            except Exception:
                log.debug("could not show %s", key, exc_info=True)

    def apply(self) -> set[str]:
        """Write everything back. Returns the keys whose stored value actually changed."""
        moved: set[str] = set()
        for key, field in self._fields.items():
            before = self._config[key]
            try:
                self._config[key] = field.read()
            except Exception:
                log.debug("could not store %s", key, exc_info=True)
                continue
            if self._config[key] != before:
                moved.add(key)
        # Reloaded so coercion and clamping are visible: type "99" into ghost opacity and the box
        # should show what was actually kept, not what you typed.
        self.reload()
        if moved:
            log.info("settings changed: %s", ", ".join(sorted(moved)))
            self._config.save()
            self.changed.emit(moved)
        return moved

    def _on_ok(self) -> None:
        self.apply()
        self.accept()

    def _on_restore(self) -> None:
        answer = QMessageBox.question(
            self, "Nib",
            "Put every setting on this page back to its default?\n\n"
            "Your costume, colour and remembered position are not touched.",
        )
        if answer != QMessageBox.Yes:
            return
        for key, field in self._fields.items():
            if key in ("costume", "skin"):
                continue
            field.write(cfg_module.DEFAULTS[key])

    def _on_clear_history(self) -> None:
        count = len(self._config["prompt_history"])
        if not count:
            QMessageBox.information(self, "Nib", "There is nothing stored.")
            return
        answer = QMessageBox.question(
            self, "Nib",
            f"Forget the {count} prompt(s) Nib has remembered?\n\n"
            "They are only stored on this machine, in config.json.",
        )
        if answer != QMessageBox.Yes:
            return
        self._config.clear_prompt_history()
        self._config.save()
        self.history_cleared.emit()
        QMessageBox.information(self, "Nib", "Forgotten.")
