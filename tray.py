"""System tray icon and menu.

Emits intent rather than acting: ``main`` owns the config and the mascot, so the tray just reports
what the user asked for. Checkable items are kept in sync via :meth:`Tray.refresh`, which matters
for the startup toggle - writing the registry can fail, and the menu should then show the truth
rather than what was clicked.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

log = logging.getLogger(__name__)


class Tray(QObject):
    wander_toggled = Signal(bool)
    startup_toggled = Signal(bool)
    pause_toggled = Signal(bool)
    reset_position_requested = Signal()
    quit_requested = Signal()
    costume_chosen = Signal(str)  # "" means bare-headed
    skin_chosen = Signal(str)
    ghost_toggled = Signal(bool)
    focus_toggled = Signal(bool)
    come_here_requested = Signal()
    settings_requested = Signal()
    tips_requested = Signal()
    personality_chosen = Signal(str)
    surprise_requested = Signal()
    do_something_requested = Signal()
    treat_requested = Signal()
    measure_requested = Signal()
    mic_toggle_requested = Signal()
    guard_requested = Signal()

    #: What the tray icon's tooltip says until main._update_presence first runs. From then on it
    #: always has something more specific to say - a focus countdown, a live mic, or simply his
    #: name and how he is - so this is a fallback, not the usual text.
    DEFAULT_TOOLTIP = "Nib - click him to ask Claude something"

    def __init__(
        self,
        icon: QIcon,
        parent: QObject | None = None,
        *,
        costumes: dict[str, list[str]] | list[str] | None = None,
        skins: list[str] | None = None,
        personalities: list[str] | None = None,
        costume_labels: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        #: name -> what the menu calls it, where that is not just the name tidied up: "14 · Pi Day".
        self._costume_labels = dict(costume_labels or {})
        # Accepts either the grouped mapping the sheet now provides or a plain list, so a caller
        # (or a test) holding an older flat list still gets a working menu.
        if isinstance(costumes, dict):
            self._costumes = costumes
        else:
            self._costumes = {"": list(costumes or [])}
        self._skins = skins or []
        self._personalities = personalities or []
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(self.DEFAULT_TOOLTIP)

        menu = QMenu()
        self._menu = menu

        # First, because it is the one entry that is useful when you cannot find him - which is
        # the situation you are most likely to be in when you go looking for this menu.
        come = QAction("Come here", menu)
        come.triggered.connect(self.come_here_requested.emit)
        menu.addAction(come)

        # Next to it because they are the same kind of thing: the entries that make him do
        # something now rather than whenever he gets round to it.
        do = QAction("Do something", menu)
        do.setToolTip("One of his rarer animations, now - a sneeze, a stumble, hiccups, a spin")
        do.triggered.connect(self.do_something_requested.emit)
        menu.addAction(do)

        treat = QAction("Give a treat", menu)
        treat.setToolTip("A biscuit. Three a day count - after that he is too full")
        treat.triggered.connect(self.treat_requested.emit)
        menu.addAction(treat)

        menu.addSeparator()

        self._wander = QAction("Wander", menu, checkable=True)
        self._wander.toggled.connect(self.wander_toggled.emit)
        menu.addAction(self._wander)

        self._focus = QAction("Start focus", menu, checkable=True)
        self._focus.setToolTip(
            "Holds him at a working pose for the length set in Settings -> Antics. "
            "Pauses automatically while you're away."
        )
        self._focus.toggled.connect(self._on_focus_toggled)
        menu.addAction(self._focus)

        self._pause = QAction("Pause", menu, checkable=True)
        self._pause.toggled.connect(self._on_pause_toggled)
        menu.addAction(self._pause)

        self._ghost = QAction("Ghost mode", menu, checkable=True)
        self._ghost.setToolTip("Faded and click-through - visible, but completely out of the way")
        self._ghost.toggled.connect(self.ghost_toggled.emit)
        menu.addAction(self._ghost)

        menu.addSeparator()

        self._costume_actions = self._build_grouped_choice_menu(
            menu, "Costume", self._costumes, self._on_costume,
            first="(none)", surprise="Surprise me",
        )
        self._skin_actions = self._build_choice_menu(
            menu, "Colour", self._skins, self._on_skin
        )
        self._personality_actions = self._build_choice_menu(
            menu, "Personality", self._personalities, self.personality_chosen.emit
        )

        menu.addSeparator()

        settings = QAction("Settings…", menu)
        settings.triggered.connect(self.settings_requested.emit)
        menu.addAction(settings)

        tips = QAction("Show tips", menu)
        tips.triggered.connect(self.tips_requested.emit)
        menu.addAction(tips)

        self._startup = QAction("Launch on Windows startup", menu, checkable=True)
        self._startup.toggled.connect(self.startup_toggled.emit)
        menu.addAction(self._startup)

        reset = QAction("Reset position", menu)
        reset.triggered.connect(self.reset_position_requested.emit)
        menu.addAction(reset)

        measure = QAction("Measure", menu)
        measure.setToolTip("Drag anywhere on screen for a reading in pixels, inches, and cm")
        measure.triggered.connect(self.measure_requested.emit)
        menu.addAction(measure)

        guard = QAction("Guard the screen", menu)
        guard.setToolTip("Fog over every monitor until you unlock with Windows Hello or your "
                         "guard PIN. Background work keeps running.")
        guard.triggered.connect(self.guard_requested.emit)
        menu.addAction(guard)

        #: Relabelled from main._tick_devices to match the live state, and hidden outright on a
        #: machine with no microphone - an entry that cannot do anything is worse than no entry.
        self._mic = QAction("Mute microphone", menu)
        self._mic.triggered.connect(self.mic_toggle_requested.emit)
        self._mic.setVisible(False)
        menu.addAction(self._mic)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    def set_mic_state(self, muted: bool | None) -> None:
        """Relabel the mute entry, or hide it when there is no microphone to talk about."""
        if muted is None:
            self._mic.setVisible(False)
            return
        self._mic.setVisible(True)
        self._mic.setText("Unmute microphone" if muted else "Mute microphone")

    def _on_activated(self, reason) -> None:
        """Show the menu on a left click.

        This deliberately does NOT toggle pause. It used to, and that was a trap: a stray click on
        the tray icon froze the mascot silently, the state persisted across restarts, and the only
        clue was a log line. Pausing should take a deliberate menu choice.
        """
        if reason == QSystemTrayIcon.Trigger:
            self._menu.popup(QCursor.pos())

    def popup_for_mascot(self, position, extra_actions: list[QAction] | None = None) -> None:
        """Show the exact same menu the tray icon shows - so the whole thing is reachable by
        right-clicking him directly, not just by hunting for an icon that Windows might have
        tucked into the overflow tray.

        ``extra_actions`` are pinned above it for this one showing only - the couple of things
        that only make sense right on him (asking, repeating the last prompt, hiding him for a
        bit) rather than on the tray icon. They are removed, and deleted, the moment the menu
        closes, so the persistent tray menu is never left holding onto mascot-only actions
        between one right-click and the next.
        """
        removable: list[QAction] = list(extra_actions or [])
        if removable:
            first = self._menu.actions()[0] if self._menu.actions() else None
            for action in removable:
                self._menu.insertAction(first, action)
            removable.append(self._menu.insertSeparator(first))

            def _cleanup() -> None:
                for action in removable:
                    self._menu.removeAction(action)
                    action.deleteLater()
                self._menu.aboutToHide.disconnect(_cleanup)

            self._menu.aboutToHide.connect(_cleanup)
        self._menu.popup(position)

    def _build_grouped_choice_menu(
        self, parent: QMenu, title: str, groups: dict, handler, *,
        first: str = "", surprise: str = ""
    ) -> dict[str, QAction]:
        """A radio-style menu split into category submenus, exclusive ACROSS all of them.

        One flat list was right for eight costumes and unusable at fifty - it would run off the
        screen. The exclusivity has to span the whole tree rather than each submenu, or picking a
        hat would leave the tick on whatever glasses were chosen before it.

        ``first`` is an ungrouped entry pinned at the top, which is where "(none)" belongs: taking
        the costume off is not a category of costume.
        """
        actions: dict[str, QAction] = {}
        if not groups:
            return actions
        submenu = parent.addMenu(title)
        group = QActionGroup(submenu)
        group.setExclusive(True)

        if surprise:
            # An action rather than a radio choice, and outside the exclusive group: "surprise me"
            # is a thing you DO, not a thing you can be currently set to.
            shuffle = QAction(surprise, submenu)
            shuffle.triggered.connect(self.surprise_requested.emit)
            submenu.addAction(shuffle)
            submenu.addSeparator()

        if first:
            action = QAction(first, submenu, checkable=True)
            action.triggered.connect(lambda _c=False, o=first: handler(o))
            group.addAction(action)
            submenu.addAction(action)
            actions[first] = action
            submenu.addSeparator()

        # A category with a slash nests: "Calendar/March" is the March submenu inside Calendar,
        # which is how 366 costumes of the day fit without burying the hats.
        parents: dict[str, QMenu] = {}
        for category, options in groups.items():
            if not options:
                continue
            if len(groups) <= 1:
                target = submenu
            elif "/" in str(category):
                outer, inner = str(category).split("/", 1)
                if outer not in parents:
                    parents[outer] = submenu.addMenu(outer)
                target = parents[outer].addMenu(inner)
            else:
                target = submenu.addMenu(str(category))
            for option in options:
                label = self._costume_labels.get(option) or option.replace("_", " ").title()
                action = QAction(label, target, checkable=True)
                action.triggered.connect(lambda _c=False, o=option: handler(o))
                group.addAction(action)
                target.addAction(action)
                actions[option] = action
        return actions

    def _build_choice_menu(
        self, parent: QMenu, title: str, options: list[str], handler
    ) -> dict[str, QAction]:
        """An exclusive radio-style submenu. Returns the actions keyed by option label."""
        actions: dict[str, QAction] = {}
        if not options:
            return actions
        submenu = parent.addMenu(title)
        group = QActionGroup(submenu)
        group.setExclusive(True)
        for option in options:
            action = QAction(option.replace("_", " ").title(), submenu, checkable=True)
            action.triggered.connect(lambda _checked=False, o=option: handler(o))
            group.addAction(action)
            submenu.addAction(action)
            actions[option] = action
        return actions

    def _on_costume(self, option: str) -> None:
        self.costume_chosen.emit("" if option == "(none)" else option)

    def _on_skin(self, option: str) -> None:
        self.skin_chosen.emit(option)

    def _on_pause_toggled(self, paused: bool) -> None:
        self._pause.setText("Resume" if paused else "Pause")
        self.pause_toggled.emit(paused)

    def _on_focus_toggled(self, active: bool) -> None:
        self._focus.setText("Stop focus" if active else "Start focus")
        self.focus_toggled.emit(active)

    def set_tooltip(self, text: str) -> None:
        self._tray.setToolTip(text)

    def set_icon(self, icon: QIcon) -> None:
        self._tray.setIcon(icon)

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def refresh(
        self,
        *,
        wander: bool,
        paused: bool,
        startup: bool,
        costume: str | None = None,
        skin: str | None = None,
        ghost: bool = False,
        personality: str | None = None,
        focus: bool | None = None,
    ) -> None:
        """Set the checkmarks without re-emitting the toggled signals.

        ``focus`` is None-skippable like costume/skin/personality below, deliberately NOT bundled
        into the unconditional four next to it: a call site that refreshes wander/paused/startup
        without also passing ghost already silently unchecks Ghost mode whenever that happens
        (see _on_startup_toggled in main.py) - a real, pre-existing gap this file is not otherwise
        touching. A live session must not go the same way just because some future caller forgets
        to pass focus=.
        """
        for action, value in (
            (self._wander, wander),
            (self._pause, paused),
            (self._ghost, ghost),
            (self._startup, startup),
        ):
            action.blockSignals(True)
            action.setChecked(bool(value))
            action.blockSignals(False)
        self._pause.setText("Resume" if paused else "Pause")

        if focus is not None:
            self._focus.blockSignals(True)
            self._focus.setChecked(focus)
            self._focus.blockSignals(False)
            self._focus.setText("Stop focus" if focus else "Start focus")

        if costume is not None:
            self._check_choice(self._costume_actions, costume or "(none)")
        if skin is not None:
            self._check_choice(self._skin_actions, skin)
        if personality is not None:
            self._check_choice(self._personality_actions, personality)

    @staticmethod
    def _check_choice(actions: dict[str, QAction], selected: str) -> None:
        for option, action in actions.items():
            action.blockSignals(True)
            action.setChecked(option == selected)
            action.blockSignals(False)

    def notify(self, title: str, message: str) -> None:
        try:
            self._tray.showMessage(title, message, QSystemTrayIcon.Information, 5000)
        except Exception:
            log.debug("tray notification failed", exc_info=True)
