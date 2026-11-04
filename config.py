"""Persisted settings.

Everything the brief calls tunable lives in ``DEFAULTS``. A missing, empty, or corrupt
``config.json`` is never fatal - it is logged and the defaults are used, because the mascot
failing to start over a bad settings file would be a poor trade. Unknown keys in the file are
preserved across saves so a newer build's settings survive being opened by an older one.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_DIR_NAME = "NibMascot"

#: Bumped when a stored key's *meaning* changes, not merely its default. See ``_MIGRATIONS``.
CONFIG_VERSION = 2

#: version introduced -> keys whose semantics changed at that version. A file older than the
#: version has those keys discarded so they pick up the new defaults. Without this, an existing
#: config silently pins the old behaviour and the change looks like it did not work.
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    # v2 turned the wander interval from "how long he rests before a stroll" (20-60s) into "the
    # short beat between activities" (sub-second). Old values would leave him standing still.
    2: ("wander_min_interval_s", "wander_max_interval_s"),
}

DEFAULTS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    #: Which assistant he launches. See PROVIDERS in launcher.py - adding another is one entry.
    "provider": "claude",
    "default_mode": "chat",
    "wander_enabled": True,
    # He is meant to be busy: these are the short beat *between* activities, not a long rest.
    # Sub-second on purpose - only sleeping actually stops him. Floats, so they can go below 1s.
    "wander_min_interval_s": 0.3,
    "wander_max_interval_s": 1.2,
    "walk_min_px": 120,
    "walk_max_px": 700,
    "walk_speed_px_s": 0,  # 0 = derive from the sprite's walk_step_px (keeps feet gripping)
    # Idle antics: the eye expressions and the little activities.
    "antics_enabled": True,
    "antic_chance": 0.5,
    "eye_track_distance_px": 420,
    # Micro-fidgets: the very short beats - a scratch, a foot tap, checking a watch he does not
    # own. They fill the gaps between the set pieces, which is where he used to just stand.
    "fidgets_enabled": True,
    # Involuntary: a sneeze twice an hour, a stumble three times, and hiccups after you have
    # thrown him about. Scheduled rather than rolled, so the rates mean what they say.
    "quirks_enabled": True,
    # He has an opinion about the costume you just put on him.
    "costume_reactions": True,
    # Scroll the wheel over him and he spins. No reason; it is just funny.
    "scroll_to_spin": True,
    # Reactions to you
    "injury_enabled": True,       # bandaged and dazed after you drop him from a height
    "injury_duration_s": 3.0,
    "react_to_apps": True,
    "away_after_s": 150,          # 0 disables the away/greet behaviour - also the auto-pause
                                  # threshold for a running focus session, below
    #: A session started from the tray. Read once when it starts, so changing this mid-session
    #: only affects the next one.
    "focus_duration_min": 25,
    #: Whether he shows that something is listening or watching. Reads state only - never audio,
    #: never video, never which app - see devices.py. The camera is an indicator and nothing more:
    #: Windows exposes no global camera mute for it to offer.
    "mic_indicator_enabled": True,
    "camera_indicator_enabled": True,
    #: How long you have to be talking into a muted microphone before he mentions it. The point of
    #: a delay at all is that a mic goes live a moment before you start speaking, and a tell that
    #: fires on that instant would go off every time anything touched the microphone.
    "talking_while_muted_after_s": 2.0,
    # Hold the pointer still on him and he leans into it. The only way to be *kind* to him, and
    # the only thing besides sending a prompt that takes annoyance back off.
    "petting_enabled": True,
    "pet_hold_ms": 550,           # how long the pointer must sit still on him first
    # He reacts to the shape of what you just asked - long, a bug report, a thank-you. Read from
    # the text you typed into his own bubble; nothing else is inspected and nothing is logged.
    "prompt_reactions": True,
    # A safe local parser checks what you typed for a plain sum or unit conversion before it is
    # sent anywhere; when it recognises one it answers inline instead of opening the assistant.
    # No network call, ever, and currency is deliberately not included - see docs/PRIVACY.md.
    "calculator_enabled": True,
    # World-awareness: the clock, the calendar, the season, and how hard the machine is working.
    # Nothing here touches the network or the filesystem - see world.py.
    "world_enabled": True,        # master switch for everything in this block
    "workday_end": "16:00",       # "" disables the knocking-off and week's-over moments
    "seasonal_enabled": True,
    "southern_seasons": False,    # flip the seasons for the southern hemisphere
    "cpu_reaction_enabled": True,
    "cpu_busy_percent": 70,       # sustained system-wide load that makes him fan himself
    #: A separate switch from cpu_reaction_enabled: that one is about being pinned RIGHT NOW, this
    #: one is a one-off celebration once a long stretch of it ends - a render, build, export or
    #: install finishing.
    "task_finished_reaction_enabled": True,
    "persona_costumes": True,     # let an app persona put temporary headwear on him
    #: He owns fifty-odd costumes and, left alone, wears none of them. This dresses him when there
    #: is a REASON to - a beanie all winter, a hard hat while the machine is pinned, a party hat
    #: for the knocking-off dance. Never overrides a costume you picked yourself.
    "auto_costumes": True,
    #: exe name -> animation state, merged over the built-in personas. Yours wins on a clash.
    "persona_apps": {},
    # Playing with what is around him: ducking behind the taskbar to peek, and heaving at the
    # underside of a window hanging just overhead. Strictly read-only - he reads where a window
    # is and reacts to it, and there is no code anywhere that moves one.
    "play_with_windows": True,
    # Being ignored: you are at the machine but none of what you are doing involves him. Three
    # stages - he glances over, waves once, then gives up and sits down - and it never escalates
    # past that. Suppressed while you are away, while he is asleep or cross, and while the
    # machine is pinned.
    "attention_enabled": True,
    "attention_after_s": 240,     # first stage; the wave is at 2x this and giving up at 3.5x
    # How the day went. Unlike the world block above - which is derived fresh from the clock every
    # time and therefore identical on every launch - this one *carries over*. See mood.py.
    "mood_enabled": True,
    "mood_half_life_h": 6.0,      # how long a good or bad turn takes to half fade
    "mood_spirits": 0.0,          # -1..+1, written by him
    "mood_saved_at": 0.0,         # unix seconds at the last write, so the decay survives a restart
    # What he remembers about the two of you - counts only, kept in memory.json next to this
    # file rather than in it. See memory.py, and docs/PRIVACY.md for the whole list of what's in it.
    "memory_enabled": True,
    #: The one part of that close to a time: a 24-slot tally of the hour he first saw you each
    #: day, so he can know when you usually arrive. Off drops the tally from the file.
    "memory_routine_enabled": True,
    # Energy, affection and boredom - see needs.py. Like the mood they only lean on what he
    # already does, and the four values below them are written by him, aged across a restart.
    "needs_enabled": True,
    "need_energy": 0.5,           # 0 worn out .. 1 bright-eyed
    "need_affection": 0.5,        # 0 lonely .. 1 doted on
    "need_boredom": 0.0,          # 0 kept busy .. 1 bored stiff
    "needs_saved_at": 0.0,        # unix seconds at the last write, like mood_saved_at
    #: How often he says a line in a toast - see voice.py. "rare" is at most 6 a day, 45 minutes
    #: apart; "normal" at most 14, 20 minutes apart; "off" never. Greetings skip the gap, not the cap.
    "chatter_level": "rare",
    # Latches, so a once-a-day moment stays once a day across restarts. Written, not read by you.
    "last_seen_date": "",
    "last_clock_off_date": "",
    "last_week_over_week": "",
    #: The same, for what he remembers: the last day-count he celebrated (so a restart on the
    #: seventh day does not throw the party twice) and the last day of a focus streak.
    "last_milestone_days": "",
    "last_focus_streak_date": "",
    "roam_all_monitors": True,
    # Shortcuts
    "hotkey": "ctrl+alt+c",       # "" disables the global hotkey
    "come_hotkey": "",            # calls him to your pointer; "" disables it
    #: Grabs the clipboard onto his standing sign - a click or the main hotkey empties it into the
    #: bubble instead of opening empty. Grabbing again adds another line rather than replacing it.
    #: "" disables it. Never written anywhere but memory - see Behavior._pocket.
    "pocket_hotkey": "ctrl+alt+p",
    #: Mutes and unmutes the default microphone. Empty by default, like come_hotkey, so it
    #: cannot collide with a shortcut an app you take calls in already claims - and because a
    #: mute key you did not ask for is worse than no mute key at all.
    "mic_toggle_hotkey": "",
    #: Right-click him for a game-style wheel of his tools rather than a menu. The full menu is
    #: still one gesture away - the wheel's More... tool, or Shift+right-click - and turning this
    #: off puts right-click back to that menu exactly as it was.
    "tool_wheel_enabled": True,
    #: The Away Guard: fog over every monitor, giant eyes, and Nib on guard until you prove it is
    #: you - see guard_service.py and docs/USING-HIM.md. Background work keeps running throughout.
    "guard_hotkey": "ctrl+alt+g",  # "" disables it; the wheel and the tray entry always work
    "guard_peek_minutes": 2,      # how long "Peek" lifts the guard before it comes back by itself
    #: "hello" offers Windows Hello when this PC has it, with the guard PIN as the fallback; "pin"
    #: never offers Hello. Without Hello, the first guard asks you to set a PIN.
    "guard_auth": "hello",
    #: scrypt$<salt>$<hash> of the guard PIN, written by the first-run setup. Never the PIN itself.
    #: Clear it to set a new PIN on the next guard.
    "guard_pin_hash": "",
    "guard_scene": "both",        # "both" | "eyes" | "nib" - what stands guard on the fog
    "guard_fog_strength": 5,      # 1-5: how heavily the desktop is blurred under the fog
    #: Life in the fog: worker bots, drifting dust, a camera scan line with REC, a slow glow that
    #: follows the time of day (guard_fx.py). Off = plain fog, and no effects timer at all.
    "guard_background_fx": True,
    #: Wrong PINs before the screens go black. The wait between tries keeps doubling regardless.
    "guard_blackout_after_attempts": 3,
    "guard_sounds": True,         # his guard noises; the global sound switch still applies
    "guard_auto_after_idle_min": 0,  # guard by itself after this many idle minutes; 0 = never
    #: Ten wrong PINs locks Windows itself. Locking does not stop background work.
    "guard_lock_fallback": False,
    #: Unlocking Windows (Win+L, then your Windows sign-in) ends the guard too - the way back in
    #: if you forget the PIN. Treated as on while no guard PIN is set (Hello alone could break).
    "windows_unlock_clears_guard": True,
    "quick_prompt": "",           # double-click sends this; empty falls back to your last prompt
    "last_prompt": "",            # remembered so a double-click can repeat it
    #: Recent prompts, newest first, so Up in the bubble walks back through them. Local only.
    "prompt_history": [],
    "prompt_history_max": 20,     # 0 switches it off and clears what is stored
    "annoyance_threshold": 4,
    "annoyance_window_s": 15,
    "annoyance_decay_s": 20,
    "sleep_cooldown_s": 60,
    #: A lens over the numbers above rather than a rewrite of them: the preset scales what you
    #: have set, your file keeps your own values, and switching back to "balanced" restores them.
    "personality": "balanced",
    #: What *he* is called - in the tray tooltip, his toasts and the lines he says. Not the app's
    #: name: window titles and error dialogs still say Nib, because they are about the program.
    "name": "Nib",
    "sprite_scale": 0,  # 0 = auto from monitor DPI
    "costume": "",      # "" = bare-headed; see assets/costumes.json for the names
    #: The costumes you put on him last, newest first, so the wardrobe opens on them. Checked
    #: against the sheet where they are used, the same as ``costume`` itself.
    "costume_recents": [],
    "skin": "default",  # body colour remap; see SKINS in animator.py
    #: A soft ellipse under his feet that widens on impact. Without it he reads as hovering a
    #: pixel above the bar rather than standing on it.
    "shadow_enabled": True,
    #: Faded right down and click-through, so he is visible but completely out of the way. The
    #: escape hatch for a bad day - the alternative to it is uninstalling him.
    "ghost_mode": False,
    "ghost_opacity": 0.35,
    #: Small blips synthesised into assets/sounds by tools/gen_sounds.py - no audio files ship in
    #: the repository. Off by default; a missing or unplayable file is silently ignored.
    "sound_enabled": False,
    "sound_volume": 0.35,
    #: The one-off tour of the things you would otherwise never find. Cleared to replay it.
    "intro_seen": False,
    "hide_on_fullscreen": True,
    "last_x": None,
    "last_monitor": None,
    "launch_on_startup": False,
    "paused": False,
    "hover_wave_distance_px": 120,
    # Where he stands when there is no remembered position: 0.0 = far left, 1.0 = far right.
    "home_fraction": 0.78,
    "log_level": "INFO",
}

#: Inclusive clamps for numeric settings, to keep a hand-edited file from breaking behaviour.
_BOUNDS: dict[str, tuple[float, float]] = {
    "wander_min_interval_s": (0.0, 3600.0),
    "wander_max_interval_s": (0.0, 7200.0),
    "walk_min_px": (8, 8000),
    "walk_max_px": (8, 8000),
    "walk_speed_px_s": (0, 2000),
    "antic_chance": (0.0, 1.0),
    "eye_track_distance_px": (0, 8000),
    "annoyance_threshold": (1, 100),
    "annoyance_window_s": (1, 600),
    "annoyance_decay_s": (1, 3600),
    "sleep_cooldown_s": (1, 86400),
    "sprite_scale": (0, 12),
    "hover_wave_distance_px": (0, 4000),
    "home_fraction": (0.0, 1.0),
    "away_after_s": (0, 86400),
    "focus_duration_min": (1, 180),
    "talking_while_muted_after_s": (0.5, 30.0),
    "attention_after_s": (0, 86400),
    "injury_duration_s": (0.0, 60.0),
    "cpu_busy_percent": (10, 100),
    "pet_hold_ms": (0, 10000),
    "prompt_history_max": (0, 200),
    "mood_half_life_h": (0.1, 240.0),
    "mood_spirits": (-1.0, 1.0),
    # No upper clamp worth the name: it is a unix timestamp, and a future one only means the
    # decay has nothing to apply. mood.py is what actually guards a clock that has jumped.
    "mood_saved_at": (0.0, 1.0e12),
    "need_energy": (0.0, 1.0),
    "need_affection": (0.0, 1.0),
    "need_boredom": (0.0, 1.0),
    # The same as mood_saved_at: needs.py guards a clock that has jumped, not this.
    "needs_saved_at": (0.0, 1.0e12),
    # Never fully invisible: a mascot you cannot see and cannot click is indistinguishable from a
    # crashed one, and the tray is then the only way back.
    "ghost_opacity": (0.08, 1.0),
    "sound_volume": (0.0, 1.0),
    "guard_peek_minutes": (1, 180),
    "guard_fog_strength": (1, 5),
    "guard_blackout_after_attempts": (1, 100),
    "guard_auto_after_idle_min": (0, 1440),
}

#: Every mode any provider offers. The *provider* decides which of them are actually reachable;
#: this list only exists so a hand-edited config cannot store a mode that is not a mode at all.
_VALID_MODES = ("chat", "cowork")
_VALID_PROVIDERS = ("claude", "chatgpt", "gemini")
#: See PERSONALITIES in behavior.py, which is where the multipliers live.
_VALID_PERSONALITIES = ("balanced", "chill", "hyper", "sassy")
#: See guard_service.py: Windows Hello (with the PIN as fallback) or the guard PIN only.
_VALID_GUARD_AUTH = ("hello", "pin")
#: What stands guard on the fog - see guard_overlay.build_scene.
_VALID_GUARD_SCENES = ("both", "eyes", "nib")
#: See LEVELS in voice.py, which is where the gaps and daily caps live.
_VALID_CHATTER = ("off", "rare", "normal")
_NULLABLE = ("last_x", "last_monitor")

#: Free-form text settings, stored verbatim. ``workday_end`` is parsed (and rejected) by
#: ``world.parse_clock`` at the point of use rather than here, so a typo disables that one moment
#: instead of being silently rewritten to a time the user never chose.
_TEXT_KEYS = (
    "hotkey", "come_hotkey", "pocket_hotkey", "mic_toggle_hotkey", "quick_prompt",
    "last_prompt", "workday_end",
    "last_seen_date", "last_clock_off_date", "last_week_over_week",
    "last_milestone_days", "last_focus_streak_date",
    # guard_pin_hash is verified (and rejected if malformed) by guard_auth.verify_pin; here it only
    # has to stay a string, since a non-string default would otherwise be stored verbatim.
    "guard_hotkey", "guard_pin_hash",
    # name is trimmed and capped by its own branch in _coerce, above the generic text one.
    "name",
)

#: Longest single stored prompt. Matches ``launcher.MAX_PROMPT_CHARS`` but is duplicated rather
#: than imported: config is deliberately dependency-free, and it is a clamp, not a contract.
_MAX_STORED_PROMPT = 14000
#: Hard ceiling on the stored history regardless of ``prompt_history_max``, so a hand-edited file
#: cannot turn the settings file into a transcript.
_MAX_HISTORY = 200
#: How many costumes the wardrobe remembers - one arc of the wheel.
_MAX_COSTUME_RECENTS = 8
#: Longest name he can be given. Long enough for any real name, short enough for a tooltip.
_MAX_NAME = 24


def _fresh_defaults() -> dict[str, Any]:
    """A copy of ``DEFAULTS`` with the mutable values copied too.

    ``dict(DEFAULTS)`` is shallow, so every ``Config`` would share the *same* list and dict
    objects with the module-level table. One in-place mutation would then leak into every
    instance created afterwards, including the next test's.
    """
    return {
        key: (list(value) if isinstance(value, list)
              else dict(value) if isinstance(value, dict) else value)
        for key, value in DEFAULTS.items()
    }


def config_path() -> Path:
    """``%APPDATA%\\NibMascot\\config.json``, with a sane fallback if APPDATA is unset."""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / APP_DIR_NAME / "config.json"


def _coerce(key: str, value: Any) -> Any:
    """Force ``value`` to the shape of ``DEFAULTS[key]``, clamping numbers."""
    default = DEFAULTS[key]

    if key in _NULLABLE and value is None:
        return None
    if key == "default_mode":
        text = str(value).lower().strip()
        return text if text in _VALID_MODES else default
    if key == "provider":
        text = str(value).lower().strip()
        return text if text in _VALID_PROVIDERS else default
    if key == "personality":
        text = str(value).lower().strip()
        return text if text in _VALID_PERSONALITIES else default
    if key == "guard_auth":
        text = str(value).lower().strip()
        return text if text in _VALID_GUARD_AUTH else default
    if key == "guard_scene":
        text = str(value).lower().strip()
        return text if text in _VALID_GUARD_SCENES else default
    if key == "chatter_level":
        text = str(value).lower().strip()
        return text if text in _VALID_CHATTER else default
    if key == "guard_pin_hash":
        # Never "None" or a number turned into text: a hash that looks set but is not would leave
        # the PIN path unusable while still counting as "a way back in".
        return value.strip() if isinstance(value, str) else default
    if key == "name":
        # Capped because it lands in a tray tooltip and a one-line toast, both of which truncate
        # or wrap badly; blank falls back rather than leaving him nameless, since every line that
        # uses it ("{name} is fine.") reads as broken with nothing there. A non-string (a JSON
        # null or number from a hand-edited file) is the default rather than str()'d, or he would
        # be called "None". Inner runs of whitespace - a newline, a tab - collapse to one space so
        # a tooltip stays on one line, and the strip comes after the cut so it can't end in one.
        if not isinstance(value, str):
            return default
        return " ".join(value.split())[:_MAX_NAME].strip() or default
    if key in _TEXT_KEYS:
        return str(value)
    if key == "prompt_history":
        # An explicit branch because the generic path below would fall through to `return value`
        # and store a bare string verbatim - after which the first append raises. Entries are
        # truncated and the list capped here, so nothing downstream has to re-check either.
        if not isinstance(value, (list, tuple)):
            return []
        cleaned: list[str] = []
        for entry in value:
            if not isinstance(entry, str):
                continue
            text = entry.strip()[:_MAX_STORED_PROMPT]
            if text:
                cleaned.append(text)
            if len(cleaned) >= _MAX_HISTORY:
                break
        return cleaned
    if key == "costume_recents":
        # Explicit for the same reason as prompt_history. Names are not checked against the sheet
        # here (config is dependency-free); the wardrobe drops any it does not know.
        if not isinstance(value, (list, tuple)):
            return []
        names = [entry.strip() for entry in value if isinstance(entry, str) and entry.strip()]
        return list(dict.fromkeys(names))[:_MAX_COSTUME_RECENTS]
    if key == "persona_apps":
        # exe name -> animation state. Both sides forced to strings; an unknown state name is
        # ignored at the point of use, where the sprite sheet can actually be consulted.
        if not isinstance(value, dict):
            return dict(default)
        return {str(k).strip().lower(): str(v).strip() for k, v in value.items() if str(k).strip()}
    if key in ("costume", "skin"):
        # Validated against what the sheet actually ships, not here - a name we do not recognise
        # degrades to bare-headed/default at load time rather than being silently rewritten.
        return str(value).strip()
    if key == "log_level":
        text = str(value).upper().strip()
        return text if text in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else default
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    if isinstance(default, float):
        try:
            real = float(value)
        except (TypeError, ValueError):
            return default
        if real != real or real in (float("inf"), float("-inf")):  # NaN / inf
            return default
        low, high = _BOUNDS.get(key, (float("-inf"), float("inf")))
        return max(low, min(high, real))

    if isinstance(default, int) or key in _NULLABLE:
        try:
            number: float = float(value)
        except (TypeError, ValueError):
            return default
        if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
            return default
        low, high = _BOUNDS.get(key, (float("-inf"), float("inf")))
        return int(max(low, min(high, number)))
    return value


class Config:
    """Dict-like settings store backed by a JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else config_path()
        self._extra: dict[str, Any] = {}  # keys we do not know about, preserved verbatim
        self._values: dict[str, Any] = _fresh_defaults()
        self.dirty = False
        self.load()

    # --- io -----------------------------------------------------------------------

    def load(self) -> None:
        raw: dict[str, Any] = {}
        try:
            if self.path.is_file():
                text = self.path.read_text(encoding="utf-8")
                parsed = json.loads(text) if text.strip() else {}
                if isinstance(parsed, dict):
                    raw = parsed
                else:
                    log.warning("config root is %s, not an object; using defaults", type(parsed))
        except (OSError, ValueError) as exc:
            log.warning("could not read %s (%s); using defaults", self.path, exc)

        stored_version = 0
        if raw:
            try:
                stored_version = int(raw.get("config_version", 0) or 0)
            except (TypeError, ValueError):
                stored_version = 0

        # Discard keys whose meaning changed since the file was written, so they take the new
        # default rather than silently pinning behaviour the user never chose.
        stale: set[str] = set()
        if raw:
            for version, keys in _MIGRATIONS.items():
                if stored_version < version:
                    stale.update(keys)
        if stale:
            log.info("migrating config to v%d; resetting %s", CONFIG_VERSION, sorted(stale))

        self._values = _fresh_defaults()
        self._extra = {}
        for key, value in raw.items():
            if key in stale:
                continue
            if key in DEFAULTS:
                self._values[key] = _coerce(key, value)
            else:
                self._extra[key] = value
        self._values["config_version"] = CONFIG_VERSION

        # A reversed range would make random.uniform behave oddly later on.
        for low_key, high_key in (
            ("wander_min_interval_s", "wander_max_interval_s"),
            ("walk_min_px", "walk_max_px"),
        ):
            if self._values[low_key] > self._values[high_key]:
                self._values[low_key], self._values[high_key] = (
                    self._values[high_key],
                    self._values[low_key],
                )
        # A migrated file must be rewritten, or it migrates again on every launch.
        self.dirty = bool(stale) or stored_version != CONFIG_VERSION

    def save(self) -> bool:
        """Write atomically. Returns False (and logs) rather than raising on failure."""
        payload = {**self._extra, **self._values}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=self.path.name + ".",
                suffix=".tmp",
                delete=False,
            )
            try:
                with handle:
                    json.dump(payload, handle, indent=2, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.path)
            except BaseException:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            log.warning("could not save %s: %s", self.path, exc)
            return False
        self.dirty = False
        return True

    # --- access -------------------------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        try:
            return self._values[key]
        except KeyError:
            return self._extra[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if key in DEFAULTS:
            value = _coerce(key, value)
            if self._values.get(key) == value:
                return
            self._values[key] = value
        else:
            if self._extra.get(key) == value:
                return
            self._extra[key] = value
        self.dirty = True

    def __contains__(self, key: str) -> bool:
        return key in self._values or key in self._extra

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._values:
            return self._values[key]
        return self._extra.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return {**self._extra, **self._values}

    def reset_position(self) -> None:
        self["last_x"] = None
        self["last_monitor"] = None

    # --- prompt history -------------------------------------------------------------
    #
    # Deliberately behind methods. ``config["prompt_history"].append(...)`` would mutate the
    # stored list in place, which never sets ``dirty`` - so the change would be lost on the next
    # save, or written at some unrelated later moment. Assigning a fresh list is the only way the
    # dirty flag can see the change.

    def remember_prompt(self, text: str) -> None:
        """Push ``text`` onto the front of the history, de-duplicated and capped."""
        limit = int(self["prompt_history_max"])
        if limit <= 0:
            if self["prompt_history"]:
                self["prompt_history"] = []
            return
        text = str(text).strip()
        if not text:
            return
        # Sending the same thing twice should move it to the front, not fill the list with it.
        kept = [entry for entry in self["prompt_history"] if entry != text]
        self["prompt_history"] = [text, *kept][:limit]

    def clear_prompt_history(self) -> None:
        self["prompt_history"] = []

    def remember_costume(self, name: str) -> None:
        """Push ``name`` onto the front of the wardrobe's recents, the same way as a prompt.
        Taking a costume off ("") is not a costume, so it is not recorded."""
        name = str(name).strip()
        if not name:
            return
        kept = [entry for entry in self["costume_recents"] if entry != name]
        self["costume_recents"] = [name, *kept][:_MAX_COSTUME_RECENTS]

    def save_if_dirty(self) -> bool:
        return self.save() if self.dirty else True

