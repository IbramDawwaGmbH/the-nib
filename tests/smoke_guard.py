"""The Away Guard's rules: transitions, the suspicious-events table, backoff, peek timing, the log.

``guard.py`` is pure logic, so this runs with a fake clock and no Qt at all - nothing opens on
screen and no keyboard hook is installed.
"""

import logging
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import guard as G  # noqa: E402
from guard import GuardController, Mode  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def make(**kw):
    clock = Clock()
    g = GuardController(clock, **kw)
    seen = []
    g.add_listener(lambda old, new: seen.append((old, new)))
    return clock, g, seen


print("\n--- basic transitions ---")
clock, g, seen = make()
check("starts idle", g.mode is Mode.IDLE)
check("Mode is a str enum", Mode.FOG == "fog" and Mode("blackout") is Mode.BLACKOUT)
g.guard()
check("guard: IDLE -> FOG", g.mode is Mode.FOG)
check("listener hears (old, new)", seen == [(Mode.IDLE, Mode.FOG)], str(seen))
check("guarded_since set", g.guarded_since == clock.t)
g.guard()
check("guard again is a no-op", len(seen) == 1)
g.choose_peek()
g.choose_unlock()
check("peek/unlock need CHOICE", g.mode is Mode.FOG)
g.auth_ok()
check("auth_ok: FOG -> CHOICE", g.mode is Mode.CHOICE)
g.auth_ok()
check("auth_ok in CHOICE is ignored", g.mode is Mode.CHOICE)
g.choose_unlock()
check("choose_unlock: CHOICE -> IDLE", g.mode is Mode.IDLE and g.guarded_since is None)
g.auth_ok()
check("auth_ok while idle does nothing", g.mode is Mode.IDLE)
check("auth_failed while idle returns 0 and does not count",
      g.auth_failed() == 0.0 and g.attempts == 0)
g.guard()
g.auth_ok()
g.guard()
check("guard from CHOICE -> FOG", g.mode is Mode.FOG)

print("\n--- wrong attempts, backoff and blackout ---")
clock, g, seen = make(blackout_after=3)
g.guard()
waits = []
for i in range(8):
    waits.append(g.auth_failed())
    if i == 1:
        check("still fog after 2 wrong", g.mode is Mode.FOG)
    if i == 2:
        check("blackout at the 3rd wrong", g.mode is Mode.BLACKOUT)
check("backoff 1,2,4,8,16,30,30,30", waits == [1, 2, 4, 8, 16, 30, 30, 30], str(waits))
check("attempts counted", g.attempts == 8)
check("backoff_for(0) is 0, huge attempts capped",
      G.backoff_for(0) == 0 and G.backoff_for(10_000) == 30)
check("backoff_remaining is the last wait", g.backoff_remaining() == 30)
clock.advance(12)
check("backoff_remaining counts down", g.backoff_remaining() == 18)
clock.advance(100)
check("backoff_remaining floors at 0", g.backoff_remaining() == 0)
check("wrong attempts are logged", sum(e.kind == "auth_failed" for e in g.log) == 8)
check("no want_lock without lock_fallback", g.want_lock is False)
g.auth_ok()
check("auth_ok leaves blackout for CHOICE", g.mode is Mode.CHOICE)
check("auth_ok resets attempts and backoff", g.attempts == 0 and g.backoff_remaining() == 0)
check("log survives auth_ok", len(g.log) == 8)

clock, g, _ = make(blackout_after=1)
g.guard()
g.auth_failed()
check("blackout_after=1 blacks out on the first wrong", g.mode is Mode.BLACKOUT)

print("\n--- lock fallback ---")
clock, g, _ = make(lock_fallback=True)
g.guard()
for _ in range(9):
    g.auth_failed()
check("no want_lock at 9", g.want_lock is False)
g.auth_failed()
check("want_lock at 10", g.want_lock is True)
g.want_lock = False
g.auth_failed()
check("want_lock re-raised on the 11th", g.want_lock is True)
g.auth_ok()
check("auth_ok clears want_lock", g.want_lock is False)

print("\n--- suspicious events table ---")
for kind in ("blocked_key", "desktop_switch", "core_restarted"):
    clock, g, _ = make()
    g.guard()
    g.event(kind, "x")
    check(f"{kind} -> BLACKOUT", g.mode is Mode.BLACKOUT)
    check(f"{kind} logged", [e.kind for e in g.log] == [kind])

clock, g, _ = make()
g.guard()
g.event("foreground_stolen", "agent.exe")
check("foreground_stolen without input stays FOG", g.mode is Mode.FOG)
check("... but is logged", g.log and g.log[-1].kind == "foreground_stolen"
      and g.log[-1].detail == "agent.exe")
g.event("foreground_stolen", "notepad.exe", recent_input=True)
check("foreground_stolen with recent input -> BLACKOUT", g.mode is Mode.BLACKOUT)

