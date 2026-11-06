"""Plays Nib's small noises. Off by default, and silent about every possible failure.

Two rules shape the whole module:

*   **No audio ships in the repository.** The WAVs are synthesised by ``tools/gen_sounds.py``, the
    same way the sprites are. An installation that has never run it simply has no sounds, and that
    has to be a no-op rather than an error.
*   **Nothing here may ever raise.** This is decoration on a mascot. A missing file, a machine with
    no audio device, an RDP session, a Qt build without the multimedia backend - every one of those
    ends in silence and a debug line, never in an exception reaching the tick.

Uses ``QSoundEffect``, which is non-blocking, gives real volume control, and can overlap sounds.
``winsound`` was the alternative and would have meant no volume control at all, since it plays at
whatever the system mixer says - which for a feature whose main risk is being too loud is exactly
the wrong trade.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: Events the rest of the app can ask for. Anything not in this set is ignored rather than being
#: allowed to reach the filesystem as a path fragment.
EVENTS = frozenset({
    "open", "send", "poke", "grump", "land", "pet", "sleep", "wake",
    # the involuntary lot, and the spin
    "sneeze", "trip", "hiccup", "spin",
    # the one deliberately celebratory sound in the set - see cheer in tools/gen_sounds.py
    "cheer",
    # a focus session completing on its own - deliberately distinct from cheer, see gen_sounds.py
    "focus_done",
    # eating a treat
    "nom",
})

#: Shortest gap between two plays of the *same* event. He can bounce three times in half a second,
#: and three thuds in half a second is a bug that sounds like a machine gun.
_REPEAT_GUARD_MS = 140.0


class Sounds:
    """Loads what exists, plays it when asked, and stays quiet about the rest."""

    def __init__(self, directory: Path, config) -> None:
        self._dir = Path(directory)
        self._config = config
        self._effects: dict[str, object] = {}
        self._last_played: dict[str, float] = {}
        self._clock_ms = 0.0
        self._available = False
        self._effect_class = None

        if not self._dir.is_dir():
            log.info("no sounds at %s - run tools/gen_sounds.py to create them", self._dir)
            return
        # The multimedia backend is NOT imported here. It costs 2.5MB of working set, measured,
        # and this feature is off by default - so an installation that never switches it on
        # should never pay for it. It is loaded on the first sound that actually plays.
        self._available = True

    def _load_backend(self) -> bool:
        """Import Qt's multimedia layer, once, on demand. False means silence from here on."""
        if self._effect_class is not None:
            return self._effect_class is not False
        try:
            from PySide6.QtMultimedia import QSoundEffect
        except Exception:
            log.info("Qt multimedia is unavailable; sound is off", exc_info=True)
            self._effect_class = False
            return False
        self._effect_class = QSoundEffect
        return True

    # --- state --------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when there are sound files to play. The backend loads on the first one."""
        return self._available

    @property
    def enabled(self) -> bool:
        return self._available and bool(self._config.get("sound_enabled", False))

    def _volume(self) -> float:
        try:
            return max(0.0, min(1.0, float(self._config.get("sound_volume", 0.35))))
        except (TypeError, ValueError):
            return 0.35

    def tick(self, dt_ms: float) -> None:
        """Advance the repeat guard's clock. Driven from the app tick."""
        self._clock_ms += max(0.0, dt_ms)

    # --- playing ------------------------------------------------------------------

    def _effect(self, event: str):
        cached = self._effects.get(event)
        if cached is not None:
            return cached
        path = self._dir / f"{event}.wav"
        if not path.is_file():
            self._effects[event] = False  # remembered, so a missing file is looked up once
            return False
        if not self._load_backend():
            self._effects[event] = False
            return False
        try:
            from PySide6.QtCore import QUrl

            effect = self._effect_class()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            self._effects[event] = effect
            return effect
        except Exception:
            log.debug("could not load %s", path, exc_info=True)
            self._effects[event] = False
            return False

    def play(self, event: str) -> bool:
        """Play a named event. Returns True only if a sound actually started.

        Every branch out of here is silent-and-false rather than an exception: it is called from
        the state machine, and a mascot that crashes because a WAV is missing is a worse outcome
        than one that says nothing.
        """
        if not self.enabled or event not in EVENTS:
            return False
        last = self._last_played.get(event)
        if last is not None and self._clock_ms - last < _REPEAT_GUARD_MS:
            return False
        effect = self._effect(event)
        if not effect:
            return False
        try:
            effect.setVolume(self._volume())
            effect.play()
        except Exception:
            log.debug("could not play %s", event, exc_info=True)
            return False
        self._last_played[event] = self._clock_ms
        return True

    def stop_all(self) -> None:
        for effect in self._effects.values():
            if effect:
                try:
                    effect.stop()
                except Exception:
                    log.debug("could not stop an effect", exc_info=True)
