# Away Guard — build contract (interfaces every builder must honour)

Design source of truth: `plans/AWAY-GUARD-PLAN.md`. This file fixes names and signatures so the
modules can be built in parallel. Match the codebase's existing style: `from __future__ import
annotations`, module docstring explaining *why*, `log = logging.getLogger(__name__)`, type hints,
comments at the same density as `win_integration.py` / `focus_timer.py`. PySide6 only (no new GUI
toolkits). Python 3.10+.

## File ownership (only touch your own files)
| Owner | Files |
| --- | --- |
| core | `guard_core.py` |
| auth | `guard_auth.py`, `requirements.txt` (append only) |
| controller | `guard.py`, `tests/smoke_guard.py` |
| scene | `guard_scene.py`, `assets/guard_phrases.json`, `tests/smoke_guard_scene.py` |
| overlay | `guard_overlay.py` |
| sprite | `tools/gen_sprites.py`, `assets/nib_sheet.png`, `assets/frames.json` (+ costume files only if regenerating requires it) |
| integrator (later) | `guard_service.py`, `main.py`, `config.py`, `tool_wheel.py`, `tray.py`, `docs/*.md`, `nib.spec`, `tests/run_all.py` |

## Config keys (integrator adds them; builders read them via `config.get(key, default)`)
`guard_hotkey`="ctrl+alt+g", `guard_peek_minutes`=3, `guard_auth`="hello" ("hello"|"pin"),
`guard_pin_hash`="" (format `scrypt$<salt_hex>$<hash_hex>`), `guard_scene`="both"
("both"|"eyes"|"nib"), `guard_fog_strength`=5 (1–5), `guard_blackout_after_attempts`=3,
`guard_sounds`=True, `guard_auto_after_idle_min`=0, `guard_lock_fallback`=False,
`windows_unlock_clears_guard`=True.

## guard.py — pure logic, no Qt, no ctypes
```python
class Mode(str, Enum): IDLE="idle"; FOG="fog"; BLACKOUT="blackout"; CHOICE="choice"; PEEK="peek"

@dataclass
class LogEntry: t: float; kind: str; detail: str = ""

class GuardController:
    def __init__(self, clock: Callable[[], float], *, peek_minutes: float = 3,
                 blackout_after: int = 3, lock_fallback: bool = False) -> None
    mode: Mode                       # property
    attempts: int                    # wrong attempts since last success
    log: list[LogEntry]              # intrusion log (memory only)
    def add_listener(self, fn: Callable[[Mode, Mode], None]) -> None   # (old, new)
    def guard(self) -> None          # IDLE/PEEK/CHOICE -> FOG
    def auth_ok(self) -> None        # FOG/BLACKOUT -> CHOICE; resets attempts
    def auth_failed(self) -> float   # counts, logs, returns backoff seconds (1,2,4,8.. cap 30);
                                     # blackout at >= blackout_after; sets want_lock when
                                     # lock_fallback and attempts >= 10
    def backoff_remaining(self) -> float
    want_lock: bool                  # read-and-clear by service
    def choose_peek(self) -> None    # CHOICE -> PEEK
    def choose_unlock(self) -> None  # CHOICE -> IDLE
    def extend_peek(self) -> None    # +peek_minutes, no auth
    def lock_now(self) -> None       # PEEK -> FOG
    def peek_remaining(self) -> float
    def tick(self) -> None           # PEEK expiry -> FOG
    def event(self, kind: str, detail: str = "", *, recent_input: bool = False) -> None
        # applies the suspicious-events table; ignored outside FOG/BLACKOUT
    def clear_log(self) -> None
```
Suspicious kinds: `blocked_key`, `desktop_switch`, `core_restarted` → BLACKOUT;
`foreground_stolen` → BLACKOUT only if `recent_input` else log only; `session_unlock` → handled by
service (may call choose_unlock); unknown kinds → log only.

## guard_core.py — separate process, ctypes only (no Qt import!)
Run as `python main.py --guard-core <parent_pid>` (frozen: `Nib.exe --guard-core <pid>`); module
exposes `def run(parent_pid: int) -> int`. stdin/stdout JSON lines, UTF-8, one object per line,
flush after each write.
- Nib → core: `{"cmd":"guard"}` (take power request, install hook, start WinEvent watching),
  `{"cmd":"peek"}` (unhook + stop tripwires, keep power request), `{"cmd":"unlock"}` (release all,
  exit 0), `{"cmd":"ping"}`, `{"cmd":"overlay_hwnds","hwnds":[...]}` (foreground events for these
  HWNDs, or any window of the Nib pid, are not suspicious), `{"cmd":"cover"}` / `{"cmd":"uncover"}`
  (show/hide native black windows on all monitors).
- core → Nib: `{"ev":"ready"}`, `{"ev":"pong"}`, `{"ev":"event","kind":..., "detail":..., "t":...}`
  kinds: `blocked_key` (detail = key name), `foreground_stolen` (detail = exe name),
  `desktop_switch`, `input` (throttled ≥1 per 500 ms, any key/mouse activity, so Nib knows "recent
  input").
- Keyboard: `WH_KEYBOARD_LL` on its own thread with its own `GetMessageW` loop; swallow LWin/RWin,
  Alt+Tab, Alt+Esc, Alt+F4, Ctrl+Esc while guarded; callback must be trivial (post to a queue).
  Re-install the hook every 30 s as self-heal. Never swallow Win+L.
- Foreground: `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)`; whitelist own/Nib pids,
  `CredentialUIBroker.exe`, `LockApp.exe`. Desktop: `EVENT_SYSTEM_DESKTOPSWITCH`.
- Power: `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED|ES_DISPLAY_REQUIRED)` held from
  guard until unlock/exit.
- Parent death: wait on the parent process handle; if it dies while guarded → show native black
  topmost windows on every monitor (plain Win32 `CreateWindowExW`, black brush) and keep them until
  a new Nib connects... simplest acceptable: keep covering until the user unlocks Windows
  (`WTS` unlock) or a stdin `uncover`; exit when stdin closes AND not guarded.
- Also handle `WM_WTSSESSION_CHANGE` (`WTSRegisterSessionNotification`) → emit `session_unlock` /
  `session_lock` events.

## guard_auth.py
```python
def hello_available() -> bool                        # UserConsentVerifier.check_availability == Available; False on any error/missing package
def request_hello(hwnd: int, message: str, done: Callable[[bool], None]) -> None
    # async; calls done(True/False) on the Qt main thread (use a QObject signal / QTimer.singleShot)
def hash_pin(pin: str) -> str                         # "scrypt$salt$hash", n=2**14 r=8 p=1 dklen=32, 16-byte salt
def verify_pin(pin: str, stored: str) -> bool         # hmac.compare_digest; False on malformed
class PinVerifier(QObject):  finished = Signal(bool)  # def verify(pin, stored) runs hashing in a QThreadPool / thread
```
Prefer `IUserConsentVerifierInterop.RequestVerificationForWindowAsync` with the HWND; if pywinrt
can't reach the interop, use plain `RequestVerificationAsync` and document that the overlay must
drop topmost while the prompt is open (expose `HELLO_NEEDS_UNTOPMOST: bool`).

## guard_scene.py — Qt painting, no window management
```python
class PhraseBag:
    def __init__(self, phrases: dict[str, list[str]], rng: random.Random) -> None
    def next(self, category: str, **fmt) -> str      # shuffle bag per category, no repeat until exhausted; {minutes}
def load_phrases(assets_dir: Path, user_dir: Path | None) -> dict[str, list[str]]   # merges user guard_phrases.json

class EyesModel:   # pure state: no painting
    def __init__(self, rng: random.Random) -> None
    def update(self, dt: float, now: float, pointer: QPointF, centre: QPointF, *, pointer_speed: float,
               idle_s: float, typing: bool, near_field: bool) -> None
    def set_mood(self, mood: str) -> None             # "calm"|"squint"|"veins"|"red"|"happy"|"stare"
    # exposes: pupil offset (x,y in -1..1), lid_open (0..1), blinking, sleeping, one_eye_peek, mood
class EyesPainter:
    def paint(self, p: QPainter, rect: QRectF, model: EyesModel, *, scale: int) -> None   # pixel-art, 2px blocks*scale

class StageDirector:   # chooses Nib states; pure-ish, seeded rng
    def __init__(self, sheet_states: set[str], rng: random.Random) -> None
    def update(self, now: float, idle_s: float) -> None
    def react(self, kind: str) -> None   # "pointer_near","poked","clicked","typing_start","typing_stop",
                                         # "wrong","blackout","unlock","peek_return"
    state: str; x: float (0..1 position on stage); facing: int; phrase: tuple[str,str] | None  # (category, text) to show, consumed by overlay
    sound: str | None                    # sound event to play, read-and-clear
class GuardScene(QWidget):  # the composed centre widget (eyes + Nib + bubble), transparent background
    def __init__(self, sheet: SpriteSheet, config, *, show_eyes: bool, show_nib: bool, rng=None, parent=None)
    def set_mode(self, mode: str) -> None   # "fog"|"blackout"|"peek_return"|"unlock"
    def notify(self, kind: str) -> None     # forwards reactions (wrong, typing_start, ...)
    sound_requested = Signal(str)
class MiniEyes(QWidget): ...               # small eyes for secondary monitors, track global cursor
class PeekChip(QWidget):                   # small topmost frameless chip
    extend_requested = Signal(); lock_requested = Signal()
    def set_remaining(self, seconds: float, total: float) -> None
```
Nib states used must be checked with `sheet.has_state()`; fall back gracefully (e.g. `cover_eyes`
→ `sleep` frame 0 if missing). Animation timing: one 30 FPS QTimer, 10 FPS when sleeping.

## guard_overlay.py
```python
def make_fog(screen: QScreen, strength: int) -> QPixmap   # grab -> <=48px wide immediately -> discard -> upscale+blur+haze
class GuardOverlay(QWidget):     # one per QScreen, frameless topmost Tool window, WDA_EXCLUDEFROMCAPTURE
    def __init__(self, screen: QScreen, *, primary: bool, scene: GuardScene | None, config, parent=None)
    pin_submitted = Signal(str); hello_requested = Signal(); choice_peek = Signal(); choice_unlock = Signal()
    typing_changed = Signal(bool); pointer_near_field = Signal(bool)
    def set_mode(self, mode: str) -> None          # "fog"|"blackout"|"choice"
    def set_status(self, text: str) -> None
    def set_auth(self, *, hello: bool, pin: bool, backoff_s: float) -> None
    def refresh_fog(self) -> None                  # no-op in blackout
    def reassert(self) -> None                     # topmost + activate + re-apply display affinity
    def hwnd(self) -> int
class OverlaySet:   # manages all monitors, screenAdded/screenRemoved, 3 s fog timer, 500 ms reassert timer
```
Primary overlay hosts the GuardScene, the PIN field (password echo, digits/any chars), the Hello
button, the backoff countdown, and the CHOICE panel ("Peek 3 min" / "Unlock"). Blackout = solid
black + scene in red-eyes mode.

## Sprite
New state `cover_eyes`: 4 frames, both arms raised over the eyes (looping 2-frame jiggle ok),
generated by `tools/gen_sprites.py` the same way as the others. Existing state order/indices that
costumes rely on must not break: `tests/smoke_costumes.py` and `tests/smoke_faces.py` must pass.
