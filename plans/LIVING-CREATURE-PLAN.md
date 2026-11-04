> **Status:** WP1–WP5 are implemented, reviewed and committed on this branch (`wip/living-creature`); every test passes at each commit.
> Continue from **WP6**. Run `py tests/run_all.py` and `py tools/check_docs.py` before each commit, and keep each file's line endings as they are.
>
> - [x] WP1 name + mood
> - [x] WP2 memory + Forget
> - [x] WP3 greetings/milestones
> - [x] WP4 voice/phrases
> - [x] WP5 needs (energy, affection, boredom)
> - [ ] WP6 bond/gentle/play
> - [ ] WP7 nap
> - [ ] WP8 treat
> - [ ] WP9 learned taste

# Nib "living creature" — engineering spec for branch `feat/living-creature`

(Engineering spec written after reading the code. CHANGELOG.md has an `## [Unreleased]` section above `## [1.2.0]`; new work goes there.)

Implementers should read `docs/HOW-IT-WORKS.md`, `CONTRIBUTING.md` ("The rules that aren't negotiable"), `mood.py` (the template for every new state module) and `tests/smoke_mood.py` (the template for every new test) before WP1.

---

## 0. Ground rules for every package

1. **Qt-free logic.** `behavior.py`, `world.py`, `mood.py` and the new modules (`memory.py`, `needs.py`, `voice.py`, plus the extracted `phrases.py`) import no `PySide6`. Anything Qt (toasts, tray, settings) stays in `main.py` / `settings.py` / `tray.py`. Behavior talks outward only through drain queues (`take_sounds()` pattern) and inward through plain method calls, exactly as `Mood` and `WorldState` do today.
2. **Every timed or scheduled behaviour gets a measured rate** (CONTRIBUTING "Adding things → A behaviour"): a `tests/*_rate.py` script and the number in the docs. New: `tests/chatter_rate.py`; extended: `tests/day_rate.py`. Registered in `tests/run_all.py` `RATES`.
3. **Every new setting**: `config.DEFAULTS` (+ `_BOUNDS` if numeric, `_TEXT_KEYS` if free text, a `_VALID_*` tuple + `_coerce` branch if enumerated), a row in `docs/SETTINGS.md` in the exact form `| \`key\` | \`default\` | meaning |` where the default is rendered as `tools/check_docs.py::as_text` renders it (`true`/`false`, strings **with JSON quotes** e.g. `"Nib"`, floats as Python prints them e.g. `0.5`), and placement in `settings.GROUPS` or `settings.HIDDEN`. `tests/smoke_ui.py` lines 220–230 fail if a key is in neither, or appears twice in `GROUPS`. `python tools/check_docs.py` must pass (it also checks every `](#anchor)` link in README/CONTRIBUTING/docs resolves).
4. **No new sensors, no network, no content.** Rule 4 of CONTRIBUTING. New memory is counts and a rolling 7-day window only. No executable names, no timestamps of app use, no prompt text beyond the existing `prompt_history`.
5. **Mood/needs never *fire* anything directly.** They compose multiplicatively into `Behavior._mood_weights` and `_mood_pace`; anything event-like goes through `Behavior._pick_moment` (shared `REACTION_COOLDOWN_MS` = 45 s + its own per-kind cooldown) or through `_greeting_for`.
6. **Persistence rules** (from `mood.py` and `config.py`): quantise before writing; write only from `main._flush_config` (10 s timer, `CONFIG_FLUSH_MS`) or quit; never mutate a stored list/dict in place (the `Config` docstring for `remember_prompt` explains why — in-place mutation never sets `dirty`).
7. **Line endings are per file and must be preserved.** Files are mixed CRLF/LF (`git ls-files --eol`). CRLF includes `behavior.py`, `main.py`, `config.py`, `settings.py`, `tray.py`, `bubble.py`, `sound.py`, `tool_wheel.py`, `tools/gen_sprites.py`, `tools/gen_sounds.py`, `tools/validate_sheet.py`, `assets/frames.json`, all `docs/*.md` except HOW-IT-WORKS, `README.md`, `CHANGELOG.md`, most tests. LF includes `mood.py`, `guard_service.py`, `guard.py`, `updater.py`, `tools/check_docs.py`, `CONTRIBUTING.md`, `docs/HOW-IT-WORKS.md`, `assets/guard_phrases.json`, `tests/smoke_focus.py`, `smoke_perch.py`, `smoke_pocket.py`, `smoke_update.py`. Some editing tools silently convert a whole file to LF. After editing, run the fixer: a small script that rewrites each edited file with the line endings HEAD has for it. New files: use **LF**. Reviewer check: `git diff --stat` must not show whole-file rewrites.
8. **Regenerated assets are committed.** CI runs `tools/gen_sprites.py` and fails on `git diff --exit-code assets/`; then `validate_sheet.py`, `gen_sounds.py`, `tests/run_all.py`, `tools/check_docs.py`. Any WP touching `gen_sprites.py` commits `nib_sheet.png`, `frames.json`, `preview.png`, `banner.png`, `costumes.*`.
9. **Tests are plain scripts** (no pytest): each prints `OK/FAIL` via a local `check()` and exits non-zero. New suites are added to `SUITES` in `tests/run_all.py` with a one-line description; the README suite-count sentence is updated in the WP that adds one.
10. **CHANGELOG**: WP1 adds `## [Unreleased]` above `## [1.2.0]`, with `### Added` / `### Changed`; every later WP appends bullets there.

---

## 1. Shared foundations

### 1.1 New modules and public APIs

#### `phrases.py` (new, Qt-free) — extracted from `guard_scene.py`
Move `PhraseBag`, `_KeepUnknown`, `_format`, `_read_phrase_file`, `load_phrases` out of `guard_scene.py` (lines ~89–216) verbatim, generalised:
```python
def load_phrases(assets_dir: Path, user_dir: Path | None, *, filename: str,
                 builtin: dict[str, list[str]]) -> dict[str, list[str]]
class PhraseBag:  # unchanged API: categories(), next(category, **fmt)
```
`guard_scene.py` keeps `PHRASES_FILE`, `_BUILTIN_PHRASES` and re-exports: `from phrases import PhraseBag, load_phrases as _load_phrases` with a thin `load_phrases(assets_dir, user_dir)` wrapper passing its own filename/builtin, so `tests/smoke_guard_scene.py` and `guard_overlay.py` keep working unchanged. Behaviour byte-identical; this is a pure move.