clock, g, _ = make()
g.guard()
g.event("mystery_thing", "?")
check("unknown kind: logged, mode unchanged", g.mode is Mode.FOG and g.log[-1].kind == "mystery_thing")
g.event("input")
g.event("session_lock")
g.event("session_unlock")
check("input / session_* not logged and harmless", g.mode is Mode.FOG and len(g.log) == 1)

clock, g, seen = make()
g.event("blocked_key", "LWin")
check("events ignored while IDLE", g.mode is Mode.IDLE and not g.log)
g.guard()
g.auth_ok()
g.event("blocked_key", "LWin")
check("events ignored in CHOICE", g.mode is Mode.CHOICE and not g.log)
g.choose_peek()
g.event("desktop_switch")
check("events ignored in PEEK", g.mode is Mode.PEEK and not g.log)
g.lock_now()
g.event("blocked_key", "Alt+Tab")
n = len(seen)
g.event("blocked_key", "Alt+F4")
check("blackout is sticky; a second event only logs", g.mode is Mode.BLACKOUT
      and len(seen) == n and len(g.log) == 2)
g.guard()
check("guard() doesn't lift a blackout", g.mode is Mode.BLACKOUT)
g.tick()
check("tick doesn't lift a blackout", g.mode is Mode.BLACKOUT)

print("\n--- peek timing ---")
clock, g, seen = make(peek_minutes=3)
check("peek_remaining 0 outside PEEK", g.peek_remaining() == 0 and g.peek_total == 0)
g.guard()
g.auth_ok()
g.choose_peek()
check("CHOICE -> PEEK", g.mode is Mode.PEEK)
check("peek is 180 s", g.peek_remaining() == 180 and g.peek_total == 180)
clock.advance(100)
g.tick()
check("still peeking at 100 s", g.mode is Mode.PEEK and g.peek_remaining() == 80)
g.extend_peek()
check("extend adds 3 min", g.peek_remaining() == 260 and g.peek_total == 360)
clock.advance(259)
g.tick()
check("still peeking 1 s before expiry", g.mode is Mode.PEEK)
clock.advance(1)
g.tick()
check("tick at expiry -> FOG", g.mode is Mode.FOG and seen[-1] == (Mode.PEEK, Mode.FOG))
check("peek_remaining 0 after expiry", g.peek_remaining() == 0)
g.extend_peek()
check("extend_peek outside PEEK does nothing", g.mode is Mode.FOG)

clock, g, _ = make(peek_minutes=2)
g.guard()
since = g.guarded_since
g.auth_ok()
g.choose_peek()
check("peek_minutes honoured", g.peek_remaining() == 120)
clock.advance(130)  # past expiry, but the tick hasn't run yet
g.extend_peek()
check("late extend gives the full extra time", g.peek_remaining() == 120, str(g.peek_remaining()))
g.lock_now()
check("lock_now: PEEK -> FOG", g.mode is Mode.FOG)
check("re-guard keeps the original guarded_since", g.guarded_since == since)
g.lock_now()
check("lock_now outside PEEK does nothing", g.mode is Mode.FOG)
g.auth_ok()
g.choose_peek()
g.guard()
check("guard() from PEEK -> FOG", g.mode is Mode.FOG)

print("\n--- session unlock ---")
for start in ("fog", "blackout", "peek"):
    clock, g, _ = make()
    g.guard()
    if start == "blackout":
        g.event("desktop_switch")
    if start == "peek":
        g.auth_ok()
        g.choose_peek()
    g.session_unlocked()
    check(f"session_unlocked ends from {start}", g.mode is Mode.IDLE)
clock, g, seen = make()
g.session_unlocked()
check("session_unlocked when idle is a no-op", not seen)
clock, g, _ = make()
g.guard()
g.event("desktop_switch")
g.auth_ok()
g.choose_unlock()
check("contract path auth_ok + choose_unlock works too", g.mode is Mode.IDLE)

print("\n--- listeners and the log ---")
clock, g, _ = make()
calls = []


def broken(old, new):
    raise RuntimeError("boom")


g.add_listener(broken)
g.add_listener(lambda old, new: calls.append(new))
g.guard()
check("a failing listener doesn't stop the others", calls == [Mode.FOG] and g.mode is Mode.FOG)
clock.advance(5)
g.auth_failed()
check("log entry timestamped by the clock", g.log[0].t == clock.t
      and g.log[0].detail == "attempt 1")
g.auth_ok()
g.choose_unlock()
check("log kept after unlock (for the card)", len(g.log) == 1)
g.clear_log()
check("clear_log empties it", g.log == [])
g.guard()
check("new guard starts with zero attempts", g.attempts == 0 and g.backoff_remaining() == 0)

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "guard controller OK")
sys.exit(1 if fails else 0)