#### `memory.py` (new, Qt-free, clock-injectable)
```python
MEMORY_FILE = "memory.json"
MAX_DAYS = 8                       # rolling window: today + 7
TASTE_MIN, TASTE_MAX = 0.7, 1.4

@dataclass
class DayRecord: prompts:int=0; pets:int=0; focus_done:int=0; busy_min:int=0; present_min:int=0; treats:int=0; throws:int=0; hurts:int=0

class Memory:
    def __init__(self, path: Path | None, config, clock=time.time)   # path None = in-memory only (tests)
    @property enabled -> bool                     # config["memory_enabled"]
    @property routine_enabled -> bool             # config["memory_routine_enabled"]
    def load() -> None; def save_if_dirty() -> bool   # atomic write, same tempfile+os.replace as Config.save
    def record(event: str, date_key: str, n: int = 1) -> None   # event in DayRecord fields; also bumps totals
    def first_sight(date_key: str, hour: float) -> bool   # marks the day seen; bumps days_seen; arrival_hours[int(hour)] += 1 if routine_enabled; True if new day
    def day(date_key) -> DayRecord; def yesterday(date_key) -> DayRecord | None
    @property days_seen -> int; first_met -> str; totals -> dict[str,int]
    def usual_hour() -> float | None     # argmax of arrival_hours if sum >= 5 else None
    def heavy_yesterday(date_key) -> bool   # yesterday.prompts + yesterday.busy_min/10 >= 1.6 * median of the 6 days before it, and yesterday.present_min >= 120
    def bond() -> float                   # 0..1, see 1.3; never decreases while counters only grow
    def bond_label() -> str               # "just met" <0.15, "warming up" <0.35, "friends" <0.6, "close" <0.85, else "inseparable"
    @property taste -> dict[str,float]; def adjust_taste(antic: str, delta: float) -> None  # clamp TASTE_MIN..MAX
    def decay_taste(days: int) -> None    # 1 % toward 1.0 per day
    def forget() -> None                  # resets everything, deletes the file
    def summary_lines(name: str) -> list[str]   # plain English; used by Settings
```
Every mutator is a no-op when `not enabled` (file untouched; `forget()` still works).

#### `needs.py` (new, Qt-free, clock-injectable) — a sibling of `Mood`
```python
class Needs:
    def __init__(self, config, clock=time.time)
    @property enabled -> bool                         # config["needs_enabled"]
    energy, affection, boredom -> float               # each 0..1; 0.5 when disabled (energy/affection), 0.0 (boredom)
    def tick(dt_ms, *, away: bool, part_of_day: str, present_interacting: bool) -> None
    def nudge(event: str, scale: float = 1.0) -> None  # NEED_NUDGES table, squared outward taper as Mood.nudge
    def persist() -> None                             # quantised 0.01, writes need_* + needs_saved_at
    def weights() -> dict[str, float]                 # multipliers, {} at neutral
    def pace() -> float                               # 1.0 at neutral
    def slow_blink_bonus() -> float
    def peek_boost() -> float                         # 1.0..1.6 from boredom
    def labels() -> tuple[str,str,str]                # ("rested"/"okay"/"tired", "loved"/"fine"/"lonely", "busy"/"fine"/"bored")
    def reset() -> None
```

#### `voice.py` (new, Qt-free)
```python
LEVELS = {"off": None, "rare": (45*60, 6), "normal": (20*60, 14)}   # (min gap s, max per day)
LAUNCH_QUIET_S = 60.0
class Voice:
    def __init__(self, bag: PhraseBag, config, clock=time.time)
    def may_speak(self, category: str) -> bool      # level, gap, day cap, launch quiet; "greeting_*" exempt from gap only
    def say(self, category: str, **fmt) -> str      # "" if may_speak false; else records the time/count and returns the line
```
Day count and last-spoken time live in memory only (reset on restart — documented).

### 1.2 Where state lives

**`config.json`** (scalars) — see table 1.4.
**`%APPDATA%\NibMascot\memory.json`** (structured counters, owned by `memory.py`, written only from `main._flush_config` when dirty — at most one write per minute because the only per-minute counter is `present_min`):
```json
{"version": 1, "first_met": "2026-11-04", "days_seen": 23,
 "totals": {"prompts": 87, "pets": 412, "focus_done": 12, "treats": 40, "throws": 9, "hurts": 3},
 "days": {"2026-11-03": {"prompts": 9, "pets": 20, "focus_done": 1, "busy_min": 48, "present_min": 410, "treats": 2, "throws": 0, "hurts": 0}},
 "arrival_hours": [0,0,0,0,0,0,0,2,11,6,1,0,0,0,0,0,0,0,0,0,0,0,0,0],
 "taste": {"hop": 1.12, "dance": 0.94}}
```
`days` is pruned to `MAX_DAYS` newest keys on every `record`. A corrupt file is logged and replaced by an empty memory (same stance as `Config.load`).

### 1.3 Formulas (fixed here so packages agree)
- **Bond** = `clamp01(0.45·L(days_seen,120) + 0.30·L(pets,600) + 0.15·L(prompts,300) + 0.10·L(focus_done,60))`, `L(n,k)=ln(1+n)/ln(1+k)`.
- **Need nudges** (`needs.NEED_NUDGES`, each `(energy, affection, boredom)` deltas): `petted (0, +0.06, −0.5)`, `prompt_sent (0, +0.02, −0.5)`, `greeted (0, +0.03, −0.3)`, `treated (+0.05, +0.08, −0.3)`, `focus_done (−0.05, +0.04, −0.3)`, `thrown (−0.02, 0, −0.2)`, `hurt (−0.05, 0, 0)`, `interacted (0, 0, −0.5)`, `world_event (0, 0, −0.2)`, `nap_woke (+0.30, 0, 0)`.
- **Need drift per hour** (in `tick`): energy `+0.50/h` while `away` or closed, `−0.08/h` while present, extra `−0.15/h` in `PART_NIGHT`; affection half-life 12 h toward 0.5; boredom `+0.10/h` while present and no interaction, decays toward 0 with half-life 2 h otherwise. Offline ageing in `_load` uses the same rates with `away=True`.
- **Need weights** (`Needs.weights`, linear in distance from the band edge, like `Mood.weights`): energy<0.35 → `sit_dangle ×2.0, check_watch ×1.5, hop/dance/football/pushups ×0.4`; energy>0.75 → `hop ×1.5, dance ×1.4`; affection>0.7 → `heart_eyes ×1.8, sparkle ×1.4, wink ×1.3`; affection<0.3 → `look_around ×1.4` and the four `"eyes"` antics ×1.5; boredom>0.6 → `check_watch ×2.0, foot_tap ×1.6, scratch ×1.4, sit_dangle ×1.5`. `pace()`: 1.0 → 0.90 as energy goes 0.35 → 0. `slow_blink_bonus()`: `0.25·(0.4−energy)/0.4` when energy<0.4. `peek_boost()`: `1 + 0.6·max(0, boredom−0.6)/0.4`.
- At neutral (energy 0.5, affection 0.5, boredom 0) **every multiplier is exactly 1.0 and `weights()` is `{}`** — this is what keeps `fidget_rate.py` / `quirk_rate.py` baselines unchanged.

### 1.4 New settings (complete list)

| Key | Default | Bounds / values | Tab (settings.GROUPS) | Hidden | SETTINGS.md section & row meaning |
| --- | --- | --- | --- | --- | --- |
| `name` | `"Nib"` | text, `_TEXT_KEYS`; coerce: strip, max 24 chars, empty → `"Nib"` | Personality (first) | no | Personality: "What he's called in the tray, toasts and lines he says" |
| `memory_enabled` | `true` | bool | Personality | no | "Counts the days, pets, prompts and focus sessions you share. Counts only, kept in memory.json" |
| `memory_routine_enabled` | `true` | bool | Personality | no | "Lets him learn roughly what time you usually arrive (an hourly tally, nothing else)" |
| `needs_enabled` | `true` | bool | Personality | no | "Energy, affection and boredom that colour what he does" |
| `chatter_level` | `"rare"` | `_VALID_CHATTER = ("off","rare","normal")`; `settings.CHOICES["chatter_level"]` | Personality | no | "How often he says a line in a toast: rare is at most 6 a day, 45 min apart" |
| `nap_enabled` | `true` | bool | Personality | no | "Dozes off late at night when you've stepped away, and wakes when you're back" |
| `need_energy` | `0.5` | `(0.0, 1.0)` | — | yes | State and diagnostics: "Written by him" |
| `need_affection` | `0.5` | `(0.0, 1.0)` | — | yes | State and diagnostics |
| `need_boredom` | `0.0` | `(0.0, 1.0)` | — | yes | State and diagnostics |
| `needs_saved_at` | `0.0` | `(0.0, 1.0e12)` | — | yes | State and diagnostics: "Unix seconds at the last write" |
| `last_milestone_days` (WP3) | `""` | text, `_TEXT_KEYS` | — | yes | State |
| `last_focus_streak_date` (WP3) | `""` | text, `_TEXT_KEYS` | — | yes | State |

`mood.NUDGES` gains `"treated": 0.12` (WP8). `settings.HELP` gets one line per visible key. `settings.NEEDS_RESTART` unchanged.

### 1.5 `docs/PRIVACY.md` wording (WP2, extended in WP4/WP9)
Replace the final bullet ("Everything he remembers — position, mode, costume, your prompts, how the day has gone — stays in `%APPDATA%\NibMascot\config.json`") with:
> - **Everything he remembers stays on your machine.** Settings, position, costume, your prompts and how the day has gone live in `%APPDATA%\NibMascot\config.json`. What he remembers *about you two* lives next to it in `memory.json`: the day you first ran him, how many days he has seen you, lifetime counts of pets, prompts, completed focus sessions, treats and throws, the same counts for the last seven days plus how many minutes the machine was busy and how many you were present, and — if `memory_routine_enabled` is on — a 24-slot tally of which hour he first saw you each day. **That is the whole file.** No app names, no times of anything, no text. You can read it in **Settings → About him**, switch it off with `memory_enabled`, and **Forget everything** wipes it. The lines he occasionally says come from a local table in `assets/nib_phrases.json` (add your own in a `nib_phrases.json` next to `config.json`); nothing is generated by a model or fetched.

### 1.6 Wiring map

| Existing code | Hook |
| --- | --- |
| `main.NibApp.__init__` (after `self.mood = Mood(config)`) | `self.memory = Memory(config_path().parent / MEMORY_FILE, config)`; `self.needs = Needs(config)`; `Behavior(config, self.sheet, self.mood, needs=self.needs, memory=self.memory)`; `self.voice = Voice(PhraseBag(load_phrases(assets, config_dir, filename=NIB_PHRASES_FILE, builtin=...), random.Random()), config)` |
| `Behavior.__init__(config, sheet, mood=None)` | add `needs: Needs | None = None, memory: Memory | None = None`, defaulting to `Needs(config)` and `Memory(None, config)` so every existing test constructor still works |
| `Behavior.update` (after `self.mood.tick(dt_ms)`) | `self.needs.tick(dt_ms, away=self._away, part_of_day=..., present_interacting=self._neglect_ms < 1000)`; nap check (WP7) |
| `Behavior._mood_weights` | multiply in `self.needs.weights()` and `self.memory.taste` (WP5/WP9) |
| `Behavior._mood_pace` | `× self.needs.pace()` |
| `Behavior._pick_blink` | `+ self.needs.slow_blink_bonus()` next to `self.mood.slow_blink_bonus()` |
| `Behavior._choose_next_action` peek branch (`random.random() < PEEK_CHANCE`) | `PEEK_CHANCE * self.needs.peek_boost()` |
| `Behavior._pick_moment` (after the seasonal block) | needs threshold moments (WP5), milestone moments (WP3) |
| `Behavior._maybe_pet`, `prompt_sent`, `_finish_focus`, `set_away`, `drag_end`, `_settle` | `self.needs.nudge(...)`, `self.memory.record(...)`, `self._event(...)` |
| `Behavior.noticed()` | `self.needs.nudge("interacted")` |
| `Behavior.foreground_changed` (any branch that begins an antic) and `_pick_moment` success | `self.needs.nudge("world_event")` |
| `Behavior._greeting_for` | memory-aware greeting (WP3) |
| New `Behavior._event(kind, **data)` + `take_events() -> list[tuple]` | drain queue like `take_sounds`; main maps to voice categories |
| `main._tick` (after `take_sounds` loop) | `for kind, data in self.behavior.take_events(): self._on_creature_event(kind, data)` |
| `main._update_presence` | first-sight + present/busy minutes into memory; tooltip composition (`name`, mood label, bond) |
| `main._flush_config` | `self.needs.persist()`; `self.memory.save_if_dirty()` |
| `main._on_settings_changed` | `"name" in keys` → `_update_presence()` (tooltip) |
| `main._wheel_items` Him group, `main._on_tool_chosen`, `tray.py` | the Treat tool (WP8) |
| `settings.SettingsWindow` | optional `status: Callable[[], dict] | None` + `forget: Callable[[], None] | None`; new hand-built "About him" tab (WP1/WP2) |
| `tests/run_all.py` | new suites and rates |

---

## 2. Work packages

### WP1 — Name, and the mood you can see (S)
**Goal.** Owner can glance at the tray and read how he is; he has a name.
**Files.** `config.py`, `settings.py`, `tray.py`, `main.py`, `docs/SETTINGS.md`, `docs/USING-HIM.md`, `docs/BEHAVIOUR.md`, `README.md`, `CHANGELOG.md`, `tests/smoke_ui.py`.
**Behaviour.**
- `config.DEFAULTS["name"] = "Nib"`; add to `_TEXT_KEYS`; `_coerce` branch: `str(value).strip()[:24] or "Nib"`.
- `tray.Tray.DEFAULT_TOOLTIP` stays as a fallback; `main._update_presence` `else` branch becomes `self.tray.set_tooltip(f"{name} — {label_for(self.mood.spirits)}")` (import `label_for` from `mood`). The focus/device/pin branches above it keep their text but prefix `name` instead of the literal `"Nib - "`. `tray.notify("Nib", ...)` in `_tick` → `notify(name, ...)`. `INTRO_STEPS` gains a 5th step `("You can rename me in Settings → Personality.", "And see how I'm doing there too.", 5000)`; `tests/smoke_ui.py:363` loops `len(INTRO_STEPS)+1` so no change needed there. Dialog titles ("Nib settings", QMessageBox "Nib") stay — that is the app's name, not his.
- `settings.SettingsWindow.__init__(config, *, costumes, skins, status=None, forget=None, parent=None)`. After the `GROUPS` tabs, add a hand-built tab **"About him"** (not in `GROUPS`, holds no `_Field`, so the "unreachable" and "nothing hidden leaked" checks in `smoke_ui.py` are unaffected): a `QLabel` "Right now" filled from `status()` → `{"line": "Nib is in good spirits"}`; refreshed in `reload()`. The Forget button and memory lines arrive in WP2; this WP ships the tab with the one line.
- `main._on_settings` passes `status=self._status_text`, returning `{"line": f"{name} is {label_for(spirits)}."}`.
**Tests.** `tests/smoke_ui.py`: new section — `SettingsWindow(cfg, costumes=[], skins=["default"], status=lambda: {"line": "x"})` has a tab titled "About him"; `_coerce("name", "   ")` → `"Nib"`, 40-char name truncated to 24; tooltip composition tested via `inspect.getsource(M.NibApp._update_presence)` contains `label_for`.
**Docs.** SETTINGS.md Personality row for `name`; USING-HIM.md "Tray menu" and a new short "How he's doing" paragraph (tooltip + About him); BEHAVIOUR.md "How the day went" — note where the label now appears; README one clause; CHANGELOG `[Unreleased]` created.
**Acceptance.** Tray tooltip shows `"<name> — fine"` on a fresh config and changes after a pet; Settings has "About him"; renaming applies after Apply without restart; `check_docs.py` and `run_all.py` green; no `"Nib - "` literal left in `main._update_presence`.

### WP2 — `memory.py`: counters, About-him panel, Forget, privacy text (M)
**Goal.** He starts keeping count; the owner can see and wipe it.
**Files.** new `memory.py`, `config.py`, `behavior.py`, `main.py`, `settings.py`, new `tests/smoke_memory.py`, `tests/run_all.py`, `docs/PRIVACY.md`, `docs/SETTINGS.md`, `docs/USING-HIM.md`, `docs/HOW-IT-WORKS.md` (project layout list), `README.md` (suite count), `CHANGELOG.md`.
**Behaviour.**
- Settings `memory_enabled`, `memory_routine_enabled` (table 1.4).
- `Behavior` records (all pass `self._world.date_key` if `_world` else today's `strftime`): `_maybe_pet` → `pets`; `prompt_sent` → `prompts`; `_finish_focus` (inside the gated block) → `focus_done`; `drag_end` when `abs(vx) > 0.4` → `throws`; `_settle` when injured → `hurts`.
- `main._update_presence`: when `not away` and `world.state.date_key != self._memory_day` → `memory.first_sight(date_key, hour)`; accumulate `present_min` (every 60 s of polls while not away) and `busy_min` (while `world.state.cpu_busy`). Keep a `self._minute_acc` float; one `record` per full minute.
- `memory.save_if_dirty()` in `_flush_config`.
- Settings "About him": `status()` now returns `{"line": ..., "memory": [lines], "can_forget": bool}`; a **Forget everything…** button calls `forget()` after a `QMessageBox.question` worded like `_on_clear_history`. `main` passes `forget=self._forget_memory` → `memory.forget(); needs.reset(); mood.reset(); behavior.noticed()` (needs.reset arrives in WP5 — guard with hasattr or add later).
- `summary_lines(name)`: "Known you 23 days (since 4 Nov 2026)", "412 pets · 87 prompts · 12 focus sessions · 40 treats", "Usually sees you around 08:00" (only if `usual_hour()`), "Yesterday was a heavy one" (if `heavy_yesterday`).
**Tests.** `tests/smoke_memory.py` (Qt-free, FakeClock like `smoke_mood.py`): record/totals; days pruned to 8; `first_sight` once per day; `usual_hour` None under 5 samples; `heavy_yesterday` true/false fixtures; `bond()` monotone over a growing fixture and label boundaries; `forget()` deletes the file; disabled → no file written; corrupt file → empty memory, no raise; atomic write leaves no `.tmp`. Register `("smoke_memory", "what he remembers: counters, the 7-day window, bond, forget")`.
**Docs.** PRIVACY.md text in 1.5; SETTINGS.md two rows; USING-HIM.md "What he remembers" subsection (and how to clear); HOW-IT-WORKS.md layout gets `memory.py` and `memory.json`; CHANGELOG.
**Acceptance.** After one pet and one send, `memory.json` exists with `pets:1, prompts:1` within 10 s; `present_min` increments; Forget empties the panel and deletes the file; `memory_enabled=false` → no file created; PRIVACY.md describes exactly the keys in the file.

### WP3 — Memory in behaviour: greetings and milestones (S–M)
**Goal.** He acts on what he remembers, without saying a word yet.
**Files.** `behavior.py`, `config.py` (two hidden latch keys), `settings.py` (HIDDEN), `tests/smoke_memory.py` (new section), `tests/day_rate.py`, `docs/BEHAVIOUR.md`, `docs/SETTINGS.md`, `CHANGELOG.md`.
**Behaviour.**
- `Behavior._greeting_for(gone_s)`: after computing the base list, if `memory.heavy_yesterday(date_key)` and `part_of_day == PART_MORNING` and this is the overnight branch → `GREET_OVERNIGHT + [("coffee", 2600)]`. If `memory.bond() >= 0.6` the hop branch becomes `[(GREET_ANTIC, GREET_MS), ("heart_eyes", 1400)]`. Emit `self._event("greeted", gone_s=gone_s, overnight=bool, heavy=bool)` for WP4 (add `_event`/`take_events` drain queue now if not yet present).
- `_pick_moment`, new block after seasonal: **day milestones** — `memory.days_seen in MILESTONE_DAYS = (7, 30, 100, 365)` and `self._latch("last_milestone_days", str(days_seen))` → `self._moment(TASK_FINISHED_ANIM, TASK_FINISHED_MS, TASK_FINISHED_MS, costume="party")`, `self._sound("cheer")`, `self._event("milestone", days=n)`. **Focus streak**: `day(date_key).focus_done == 3` exactly, once per day (`last_focus_streak_date`, hidden) → same celebration (copy `_finish_focus`'s use of the task-finished cooldown). **Big week** on Friday inside the existing `last_week_over_week` branch: if this week's prompts ≥ 1.5× last week's → `_event("big_week")`.
- `_pick_moment` success → `self.needs.nudge("world_event")` if a needs object exists (no-op until WP5).
**Tests.** New section in `smoke_memory.py` constructing a `Behavior` with a pre-filled in-memory `Memory`: milestone fires once on day 7 and not again after restart (latch); heavy-yesterday morning greeting contains `coffee`; high-bond greeting contains `heart_eyes`; `day_rate.py` extended with a bond-0.9 / day-30 run, milestone count == 1 per simulated day.
**Docs.** BEHAVIOUR.md "Moments" table rows: Day milestone, Focus streak; greeting paragraph; SETTINGS.md hidden rows.
**Acceptance.** `day_rate.py` output unchanged for the existing runs (no new firing in neutral memory); milestone exactly once; latch keys documented.

### WP4 — `phrases.py` extraction, `voice.py`, `assets/nib_phrases.json`, chatter rate (S–M)
**Goal.** One sentence now and then, at most six a day, never in the way.
**Files.** new `phrases.py`, `guard_scene.py` (re-export only), new `voice.py`, new `assets/nib_phrases.json` (LF), `behavior.py` (`_event`/`take_events` if not already), `main.py`, `config.py`, `settings.py` (CHOICES), `bubble.py` if `Toast.show_message` needs `on_click`, new `tests/smoke_voice.py`, new `tests/chatter_rate.py`, `tests/run_all.py`, `tests/smoke_guard_scene.py` (should pass untouched — verify), `nib.spec` (bundle `assets/nib_phrases.json` in datas, like guard_phrases.json), docs.
**Behaviour.**
- `chatter_level` setting (1.4). `Voice.may_speak` rules: level off → never; `clock − launch < LAUNCH_QUIET_S` → no; per-day count ≥ cap → no; `clock − last < gap` → no unless category starts with `greeting_` (still counted toward the day cap).
- `main._voice_blocked()` → True if guard up/active, `behavior.focus_active`, bubble visible, tool wheel visible, snoozed, hidden by fullscreen, ghost mode, `behavior.away`, behavior state in (SLEEP, WAKE, ANGRY, DRAG, HIDDEN), or the toast is already visible (never stack over intro/update/calculator).
- `main._say(category, **fmt)`: `if blocked: return`; `line = voice.say(category, name=..., **fmt)`; `if line: self._tell(line, duration_ms=4500, on_click=...)` where on_click → `behavior.noticed(); mood.nudge("greeted")` (WP5 adds `needs.nudge("petted", 0.5)`). `_tell` grows an `on_click=None` parameter (default None so existing call sites are untouched).
- `main._on_creature_event(kind, data)` map: `greeted` → `greeting_overnight` if overnight else `greeting_short` (only when `gone_s ≥ 600`), plus `heavy_yesterday` once per day if flagged, plus `early_today`/`late_today` once per day when `memory.usual_hour()` exists and `|hour − usual| ≥ 1.5`; `milestone` → `milestone_days`; `big_week` → `big_week`; later WPs add `tired`, `fond`, `treat`, `treat_full`, `nap_wake`.
- `assets/nib_phrases.json` categories (≥ 6 lines each, `{name}`, `{days}`, `{pets}` placeholders): `greeting_short, greeting_overnight, heavy_yesterday, early_today, late_today, milestone_days, milestone_pets, big_week, tired, fond, bored, treat, treat_full, nap_wake`. Tone: warm, short, a bit funny; observations, never questions or instructions, never "I saw you using…", never guilt-tripping. `voice.BUILTIN_PHRASES` has one fallback line per category.
**Tests.** `tests/smoke_voice.py` (Qt-free): level off → never; rare → second line within 44 min refused, 46 min allowed; 7th line of the day refused; greeting bypasses gap but not cap; launch-quiet; unknown category → `""`, no crash; user file merge via `load_phrases(..., filename=…)`; `_KeepUnknown` keeps stray braces. `tests/chatter_rate.py`: simulate a 10-hour working day with returns-from-away, milestones, needs events → assert ≤ 6 lines at "rare", ≤ 14 at "normal", 0 at "off". Register suite + rate.
**Docs.** USING-HIM.md "He says things, rarely" (caps, how to add lines, how to turn off); SETTINGS.md row; PRIVACY.md sentence; BEHAVIOUR.md cross-link; CHANGELOG.
**Acceptance.** `smoke_guard_scene.py` passes with the extraction; a greeting toast after a ≥10 min absence and never while the bubble is open; chatter numbers in the docs match `chatter_rate.py`; clicking the toast nudges mood.

### WP5 — `needs.py` and its moments (M)
**Goal.** Energy, affection and boredom colour everything; two threshold moments.
**Files.** new `needs.py`, `config.py`, `behavior.py`, `main.py`, `settings.py` (status labels), new `tests/smoke_needs.py`, `tests/run_all.py`, `tests/day_rate.py`, `tests/fidget_rate.py` (run, confirm unchanged), docs.
**Behaviour.**
- Settings 1.4; formulas 1.3. `Needs` copies `Mood`'s structure: `_load` with backwards-clock guard, `STORE_QUANTUM`, `persist` from `_flush_config`.
- Wiring per 1.6. Nudges: `_maybe_pet` → `petted`; `prompt_sent` → `prompt_sent`; `set_away(False)` → `greeted`; `_finish_focus` → `focus_done`; `drag_end` throw → `thrown`; `_settle` injury → `hurt`; `noticed()` → `interacted`; `_pick_moment` success / `foreground_changed` antic → `world_event`; toast click → `petted` at scale 0.5.
- `_pick_moment` new block after seasonal, own cooldown `NEEDS_MOMENT_COOLDOWN_MS = 90 min` (add to the cooldown loop in `update`): **tired** — `energy < 0.25` and `part_of_day in (PART_EVENING, PART_NIGHT)` → `_begin_sequence([("yawn", 2800), ("blink_slow", 800)])`, event `tired`; **fond** — `affection > 0.85` and cursor near him → `heart_eyes` 1400–2100, event `fond`. Both charge `_reaction_cooldown_ms` like every moment. Boredom high may emit a `bored` event at most once per 90 min via the same cooldown (voice line only, no new antic).
- Settings "Right now" line becomes `"{name} is {mood label} — {energy label}, {affection label}, {boredom label}."`; `_forget_memory` also calls `needs.reset()`.
**Tests.** `tests/smoke_needs.py` (Qt-free, FakeClock): neutral → `weights()=={}`, `pace()==1.0`, `peek_boost()==1.0`; energy climbs while away and drops at night; affection taper; boredom clears on `interacted`; offline ageing restores energy; disabled → constants; persist quantised (0 writes over 10 idle minutes, as `smoke_mood` does). Behavior section: `_mood_weights` at neutral equals pre-WP5 output; tired moment fires at most once per 90 min in a simulated night; `day_rate.py` adds an "exhausted evening" run. **Run `fidget_rate.py` and `quirk_rate.py` before/after: numbers must match within noise.** Register suite.
**Docs.** BEHAVIOUR.md new section "Energy, affection and boredom" (+ "never fires anything" + the anti-Tamagotchi rule); SETTINGS.md rows (visible + hidden); CHANGELOG.
**Acceptance.** Fresh config behaves identically to today (rate scripts); leaving the PC for an hour raises energy; `needs_enabled=false` → all effects gone and `need_*` not rewritten.

### WP6 — Bond effects, gentle handling, play (S–M)
**Goal.** Rough vs kind handling means something; the relationship shows.
**Files.** `behavior.py`, `tests/smoke_throw.py` (new section), `tests/smoke_pet.py` (new section), `docs/USING-HIM.md`, `docs/BEHAVIOUR.md`, `CHANGELOG.md`; tray tooltip may append the bond word: `"{name} — {mood label}, {bond label}"` (keep focus/away branches above it).
**Behaviour.**
- **Squirm**: `_tick_drag` struggle amplitude `STRUGGLE_AMPLITUDE_PX * (1.0 − 0.6 * memory.bond())`.
- **Forgiveness**: `_tick_pet` drain interval `PET_DRAIN_MS * (1.0 − 0.4 * bond)`.
- **Gentle set-down**: in `drag_end`, if `perch_rect is None`, release speed ≤ `PERCH_MAX_RELEASE_SPEED_PX_MS` and `rest_top() − self._y < INJURY_FALL_PX` → `self._gentle_pending = True`; `_settle` clears it and, if not injured and not resuming sleep, plays `sparkle` 900 ms, `needs.nudge("petted", 0.3)`, cooldown `GENTLE_COOLDOWN_MS = 30 s`.
- **Play**: in `_settle`, a throw (`abs(vx_at_release) > 0.4` recorded in `drag_end`) that did not injure → `needs.nudge("thrown")` and, if `affection > 0.6`, `sparkle` 900 ms ("again!"); injuries unchanged.
- **Wary**: `_mood_weights` multiplies `heart_eyes × 0.5` and `sparkle × 0.7` while hurts over the last 7 days ≥ 5 and > pets over the same window.
**Tests.** `smoke_throw.py`: gentle release → `sparkle` within 2 s, no injury, second gentle release inside 30 s → no sparkle; hard throw still `INJURED`. `smoke_pet.py`: bond 0.9 drains a poke faster. Neutral memory → amplitudes/drains equal today's constants.
**Docs.** USING-HIM.md "Picking him up…"; BEHAVIOUR.md "Petting" + new "Bond" paragraph (five words, never a number).
**Acceptance.** All existing `smoke_throw` physics checks pass; no new Win32 calls in behavior.py.

### WP7 — The nap (M)
**Goal.** Sleep that isn't a punishment.
**Files.** `behavior.py`, `main.py` (tooltip), `config.py`, `settings.py`, tests, docs.
**Behaviour.**
- Setting `nap_enabled`. No new `State`: reuse `State.SLEEP` with `self._napping: bool`. In `update` after `needs.tick`: if nap enabled and needs enabled and `self._away` and away ≥ 10 min and `needs.energy < 0.25` and `part_of_day == PART_NIGHT` and he's in his away pose → `self._napping = True; self._enter(State.SLEEP); self._sleep_ms = huge`. Track away time.
- `_tick_sleep`: if `_napping`, skip timer expiry and hover-wake; `set_away(False)` with `_napping` → `_napping=False; needs.nudge("nap_woke", scale=min(1, napped_h/2))`; `_enter(State.WAKE)`; then the normal greeting sequence runs after WAKE → IDLE (store a pending greeting outside `_antic_queue`, since `_enter` clears it). `click()` while napping → wake without grumble. He does not turn away (that's the sulk). `_neglect_suppressed` already covers SLEEP. `drag_start` mid-nap keeps the flag. `_event("nap_wake")`.
- Tooltip: `"{name} — napping"` when `behavior.napping` (new property).
**Tests.** away 11 min at 23:30 with energy 0.1 → `SLEEP` and `napping`; return → `WAKE` then greeting antic; click wakes without grumble; a sulk is **not** `napping` and still grumbles; no nap when `nap_enabled=false`, during focus, or while perched. Re-run `smoke_attention.py`, `smoke_focus.py`, `smoke_pet.py`.
**Docs.** BEHAVIOUR.md; SETTINGS.md row; USING-HIM.md tooltip states; CHANGELOG.
**Acceptance.** No existing SLEEP semantics change when `_napping` is False (grep every `State.SLEEP` reference and confirm).

### WP8 — Treat: the `eat` animation, the wheel tool, the sound (M + art)
**Goal.** A second kindness, with the one new animation.
**Files.** `tools/gen_sprites.py`, `assets/*` (regenerated), `tools/validate_sheet.py` (REQUIRED_STATES), `behavior.py`, `mood.py` (`NUDGES["treated"] = 0.12`), `main.py`, `tray.py`, `tool_wheel.py` (icon), `sound.py`, `tools/gen_sounds.py`, new `tests/smoke_treat.py`, docs incl. `docs/ART.md`.
**Behaviour.**
- `Behavior.treat() -> bool`: refuse in DRAG, HIDDEN, while falling/rolling; asleep (non-nap) → grumble as `click()` does and return False; napping → wake first. `self.noticed()`. If today's treats ≥ `TREATS_PER_DAY = 3` → `_begin_antic("sit_dangle", 1500)`, `_event("treat_full")`, return True (no nudges). Else `memory.record("treats")`, `mood.nudge("treated")`, `needs.nudge("treated")`, `_sound("nom")`, `_event("treat")`, `_begin_antic("eat", EAT_MS=1400.0)`. If the sheet lacks `eat` fall back to `heart_eyes`.
- `main._wheel_items` Him group, after `do_something`: `WheelItem("treat", "Give him a treat", "treat", group=him)`; `_on_tool_chosen`: `elif key == "treat": self.behavior.treat()`. Not in `TOOL_BEATS`. `tray.py`: "Give a treat" action next to "Do something" with a `treat_requested` signal wired in `main._connect`. `tool_wheel.py`: `_icon_treat` (a small round biscuit) in `ICONS`.
- `sound.EVENTS` += `"nom"`; `gen_sounds.SOUNDS["nom"]` = two short soft triangle tones (deterministic). USING-HIM.md "Sound" count +1.
- Do NOT add `eat` to `Behavior.ON_DEMAND` or `ANTICS` (`tests/smoke_quirks.py:354–363` pins the rare share).
**Art.** Copy `frame_coffee` (`tools/gen_sprites.py`) — body, legs, eyes, arm pose, `outline`, `measure_anchors` **before** stamping the prop, then `stamp_prop`.
```python
BISCUIT = [".LLL.", "LOLOL", "LOOOL", ".LLL."]   # body-palette letters only
EAT_KEYS = (
    (0,  "glint",  0, (39, 42), 0, ()),
    (0,  "closed", 2, (37, 25), 0, ()),
    (0,  "closed", 2, None,     1, ((34, 27), (40, 29))),
    (0,  "half",   0, None,     0, ((39, 44),)),
)
def frame_eat(index): ...  # see frame_coffee; draw_mouth "smile" on the last frame if that helper exists
```
Adapt helper names/arguments to what actually exists in gen_sprites.py. Cells: the next free run after `pet` (spec suggests 324–327; verify free). Manifest: `"eat": {"loop": False, "frames": [[324, 320], [325, 300], [326, 260], [327, 520]]}` (total 1400 ms). Validator: add `eat` to `REQUIRED_STATES`. Must pass: geometry, alpha 0/255 only, seven body colours (body PALETTE letters only), feet on baseline row 46, head/eyes anchors present. Check hats sit on his head on all four eat frames (`smoke_costumes.py` runs every costume on every frame; also look at a render). Make the biscuit read clearly at 1x (iterate with renders — view PNGs with the Read tool).
**Tests.** New `tests/smoke_treat.py` (register): treat → ANTIC with `eat`, today's treats == 1, mood rose; fourth treat → `sit_dangle`, no mood change; refused while dragged/hidden; sleeping → grumble.
**Docs.** USING-HIM.md table row + "Treats" paragraph; BEHAVIOUR.md nudge table `+0.12`; ART.md state/frame counts and a sentence on `eat`; CHANGELOG.
**Acceptance.** `gen_sprites.py` + `validate_sheet.py` clean; assets committed and reproducible; hats sit right on all eat frames.

### WP9 — Learned taste (S–M)
**Goal.** Each install drifts toward what its owner rewards.
**Files.** `behavior.py`, `memory.py`, `settings.py`/`main.py` (About him line), `tests/smoke_memory.py`, `tests/fidget_rate.py` (verify), docs.
**Behaviour.**
- Ambient antics only (from `_pick_antic` via `_choose_next_action`; pass an `ambient=True` flag) record `self._last_ambient = (name, clock_ms)`.
- `_maybe_pet` and `click()` (bubble actually opened): if `_last_ambient` within `TASTE_WINDOW_MS = 10_000` → `memory.adjust_taste(name, +0.03)`. `bubble_dismissed` → `−0.02`. Neglect stage 3 → `−0.02` for `_last_ambient` if within 5 min.
- `_mood_weights` multiplies `memory.taste` (1.0 for missing). New day in `first_sight` → `memory.decay_taste(1)`.
- About him: "Seems to like: hop, dance" for tastes > 1.15 (top two); "Not keen on: …" for < 0.85.
**Tests.** adjust/clamp/decay; pet within 10 s of an ambient `hop` raises `taste["hop"]`; a moment/persona antic does not count; neutral taste → weights unchanged. `fidget_rate.py` unchanged on fresh memory.
**Docs.** BEHAVIOUR.md "He develops tastes"; PRIVACY.md clause listing `taste` in memory.json; CHANGELOG.
**Acceptance.** Clamp holds after 1000 adjustments; `memory_enabled=false` → taste never written; rate scripts unchanged.

---

## 3. Deferred art (not now)
doze, droop idle, nuzzle — draw only after WP5–7 have been watched for a week.

## 4. Reviewer checklist (every WP)
- [ ] `py tests/run_all.py` green; `py tests/run_all.py --rates` run once per WP that touches weights/moments (check how run_all exposes rates), numbers in the docs if they moved.
- [ ] `py tools/check_docs.py` → "docs and code agree".
- [ ] WP8: `py tools/gen_sprites.py && py tools/validate_sheet.py assets/nib_sheet.png assets/frames.json`, assets committed, `git status` clean after regenerating.
- [ ] `grep -n PySide6 memory.py needs.py voice.py phrases.py behavior.py mood.py world.py` → nothing.
- [ ] No new Win32 calls in `behavior.py`.
- [ ] Every new `DEFAULTS` key: `_BOUNDS`/`_TEXT_KEYS`/`_VALID_*`, `GROUPS` or `HIDDEN`, SETTINGS.md row with the `as_text` default.
- [ ] Every new timed/scheduled behaviour has a rate script and a documented number.
- [ ] Line endings preserved (`git diff --stat` shows no whole-file rewrites); new files LF.
- [ ] CHANGELOG `[Unreleased]` bullet; README suite count if a suite was added.
- [ ] PRIVACY.md lists exactly what `memory.json` holds.

## 5. Risks and tricky spots
1. `Behavior.update` early returns: HIDDEN returns before anything ticks; paused returns before `mood.tick`. Needs won't tick while hidden/paused — same as mood; rely on wall-clock ageing; do not move the tick above those returns.
2. `_choose_next_action` ordering is load-bearing (perch → pocket → away → focus → quirks → eye-track → moments → neglect → ledge/peek → ambient → walk). New moments go inside `_pick_moment`; only add the `peek_boost` multiplier and the `ambient=True` flag there.
3. `_enter` clears `_antic_queue`, `_persona_costume`, `_pending_action` on any non-ANTIC entry — the nap's wake-then-greet must store its greeting elsewhere until WAKE reaches IDLE.
4. `_pick_antic` has an `if not any(weights)` guard — keep every multiplier ≥ 0.4.
5. Neutral must be exactly 1.0 — `fidget_rate.py`, `quirk_rate.py`, `day_rate.py` are the regression net.
6. `tests/smoke_ui.py:220–230`: every key in GROUPS or HIDDEN exactly once; "About him" tab adds no `_Field`.
7. `tests/smoke_quirks.py:354–363` pins `do_something`'s rare share; never add `eat` to `ON_DEMAND`/`ANTICS`.
8. `tests/smoke_focus.py:125`: tooltip focus/away branch must stay above the mood/nap text.
9. `State.SLEEP` is referenced in ~15 places; the nap flag must leave each behaving as today when `_napping` is False.
10. `Toast` is one shared widget (intro, update progress with 60 s durations, calculator, launch failure with on_click). `_say` checks `toast.isVisible()`; `_tell`'s new `on_click` defaults to None.
11. `Config._coerce` doesn't validate dict defaults — structured memory goes to `memory.json`, never dict/list keys in DEFAULTS.
12. `measure_anchors` before props in `frame_eat`.
13. `guard_scene.py` extraction: keep `PhraseBag`/`load_phrases`/`PHRASES_FILE` importable from `guard_scene`.
14. Memory write frequency: never record per tick; `save_if_dirty` only from `_flush_config`.
15. Two clocks: Behavior's `_clock_ms` vs wall time. Use `WorldState.date_key` for day keys everywhere.
