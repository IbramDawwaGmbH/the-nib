"""The mascot's state machine: wandering, gait, cursor reactions, annoyance, and sleep.

Deliberately free of Qt so the behaviour can be reasoned about (and tested) on its own. It reads
geometry from the sprite sheet and owns the ``Animator``, because the walk maths and the animation
clock have to stay in lockstep.

**Why the feet do not slide.** The sheet promises that one walk frame of leg travel covers
``walk_step_px`` logical pixels, and that a planted leg's offset steps back by exactly that much
each frame. So the mascot must advance exactly ``walk_step_px * scale`` per animation frame - no
more, no less. Rather than integrate a velocity independently of the animation (the classic
moonwalk bug), ``_walk`` derives the distance from the animation's own progress: the same scaled
delta drives both. Easing in and out then works by slowing the *animation* down, which is also how
a real gait slows - shorter time per step, same stride length.
"""

from __future__ import annotations

import enum
import logging
import math
import random
import time
from collections import deque

from animator import Animator, SpriteSheet
from memory import Memory
from mood import Mood
from needs import BORED_ABOVE, Needs
from world import (
    PART_DAY,
    PART_EVENING,
    PART_MORNING,
    PART_NIGHT,
    SEASON_AUTUMN,
    SEASON_SPRING,
    SEASON_SUMMER,
    SEASON_WINTER,
    WorldState,
    parse_clock,
)

log = logging.getLogger(__name__)


class State(enum.Enum):
    IDLE = "idle"
    WALK = "walk"
    INJURED = "injured"
    ANTIC = "antic"
    HOVER_WAVE = "hover_wave"
    ASK = "ask"
    WORKING = "working"
    ANGRY = "angry"
    SLEEP = "sleep"
    WAKE = "wake"
    HIDDEN = "hidden"
    DRAG = "drag"
    PEEK = "peek"
    PET = "pet"
    PERCH = "perch"


# --- tuning that is not worth a config key ------------------------------------------

BLINK_MIN_MS, BLINK_MAX_MS = 2600, 7000
WAVE_DURATION_MS = 2100
WAVE_COOLDOWN_MS = 9000
WORKING_MIN_MS, WORKING_MAX_MS = 1500, 2500
ANGRY_DURATION_MS = 2500
GRUMBLE_MS = 380
STORM_OFF_MIN_PX, STORM_OFF_MAX_PX = 90, 190
TURN_AROUND_CHANCE = 0.35

#: Blinks come in three kinds. A single blink is one 120ms frame and reads as punctuation; the
#: double is two in quick succession; the slow one holds half-mast lids and reads as tiredness.
#: Which he uses is a MOOD, not a random pick - a sleepy blink at eleven in the morning says
#: nothing, and the same blink at midnight says everything.
BLINK_SINGLE = "blink"
BLINK_DOUBLE = "blink_double"
BLINK_SLOW = "blink_slow"
#: (double, slow) chance by part of day; the remainder is an ordinary single blink.
BLINK_MIX_BY_PART = {
    PART_NIGHT: (0.15, 0.55),
    PART_EVENING: (0.2, 0.3),
    PART_MORNING: (0.2, 0.35),
    PART_DAY: (0.25, 0.05),
}

#: A window appearing in front of him makes him jump. Short, and on its own cooldown so that
#: alt-tabbing repeatedly does not turn him into a twitch.
STARTLE_ANIM = "startle"
STARTLE_MS = 700.0
STARTLE_COOLDOWN_MS = 25000.0
STARTLE_CHANCE = 0.45

#: How long he keeps his eyes on the pointer, and how long before he will do it again.
EYE_TRACK_MIN_MS, EYE_TRACK_MAX_MS = 1800.0, 3200.0
EYE_TRACK_COOLDOWN_MS = 7000.0
EYE_TRACK_STATE = "eyes_track"

#: The idle antics pool: (animation state, weight, min ms, max ms, category).
#: "eyes" antics are the attention-seeking ones and get weighted up when the cursor is nearby;
#: "activity" antics are what he does to amuse himself when nobody is around; "fidget" antics are
#: the short ones that fill the gaps, and they are the only category that can be switched off on
#: its own.
ANTICS: tuple[tuple[str, float, float, float, str], ...] = (
    ("look_around", 1.4, 1800, 2800, "eyes"),
    ("wink", 1.0, 1000, 1500, "eyes"),
    ("sparkle", 1.0, 1200, 1900, "eyes"),
    ("heart_eyes", 0.7, 1400, 2100, "eyes"),
    ("football", 1.1, 2600, 4400, "activity"),
    ("dance", 1.2, 2400, 4000, "activity"),
    ("pushups", 0.9, 2400, 3800, "activity"),
    ("hop", 1.1, 1300, 2100, "activity"),
    ("sit_dangle", 0.8, 2600, 4400, "activity"),
    # Under a second each, and none of them has anything you would notice on purpose. The whole
    # point is the gaps: every other entry above is a set piece, and a creature made entirely of
    # set pieces reads as a creature waiting for its next cue.
    ("scratch", 1.3, 700, 1100, "fidget"),
    ("foot_tap", 1.2, 800, 1300, "fidget"),
    ("check_watch", 0.8, 900, 1400, "fidget"),
)
FIDGET_CATEGORY = "fidget"
#: Multiplier applied to "eyes" antics while the pointer is close - he plays to an audience.
EYES_ANTIC_AUDIENCE_BOOST = 2.5
#: An antic asked for longer than this is a hold - the away-sit, the sign, the focus pose, the
#: give-up sit - ended by something else rather than by its own clock, so it never waits for a
#: clean frame. Detected rather than replacing the 1e9 literals, which tests pin (smoke_focus).
ANTIC_HOLD_MS = 1e8

# --- personality presets -----------------------------------------------------------------
#
# A LENS over the settings, not a rewrite of them. The user's own numbers stay in the file exactly
# as they typed them and every preset is a set of multipliers applied at the point of use, so
# switching back to "balanced" restores what they had rather than what a preset last wrote over it.
# That also means a preset and a hand-tuned config compose instead of fighting.
#
# Keys: antic_chance / beat / pace / annoyance are multipliers; weights composes into the ambient
# pool the same way the time of day and the mood do.
PERSONALITIES: dict[str, dict] = {
    "balanced": {},
    "chill": {
        "antic_chance": 0.8,
        "beat": 2.4,          # much longer pauses between activities
        "pace": 0.85,
        "annoyance": 1.75,    # takes a lot more poking to bother him
        "weights": {
            "sit_dangle": 2.2, "look_around": 1.5, "scratch": 1.3,
            "dance": 0.5, "pushups": 0.4, "hop": 0.6, "football": 0.7,
        },
    },
    "hyper": {
        "antic_chance": 1.3,
        "beat": 0.45,
        "pace": 1.18,
        "annoyance": 0.85,
        "weights": {
            "hop": 2.2, "dance": 1.9, "football": 1.7, "pushups": 1.5, "sparkle": 1.3,
            "foot_tap": 1.6, "sit_dangle": 0.2, "check_watch": 0.4,
        },
    },
    "sassy": {
        "antic_chance": 1.05,
        "beat": 0.85,
        "pace": 1.06,
        "annoyance": 0.5,     # half the patience
        "weights": {
            "wink": 2.6, "look_around": 1.4, "check_watch": 1.9, "heart_eyes": 0.5,
            "pushups": 0.7, "sit_dangle": 0.7,
        },
    },
}

# --- being petted --------------------------------------------------------------------------
#
# Everything else in this file is something he does TO you or ABOUT you. This is the one thing you
# do to him, and until it existed the whole interaction model ran one way: poke, poke, poke, sulk.
#
# The trigger is deliberately stricter than the wave's: the pointer must be *on him*, not merely
# near, and it must hold still. A wave costs nothing if it misfires; leaning into a hand that is
# not there does not read as affection, it reads as a bug.

#: How far the pointer may drift and still count as held still. A hand resting on a mouse moves a
#: pixel or two; zero tolerance would mean nobody could ever pet him.
PET_JITTER_PX = 3
#: Longest he will keep going, in case a pointer is simply abandoned on top of him.
PET_MAX_MS = 6000.0
#: One accumulated poke forgiven per this long of being petted.
PET_DRAIN_MS = 1200.0
#: A breath before he can be petted again, on top of the re-arm rule below.
PET_COOLDOWN_MS = 2500.0
PET_ANIM = "pet"

# --- reacting to what you asked for ---------------------------------------------------------
#
# He already has your prompt: you typed it into his own bubble. So this costs nothing in privacy
# and nothing in new art - it is a keyword table over states that already exist, played after the
# working animation instead of dropping straight back to idle.
#
# The text is matched and discarded. It is never logged, at any level.

#: Anything longer than this gets a double-take regardless of what it says.
PROMPT_LONG_CHARS = 420
#: (keywords, animation, min ms, max ms). First match wins, so the order is the priority order:
#: gratitude beats everything, and the length check is the fallback rather than the headline.
PROMPT_REACTIONS: tuple[tuple[tuple[str, ...], str, float, float], ...] = (
    (("thank", "thanks", "cheers", "appreciate", "you're the best", "youre the best"),
     "heart_eyes", 1500, 2200),
    (("```", "def ", "class ", "import ", "function ", "const ", "select ", "#include", "</"),
     "typing", 1800, 2600),
    (("fix", "bug", "error", "broken", "crash", "failing", "fails", "debug", "stack trace"),
     "strain", 2000, 3000),
    (("email", "reply", "draft", "letter", "message to"), "mail", 1800, 2600),
    (("why", "how does", "how do", "explain", "what is", "what's the difference"),
     "look_around", 1600, 2400),
)
#: Played for a wall of text when nothing else matched.
PROMPT_LONG_ANIM = "startle"

#: The inline calculator answering something is a different event from a prompt going anywhere -
#: see ``Behavior.calculated`` - but it borrows this animation from the Calculator-app persona
#: further down. That entry's costume field is empty, so reusing it here carries no hat along and
#: needs no new art, the same free reuse ``PROMPT_REACTIONS`` above already makes of "typing" and
#: "mail".
CALCULATED_ANIM = "counting"
CALCULATED_MS = (1800.0, 2600.0)

#: Shift-dropping an image to read text out of it is, again, a different event from a prompt
#: going anywhere - see ``Behavior.read_image``. Reuses the same "look_around" animation the
#: why/how/explain prompt reactions above already use, rather than a new "reading" state: he's
#: making sense of something either way, and free reuse beats new art here too.
READING_ANIM = "look_around"
READING_MS = (1600.0, 2400.0)

#: Talking into a microphone nothing can hear - see ``Behavior.talking_while_muted``. A wave,
#: because this is the one thing he ever has to tell *you* rather than react to you having done
#: something, and waving is already how he gets your attention. The earmuffs are the existing
#: costume that most directly reads as "this is not being heard", applied as a costume= kwarg on
#: the antic exactly as FOCUS_COSTUME and TASK_FINISHED_ANIM's "party" already are, so it needs no
#: new art and is gone the instant the antic ends rather than touching your `costume` setting.
MUTED_ANIM = "wave"
MUTED_COSTUME = "earmuffs"
MUTED_MS = 2000.0

#: While you are away he settles down to wait, and greets you when you come back. This is
#: deliberately *sitting*, not sleeping. Two things put him to sleep: being pestered (the sulk),
#: and being worn out while he waits for you late at night (the nap - see NAP_AFTER_AWAY_MS).
AWAY_ANTIC = "sit_dangle"
GREET_ANTIC = "hop"
GREET_MS = 1500.0
#: How big the greeting is depends on how long you were gone: a quick wave for a coffee, the hop for
#: a meeting, and after a night away the full wake-up - a stretch, a yawn, and then the hop.
GREET_WAVE_ANTIC = "wave"
GREET_WAVE_MS = 1400.0
GREET_HOP_AFTER_S = 600.0          # 10 min: under this, a wave
GREET_OVERNIGHT_AFTER_S = 14400.0  # 4 h: overnight, or near enough
GREET_OVERNIGHT: tuple[tuple[str, float], ...] = (
    ("stretch", 1600.0), ("yawn", 2000.0), (GREET_ANTIC, GREET_MS),
)

#: Coming back from being hidden - a snooze ending, or a fullscreen app closing - gets a wave. Not
#: noticed(), and no mood: he was put away, nobody came back to him.
BACK_ANTIC = "wave"
BACK_MS = 1200.0
BACK_MIN_HIDDEN_MS = 20000.0   # a flicker of fullscreen is not an absence
BACK_COOLDOWN_MS = 60000.0     # alt-tabbing in and out of a game gets one, not one each time

# --- being ignored ---------------------------------------------------------------------
#
# "Away" means no input at all - you left. This is the other case: you are right here, working, and
# none of it is aimed at him. Three stages, and then it STOPS. It never escalates past the third,
# which is both less irritating and truer to life - real attention-seeking peaks and subsides.
#
# There is deliberately no stage where he walks over to your cursor. A mascot that leaves its spot
# and comes at your pointer is the version of this that gets the app uninstalled.

#: Multiples of ``attention_after_s`` at which each stage fires.
NEGLECT_GLANCE_AT = 1.0     # he starts looking your way more. Invisible unless you are watching.
NEGLECT_WAVE_AT = 2.0       # one unprompted wave, cursor nowhere near
NEGLECT_GIVE_UP_AT = 3.5    # sits down and waits
#: Weight boost on the attention-seeking antics once he has noticed being ignored.
NEGLECT_EYES_BOOST = 2.0
#: He sits, but glances up now and then, so it reads as *still hoping* rather than settled in for
#: the duration - which is what the identical away-sit means.
NEGLECT_SIT_ANTIC = "sit_dangle"
NEGLECT_GLANCE_ANTIC = "look_around"
NEGLECT_GLANCE_EVERY_MS = 21000.0
NEGLECT_GLANCE_MS = 1600.0

#: Reacting to whatever app you just switched to.
FOREGROUND_REACT_CHANCE = 0.5
FOREGROUND_COOLDOWN_MS = 12000.0
#: Executables that make him happy to see you. Matched on the executable name only.
CLAUDE_PROCESSES = frozenset({"claude.exe"})
CLAUDE_ANTICS = ("heart_eyes", "sparkle", "wink")

#: App personas: (animation, temporary headwear, min ms, max ms, executables).
#: Matched on the executable name alone, exactly like every other app reaction - never on a window
#: title. A hard-coded list is inevitably incomplete, which is why ``persona_apps`` in the config
#: is merged over the top: extending it is a settings edit rather than a code change.
_PERSONA_GROUPS: tuple[tuple[str, str, float, float, tuple[str, ...]], ...] = (
    (
        "typing", "glasses", 2600, 4200,
        (
            "code.exe", "code - insiders.exe", "codium.exe", "vscodium.exe",
            "cursor.exe", "windsurf.exe", "devenv.exe", "pycharm64.exe", "idea64.exe",
            "webstorm64.exe", "clion64.exe", "rider64.exe", "goland64.exe",
            "sublime_text.exe", "zed.exe",
        ),
    ),
    ("counting", "", 2200, 3200,
     ("calculatorapp.exe", "calculator.exe", "calc.exe", "win32calc.exe")),
    ("spreadsheet", "", 2800, 4200,
     ("excel.exe", "et.exe", "gnumeric.exe", "scalc.exe")),
    (
        "mail", "", 2400, 3600,
        (
            "outlook.exe", "olk.exe", "hxoutlook.exe", "hxmail.exe", "thunderbird.exe",
            "mailspring.exe", "bluemail.exe", "emclient.exe", "postbox.exe",
        ),
    ),
)
PERSONAS: dict[str, tuple[str, str, float, float]] = {
    exe: (antic, costume, low, high)
    for antic, costume, low, high, executables in _PERSONA_GROUPS
    for exe in executables
}
#: How long a persona reaction runs when it comes from ``persona_apps`` rather than the table.
CUSTOM_PERSONA_MS = (2400.0, 3600.0)
GRAVITY_PX_PER_MS2 = 0.0028
MAX_FALL_SPEED_PX_MS = 3.2

# --- throwing him ----------------------------------------------------------------------
#
# Releasing him used to discard everything but his position: he dropped straight down however fast
# you were moving. Now the drag's velocity is carried into the fall, so a flick sends him arcing
# across the taskbar, bouncing off the ends, and rolling to a stop.

#: Velocity is averaged over this much of the *end* of the drag rather than taken from the last
#: two positions. That is the whole difference between a flick and "he flies whenever you let go":
#: drag him, hold still for a moment, release, and a windowed average is reliably zero where a
#: two-sample delta is only zero if the final sample happened to be stationary.
THROW_SAMPLE_MS = 140.0
#: Hard ceiling on launch speed. A violent flick should not fire him across three monitors and
#: leave you hunting for where he went.
MAX_THROW_SPEED_PX_MS = 4.0
#: Above this release speed, a drop over a window is a throw, not a placement - the same reading
#: _throw_velocity already takes to arc a flick across the taskbar decides here whether he sticks
#: to a window or flies through it like anywhere else, so a fling that happens to end over one does
#: not surprise you by grabbing it.
PERCH_MAX_RELEASE_SPEED_PX_MS = 0.5
#: Exponential decay per millisecond. Air is thin; the ground is not.
AIR_DRAG_PER_MS = 0.9992
ROLL_FRICTION_PER_MS = 0.997
#: Energy kept when he hits the end of the strip, and when he lands.
WALL_RESTITUTION = 0.45
BOUNCE_RESTITUTION = 0.35
#: Below these he stops bouncing / stops rolling and gets up.
MIN_BOUNCE_SPEED_PX_MS = 0.45
MIN_ROLL_SPEED_PX_MS = 0.06
#: Hitting the end of the strip this fast hurts, however short the drop was. Deliberately a
#: *separate* rule from INJURY_FALL_PX rather than a replacement: that threshold was tuned against
#: real drops, and rewriting it in terms of speed would mean re-tuning something already verified.
WALL_IMPACT_HURT_PX_MS = 1.5

#: Drop him from higher than this and he lands hurt: bandaged, seeing stars, wobbling on his feet.
#: Far enough that shuffling him along the taskbar never triggers it, low enough that actually
#: picking him up and letting go always does.
INJURY_FALL_PX = 60
INJURY_ANIM = "injured"
#: Played for the whole descent, however he got airborne.
TUMBLE_ANIM = "tumble"

#: While held by the cursor he squirms. Amplitude is in logical px (scaled by the sprite scale);
#: the two axes run at different rates and the x axis is amplitude-modulated, so it reads as
#: bursts of wriggling in every direction rather than a mechanical left-right shake.
STRUGGLE_AMPLITUDE_PX = 3.5
STRUGGLE_X_RATE = 0.019
STRUGGLE_X_BURST_RATE = 0.0068
STRUGGLE_Y_RATE = 0.0131

# --- how he is handled, and how well he knows you --------------------------------------------
#
# The bond is memory.bond(): 0..1, from days together, pets, prompts and focus sessions, and it only
# ever grows. Here it softens the two things that are about trust - how hard he fights being held,
# and how quickly a pet takes the sting out of being poked. At a bond of 0 - memory switched off,
# or a fresh memory before he has first seen you - both are exactly the constants above. Not quite
# on day one: that first sighting is already a little bond, so he squirms 4% less, and the first pet
# forgives 4% sooner, since it is counted before it starts forgiving. Each pet after adds a little.

#: How much of the squirm a full bond takes away: at 1.0 he wriggles at 40% of a stranger's
#: amplitude. Never all of it - being hoisted about is undignified however well he knows you.
SQUIRM_BOND_CALM = 0.6
#: How much quicker a full bond lets a pet forgive: at 1.0, one poke every 0.72s rather than 1.2s.
FORGIVE_BOND_SPEEDUP = 0.4

#: Faster than this sideways at the release and a let-go is a throw: counted, and played with.
THROWN_ABOVE_PX_MS = 0.4
#: Set down gently - slowly enough to perch (PERCH_MAX_RELEASE_SPEED_PX_MS), not thrown, and from
#: lower than the drop that hurts - and he sparkles: you put him down carefully. Worth a fraction
#: of a pet to his affection, once per GENTLE_COOLDOWN_MS on the behaviour clock: without the
#: cooldown, shuffling him along the taskbar would be a way to farm it.
GENTLE_ANIM = "sparkle"
GENTLE_MS = 900.0
GENTLE_COOLDOWN_MS = 30000.0
GENTLE_PET_SCALE = 0.3
#: Thrown, landed unhurt, and fond of you right now: the same sparkle, meaning "again!". No
#: cooldown - every throw is one you made on purpose - and no nudge of its own, because drag_end
#: has already nudged "thrown" for the throw itself.
PLAY_ANIM = "sparkle"
PLAY_MS = 900.0
PLAY_AFFECTION_ABOVE = 0.6

#: Hurt a lot lately, and more often than petted: he goes wary, and the warm looks thin out. Over
#: the last WARY_DAYS days, at least WARY_MIN_HURTS hard landings AND more of them than pets - so a
#: bad afternoon from somebody who pets him every day is not enough. A lean like the needs', never
#: a switch, and above the 0.4 floor every lean keeps.
WARY_DAYS = 7
WARY_MIN_HURTS = 5
WARY_WEIGHTS = {"heart_eyes": 0.5, "sparkle": 0.7}

# --- world awareness ------------------------------------------------------------------
#
# Two quite different mechanisms, and keeping them apart is what stops this turning into a dozen
# new states:
#
# * **Moments** are one-off reactions to something specific - a stretch first thing, a dance at
#   knocking-off time, fanning himself when the machine is pinned. They are priority checks in
#   ``_choose_next_action``, in the same slot the cursor-tracking check occupies, *not* members of
#   the ambient pool with a time-varying weight.
# * **Mood** never fires anything. It leans on the weights of the antics he already has and on how
#   fast he plays them, which is how "droopier and slower late at night" costs no new art at all.

#: One budget shared by every moment. Per-trigger cooldowns would let a busy machine, a Friday
#: afternoon and an app switch all land in the same second, which reads as a short circuit rather
#: than as a character.
REACTION_COOLDOWN_MS = 45000.0

#: Per-kind limits on top of that. These are LONG, and measuring is what set them: at the first
#: values (90s / 4min / 5min) a simulated day fired the seasonal moment 202 times - one every seven
#: minutes - and the morning yawn 66 times. Each was individually correct and collectively a tic.
#: The rates below work out at roughly 5 morning yawns, a dozen seasonal flourishes, and a fan
#: every five minutes while the machine is actually pinned.
HOT_COOLDOWN_MS = 300000.0        # 5 min
MORNING_COOLDOWN_MS = 3600000.0   # 1 hour
SEASONAL_COOLDOWN_MS = 5400000.0  # 90 min
#: Shorter than the others on purpose: a build finishing is its own natural rate limiter (it needs
#: another `task_min_busy_s` of sustained load before it can fire again at all), so this only needs
#: to guard against one busy-then-quiet episode being read as two if the load dips right at the
#: edge of `task_quiet_percent` on the way down.
TASK_FINISHED_COOLDOWN_MS = 180000.0  # 3 min

#: A once-a-day moment needs a *window* as well as a latch. Without one, launching him at six in
#: the evening fires the four o'clock celebration two hours late - and so does waking from sleep.
CLOCK_OFF_WINDOW_H = 0.25
WEEK_OVER_DELAY_H = 0.5
WEEK_OVER_WINDOW_H = 2.0
#: Before this hour a new calendar date is still last night, so a machine left running does not
#: get its "first sight of you today" stretch at midnight.
NEW_DAY_HOUR = 5.0

MORNING_MOMENT_CHANCE = 0.55
SEASONAL_MOMENT_CHANCE = 0.4
FRIDAY_AFTERNOON_FROM_H = 13.0
FRIDAY_DANCE_BOOST = 3.0

#: How much slower the ambient animation runs late on. Reactions are never slowed - a slow-motion
#: tantrum reads as a bug, not as tiredness.
_PACE_BY_PART = {PART_NIGHT: 0.7, PART_EVENING: 0.87}

#: Weight multipliers on the ambient pool. Anything absent keeps its normal weight.
_PART_OF_DAY_WEIGHTS: dict[str, dict[str, float]] = {
    PART_NIGHT: {
        "dance": 0.15, "pushups": 0.2, "football": 0.3, "hop": 0.4,
        "sit_dangle": 2.6, "look_around": 1.4,
    },
    PART_MORNING: {"dance": 0.6, "look_around": 1.4, "sit_dangle": 1.3},
    PART_EVENING: {"pushups": 0.6, "football": 0.7, "sit_dangle": 1.5},
}
_SEASON_WEIGHTS: dict[str, dict[str, float]] = {
    # Spring has no dedicated art; it is bright and bouncy purely by weighting.
    SEASON_SPRING: {"sparkle": 1.8, "hop": 1.6, "heart_eyes": 1.5},
    SEASON_SUMMER: {"pushups": 0.5, "football": 0.7, "sit_dangle": 1.4},
    SEASON_AUTUMN: {"sit_dangle": 1.5, "look_around": 1.4},
    SEASON_WINTER: {"hop": 1.5, "dance": 1.3, "sit_dangle": 0.7},
}
#: Played when the machine is genuinely pinned. Summer shares it, which is why summer defers.
CPU_ANIM = "hot"
#: Played once a sustained-busy stretch drops back to quiet - a render, build, export or install
#: finishing. Its own duration, not a random range like the other moments: the celebration is a
#: fixed sequence (wind-up, punch, a twinkling held sparkle, settle), so playing it for anything
#: shorter would cut it off mid-gesture and anything much longer would just repeat the smug ending.
TASK_FINISHED_ANIM = "task_finished"
#: 2240ms of animation (200+160+380+380+220+900) plus a 60ms buffer. The buffer is no longer what
#: guarantees the smug settle: the animation runs on the mood-paced clock and this on real time, so
#: at night it used to be cut off two frames short. _antic_settled lets a one-shot finish at any pace.
TASK_FINISHED_MS = 2300.0

# --- what he remembers, acted on ----------------------------------------------------------
#
# memory.py keeps the counts; these are the few places they change what he does. None of them is a
# new animation and none of them says anything yet - the lines arrive with voice.py. They are here
# so that a day like any other looks like any other, and a day that is not, does not.

#: Days together worth a party: a week, a month, a hundred days, a year. Matched on the count of
#: days he has actually seen you, not on the calendar since first_met - a fortnight's holiday does
#: not make a month. Once each, ever: the latch is the count itself (last_milestone_days).
MILESTONE_DAYS = (7, 30, 100, 365)
#: The third focus session finished in one day earns the party again, once that day. Exactly the
#: third, not "three or more": a fourth is not a new achievement, and the latch would stop it anyway.
FOCUS_STREAK = 3
#: After a heavy yesterday, the overnight greeting ends with a coffee. Same art as the morning
#: coffee moment, so it reads as "you look like you need one" without a word.
GREET_HEAVY_EXTRA: tuple[str, float] = ("coffee", 2600.0)
#: Once he knows you well, the hop is followed by heart eyes. The same threshold at which
#: memory._BOND_LABELS starts calling the bond "close", so the word and the greeting agree.
GREET_BOND_MIN = 0.6
GREET_BOND_EXTRA: tuple[str, float] = ("heart_eyes", 1400.0)
#: Events waiting for the app to drain - see _event. The same cap as the sound queue, for the same
#: reason: nothing drains it yet, and a forgotten queue must not grow for a whole session.
MAX_PENDING_EVENTS = 8

# --- what he needs, acted on --------------------------------------------------------------
#
# needs.py keeps energy, affection and boredom, and almost all they do is lean on weights. These are
# the two exceptions - moments, so they go through _pick_moment like every other moment and share
# its 45-second budget - plus one thing that is only ever a line, never an antic.

#: One cooldown shared by all three below, on top of the shared moment budget. Long, like the
#: seasonal one and for the same reason: "he is tired" is a state of the evening, and a yawn every
#: few minutes for it would turn a mood into a tic.
NEEDS_MOMENT_COOLDOWN_MS = 5400000.0  # 90 min
#: Worn out in the evening or at night: a big yawn and a heavy blink. Not in the day - a yawn at
#: three in the afternoon reads as boredom, and boredom has its own lean.
TIRED_ENERGY_BELOW = 0.25
TIRED_SEQUENCE: tuple[tuple[str, float], ...] = (("yawn", 2800.0), (BLINK_SLOW, 800.0))
#: Doted on, and your pointer is close: heart eyes, at you.
FOND_AFFECTION_ABOVE = 0.85
FOND_ANIM = "heart_eyes"
FOND_MS = (1400.0, 2100.0)
#: Interacting, for boredom's purposes, means the being-ignored clock was reset this recently - so
#: boredom builds on the same clock that being ignored does, and only while that clock is running.
#: Whenever it is on hold (asleep, away, the machine pinned, or the attention arc switched off)
#: boredom stops building and fades instead - see update.
INTERACTING_WITHIN_MS = 1000.0

# --- the nap ---------------------------------------------------------------------------------
#
# Sleep that is not a punishment. The sulk is something you did to him; this is something he does
# because he is worn out, it is late, and you have gone. So it is State.SLEEP with a flag
# (_napping) rather than a new state - the same frames, every SLEEP rule (no petting, no quirks,
# nothing said) - but none of the sulk's edges: no clock, no hover-wake, no grumble, no turning
# away. It ends when you come back, and then he gets up and greets you as he would have anyway.

#: How long you have been away - on his own clock, from set_away(True) - before he may drop off.
#: Long enough that a trip to the kettle at midnight finds him still sitting up for you.
NAP_AFTER_AWAY_MS = 600000.0  # 10 min
#: And how worn out he has to be: the same edge as the tired yawn. Energy rests at +0.5 an hour
#: while you are away, so at the ten-minute mark this means under ~0.17 when you left.
NAP_ENERGY_BELOW = TIRED_ENERGY_BELOW
#: A nap this long or longer earns the whole of needs' "nap_woke" nudge when he wakes; a shorter
#: one a share of it, so dozing off for a minute is not worth two hours' sleep.
NAP_FULL_H = 2.0

# --- a treat ---------------------------------------------------------------------------------
#
# The second kindness, from the tray or the wheel. Only ever because you gave one: nothing here is
# scheduled, so no rate moves, and "eat" is deliberately in neither ANTICS nor ON_DEMAND - a
# biscuit he finds by himself, or one Do something hands out, would not be a treat from you.

EAT_ANIM = "eat"
EAT_MS = 1400.0
#: A sheet without the eat frames - an older custom skin - shows the heart eyes instead, which is
#: the same fallback the pet uses, and says the same thing with art every sheet has.
EAT_FALLBACK_ANIM = "heart_eyes"
#: Treats a day that count. Past this he is full: he sits down with a polite no-thank-you, and the
#: mood, needs and memory are left as they were - otherwise the menu would be a button for pumping
#: his spirits up. Counted per WorldState day, like every other count.
TREATS_PER_DAY = 3
TREAT_FULL_ANIM = "sit_dangle"
TREAT_FULL_MS = 1500.0

# --- dressing himself --------------------------------------------------------------------
#
# He owns fifty-odd costumes and, left alone, wears none of them. The fix is NOT to cycle them at
# random: the costume is a choice you make from the tray and it persists, so a timer overwriting it
# every few minutes would make that menu decorative - and a hat that changes for no reason says
# nothing about him or about what is happening. It would be the only arbitrary thing in here.
#
# So a costume goes on when there is a REASON for it, through the same path the reading glasses
# have always used. Two kinds, and the difference is how long they last:
#
# * A **moment costume** is worn for the length of one reaction - a hard hat while he fans himself
#   at a pinned machine, a party hat for the knocking-off dance. Keyed on the moment rather than on
#   the animation, so dancing because it is Friday afternoon does NOT get the hat; only actually
#   knocking off does.
# * A **seasonal costume** is worn continuously for as long as the season lasts. That one is meant
#   to be predictable rather than surprising: "it is December so he has a beanie on" is a feature
#   you can reason about.
#
# Both obey the rule the persona glasses established: if you have picked a costume yourself, yours
# wins and neither of these ever fires.

#: season -> what he wears all season. Deliberately a mix of hats and eyewear so the seasons do not
#: all read as "a different hat".
SEASON_COSTUMES: dict[str, str] = {
    SEASON_WINTER: "beanie",
    SEASON_SPRING: "flowercrown",
    SEASON_SUMMER: "shades",
    SEASON_AUTUMN: "leaf",
}

#: The seasonal moment for each season. Spring is deliberately absent - see above.
SEASON_ANTICS: dict[str, str] = {
    SEASON_WINTER: "shiver",
    SEASON_SUMMER: CPU_ANIM,
    SEASON_AUTUMN: "autumn",
}

#: States whose animation the mood is allowed to slow down.
_MOOD_PACED_STATES = (State.IDLE, State.ANTIC)

# --- playing with what is around him ---------------------------------------------------
#
# Both of these are strictly READ-ONLY with respect to other applications. He looks at where a
# window is and he reacts to it; he never moves one. Nothing here can displace anybody's layout.

#: Peeking: he sinks below the top edge of the taskbar until only his head and eyes show over it,
#: looks about, and rises again. Implemented by shortening HIS OWN window and letting the sprite be
#: clipped, never by fighting the shell for z-order - two topmost windows racing each other is not
#: a fight worth picking, and losing it would leave him stuck behind the bar.
#: Logical px, scaled with the sprite - 46% of his visible height, whatever the scale. Chosen by
#: rendering the sink from 0 to 36px over a real taskbar and looking: below this he is merely
#: standing low, and two pixels further down the taskbar starts eating his eyes.
PEEK_SINK_PX = 13
PEEK_DOWN_MS = 420.0
PEEK_HOLD_MS = 1900.0
PEEK_UP_MS = 460.0
PEEK_COOLDOWN_MS = 480000.0       # 8 min
PEEK_CHANCE = 0.5
#: eyes_track is a 3x3 grid of directions; index 1 is the top row, centre column - straight up.
PEEK_LOOK_FRAME = 1

#: Straining at the underside of a window hanging just above him. He never shifts it, and that is
#: the whole joke: the gag costs nothing and cannot break anyone's window arrangement.
LEDGE_ANIM = "strain"
#: The same animation, reused for a different reason: braced legs, arm rigid, teeth gritted reads
#: as "gripping something hard" just as well as it reads as "pushing up at a window" - and it is
#: held for as long as the sign is up, rather than for one antic's usual few seconds.
POCKET_HOLD_ANIM = "strain"
#: A focus session holds him in the same three frames State.WORKING already plays for its 1.5-2.5s
#: post-prompt beat (WORKING_MIN_MS/MAX_MS below) - just held for the length of the session via the
#: same 1e9ms trick as POCKET_HOLD_ANIM/AWAY_ANTIC, instead of a fixed few seconds. A real prompt
#: sent mid-session plays the identical sprite through the real State.WORKING path and hands back
#: to this hold with no visible seam, which is the whole reason this costs no new art.
FOCUS_HOLD_ANIM = "working"
#: The headwear he wears for the whole session, regardless of whatever costume you had picked -
#: headphones read as "do not disturb" at a glance, the same reason they mean that on an actual
#: desk. Applied the same way TASK_FINISHED_ANIM borrows "party": a costume= kwarg on the antic,
#: not a change to the persistent `costume` setting, so it is gone the moment the session ends.
FOCUS_COSTUME = "headphones"
#: Every so often the working pose gives way to a burst of press-ups instead - existing art, no new
#: frames - so a 25-minute hold reads as sustained effort rather than one frame left running. Timed
#: from _tick_focus rather than folded into the reassertion in _choose_next_action, so knocking him
#: into something else and snapping back (the pet-interruption case) never itself counts as a beat.
FOCUS_BURST_ANIM = "pushups"
FOCUS_BURST_MS = 2400.0
FOCUS_BURST_INTERVAL_MIN_MS = 45000.0
FOCUS_BURST_INTERVAL_MAX_MS = 90000.0
LEDGE_COOLDOWN_MS = 900000.0      # 15 min after he has actually had a go at something
#: ...and after a look that turned up nothing, which on an ordinary desktop is most looks. This
#: has to exist: charging the cooldown only on success left ``wants_ledge`` permanently true, and
#: the window enumeration - the most expensive probe in the app - then ran on ~89% of the 700ms
#: geometry polls instead of the few times an hour it is budgeted for.
LEDGE_LOOK_AGAIN_MS = 90000.0     # 90s
LEDGE_MIN_MS, LEDGE_MAX_MS = 3200.0, 4600.0
#: How far above his head a window's bottom edge may be and still be worth reaching for, as a
#: fraction of his height. Measured on a real desktop: of 8 visible windows, the two within reach
#: hung 48px and 74px above his head, so most of a body height is the useful band.
LEDGE_REACH_FRACTION = 0.9

# --- the contact shadow ------------------------------------------------------------------
#
# Without one he does not read as standing on the taskbar so much as hovering a pixel above it.
# It is painted by Qt rather than baked into the sheet, and that is forced: a shadow has to be
# partially transparent to work over a taskbar whose colour we do not know, and the art contract
# bans partial alpha precisely because the per-pixel click mask is derived from it.
#
# Which means the shadow's own area has to be added to that mask by hand, and THAT is the thing to
# be careful about - it is the same failure the peek fix exists to prevent, where input region
# outside the sprite swallowed clicks meant for the taskbar underneath. So it is kept to a thin
# band at his feet, inside his own silhouette's width, and the growth is measured rather than
# assumed. See the shadow section of the README for the measured figure.

SHADOW_H_PX = 3                  # logical px tall, before the sprite scale
#: The shadow's width is rounded down to a multiple of this, so a landing does not force a new
#: click mask on every tick as it spreads. See the note in ``Behavior.shadow``.
SHADOW_WIDTH_QUANTUM_PX = 4
#: How much wider than his actual stance. Slightly wider reads as contact; much wider reads as a
#: puddle he is standing in.
SHADOW_WIDTH_FACTOR = 1.5
SHADOW_ALPHA = 104               # of 255, at full strength on the ground
#: Beyond this much daylight under his feet there is no shadow at all, as a fraction of his
#: height. Read from the frame's own pixels, so the hop fades it out without being told to.
SHADOW_FADE_LIFT_FRACTION = 0.45
#: A landing spreads and lightens it briefly - the one bit of squash-and-stretch he has, and it
#: costs nothing because the shadow is drawn rather than drawn *by hand*.
LAND_SQUASH_MS = 260.0
LAND_SQUASH_SPREAD = 0.55

# --- things that happen TO him -----------------------------------------------------------
#
# Everything else in this file is either a reaction to you or an activity he chose. These three are
# involuntary, and that is precisely why they sell him as alive - nobody decides to sneeze.
#
# Rates are SCHEDULED rather than rolled at each decision point. A per-roll chance would make the
# real frequency depend on how often he happens to reach a decision, which changes with the
# personality preset, the antic chance and how much he is walking; drawing the next occurrence
# from a target interval means "twice an hour" is twice an hour whatever else is going on.

SNEEZES_PER_HOUR = 2.0
TRIPS_PER_HOUR = 3.0
#: Jitter around the mean interval, so they are not metronomic.
QUIRK_SPREAD = (0.55, 1.45)

SNEEZE_ANIM = "sneeze"
SNEEZE_MS = 950.0
#: The recoil throws him backwards a little. Small - it should look like a sneeze, not a shove.
SNEEZE_RECOIL_PX = 4

TRIP_ANIM = "trip"
TRIP_MS = 470.0

#: Hiccups come in threes, and they arrive after you have thrown him about.
HICCUP_ANIM = "hiccup"
HICCUP_MS = 560.0
HICCUP_MIN, HICCUP_MAX = 3, 5
#: How hard he has to land to get them.
HICCUP_IMPACT_PX_MS = 1.1

#: A single poke used to be completely invisible: nothing at all happened until the fourth one, at
#: which point he stormed off. A brief glare makes the escalation legible instead of sudden.
POKE_FLINCH_ANIM = "angry"
POKE_FLINCH_MS = 420.0

#: Scrolling the wheel over him spins him. No reason whatsoever; it is just funny.
SPIN_MS = 620.0
SPIN_COOLDOWN_MS = 900.0

#: What he thinks of the costume you just put on him. Keyed on the costume, falling back to
#: COSTUME_CATEGORY_OPINIONS, then to COSTUME_OPINION_DEFAULT, and it costs no new art at all -
#: every one of these animations already exists.
#: He owns fifty-odd costumes and, until this, had no opinion about any of them.
COSTUME_OPINIONS: dict[str, str] = {
    # ...that is ridiculous and you know it
    "cone": "startle", "sombrero": "startle", "propeller": "startle", "party": "startle",
    "groucho": "startle", "vrheadset": "startle",
    # ...rather regal, actually
    "crown": "wink", "tophat": "wink", "monocle": "wink", "mortarboard": "wink",
    "tricorn": "wink", "viking": "wink", "cape": "wink",
    # ...oh, I like this one
    "catears": "heart_eyes", "bunnyears": "heart_eyes", "flowercrown": "heart_eyes",
    "heartglasses": "heart_eyes", "halo": "heart_eyes", "scarf": "heart_eyes",
    "beanie": "heart_eyes", "wings": "heart_eyes",
}
#: The fallback the comment above always promised: anything not named gets its category's
#: reaction rather than the one default. 32 of 53 costumes used to share look_around; with this,
#: no reaction is shared by more than 10. "hop" is deliberately absent - it is the quick-change.
COSTUME_CATEGORY_OPINIONS: dict[str, str] = {
    "Hats": "scratch",        # ...itches under the brim
    "Head": "sparkle",
    "Eyes": "look_around",    # trying them out
    "Neck": "wink",           # dapper
    "Body": "pushups",        # work gear: sleeves up
    "Behind": "stretch",      # shrugging the straps on
}
#: Anything in neither table - or nothing at all: he has a look at himself.
COSTUME_OPINION_DEFAULT = "look_around"
#: And taking one off gets the same look, not a celebration.
COSTUME_OPINION_MS = (900.0, 1500.0)
#: The quick-change: he crouches, hops, and the outfit swaps at the very top of the hop - where the
#: eye is on the jump, not the hat - then lands and gives his opinion. The greeting hop; no new art.
QUICK_CHANGE_ANIM = "hop"
#: hop = crouch, rising, apex, falling, landing; foot_print lifts 0/8/24/10/0px at scale 2.
QUICK_CHANGE_SWAP_FRAME = 2
#: One 700ms cycle is ~1300ms at the slowest mood pace (~0.54). Twice that, so this only cuts in
#: when something froze the animation.
QUICK_CHANGE_MAX_MS = 2600.0
#: A new colour gets a sparkle, for the same COSTUME_OPINION_MS: he has no favourite colour, he is
#: just pleased somebody bothered.
SKIN_REACTION_ANIM = "sparkle"

#: Pinning with nothing on the clipboard: he has a look round for it. The toast says what happened.
POCKET_EMPTY_ANIM = "look_around"
POCKET_EMPTY_MS = (1200.0, 1800.0)

#: The app did not open. A double-take instead of "on it" - he is as surprised as you are.
LAUNCH_FAILED_ANIM = "startle"
LAUNCH_FAILED_MS = STARTLE_MS

#: Putting the wheel away without choosing: a head scratch, the nearest thing he has to a shrug.
#: Not a poke - browsing the wheel is not brushing him off (see wheel_closed). One cycle.
WHEEL_CANCEL_ANIM = "scratch"
WHEEL_CANCEL_MS = 600.0

#: A beat for each wheel tool whose handler gives him nothing to do, in whole cycles. The rest are
#: left out on purpose: ask, again, read, focus, pocket, do_something, treat and costume already set
#: his state (focus's *stop* does not, but a session you just ended earns nothing - see set_focus;
#: nor does a treat he refuses, and a beat would read as him taking it after all);
#: hide and guard take him off screen; more opens a menu whose own items have handlers. Mic is
#: foot_tap and never wave: wave in earmuffs is the muted-mic tell, and this is not that. Settings
#: is typing rather than a scratch, because the scratch is what a cancel looks like.
TOOL_BEATS: dict[str, tuple[str, float]] = {
    "measure": ("look_around", 1880.0),
    "mic": ("foot_tap", 1240.0),
    "wander": ("hop", 700.0),
    "pause": ("stretch", 1520.0),   # only ever reached on resume - see tool_picked
    "ghost": ("wink", 980.0),
    "mode": ("sparkle", 1360.0),
    "settings": ("typing", 1140.0),
    "tips": ("wave", 1120.0),
    "reset": ("look_around", 1880.0),
}

#: Target update interval while he is actually moving. The gait is tick-rate independent (each
#: animation frame advances him exactly one stride regardless), so this trades only motion
#: smoothness, and 20fps is finer than the sprite's own pixel grid at normal scale.
MOVE_TICK_MS = 50.0

#: Fraction of a walk spent accelerating (and the same again decelerating).
EASE_FRACTION = 0.22
#: Slowest the gait ever runs, as a fraction of full speed. Never 0, or he would stall mid-stride.
MIN_SPEED_MULTIPLIER = 0.32


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class Behavior:
    """Owns where the mascot is, what he is doing, and how annoyed he is about it."""

    def __init__(
        self,
        config,
        sheet: SpriteSheet,
        mood: Mood | None = None,
        memory: Memory | None = None,
        needs: Needs | None = None,
    ) -> None:
        self._config = config
        self._sheet = sheet
        self.animator = Animator(sheet, "idle")
        #: How the day has gone - the only part of him that survives a restart. Constructed here
        #: when the caller does not supply one, so a test can build a Behavior on its own.
        self.mood = mood if mood is not None else Mood(config)
        #: What he remembers about the two of you - counts only. The default keeps them in memory
        #: and never writes a file, so every test that builds a Behavior on its own stays
        #: off the disk; the app passes one that saves to memory.json.
        self.memory = memory if memory is not None else Memory(None, config)
        #: Energy, affection and boredom. The default reads the config but is never persisted -
        #: only the app calls persist() - so a test's Behavior cannot write anything back.
        self.needs = needs if needs is not None else Needs(config)

        self._state = State.IDLE
        self._prev_state = State.IDLE
        self._state_ms = 0.0

        self.baseline_y = 0
        self.x_min = 0
        self.x_max = 0
        self._x = 0.0
        self._y = 0.0  # only meaningful while dragging or falling
        self._facing = "right"

        self._idle_timer_ms = self._next_wander_delay()
        self._blink_timer_ms = random.uniform(BLINK_MIN_MS, BLINK_MAX_MS)
        self._blinking = False
        self._blink_state = BLINK_SINGLE
        self._startle_cooldown_ms = 0.0

        self._walk_from = 0.0
        self._walk_target = 0.0
        self._pending_sleep = False
        #: Whether the tool wheel is what put him in State.ASK - see wheel_opened.
        self._wheel_hold = False
        #: Where the wheel tool being aimed at is, in screen px: his eyes follow it (wheel_aimed).
        self._wheel_look: tuple[int, int] | None = None
        #: An antic to play once the current walk arrives - that is how he crosses the strip to
        #: get *under* something before reacting to it.
        self._pending_action: str | None = None
        self._peek_cooldown_ms = 0.0
        self._ledge: tuple[int, int, int] | None = None  # (bottom_y, left, right), screen px
        self._ledge_cooldown_ms = 0.0

        self._wave_cooldown_ms = 0.0
        self._eye_track_cooldown_ms = 0.0
        self._foreground_cooldown_ms = 0.0
        self._away = False
        #: A focus session, started from the tray. Not written to config - quitting mid-session
        #: drops it, honestly, the same as the snippet pocket does.
        self._focus_active = False
        self._focus_remaining_ms = 0.0
        self._focus_total_ms = 0.0
        self._focus_burst_due_ms = 0.0
        #: True for exactly one drain (see take_focus_finished), the tick a session completes on
        #: its own - main.py uses it to fire an OS notification, since he might be hidden behind a
        #: fullscreen app when it happens.
        self._focus_finished = False
        #: When you went away, on the behaviour clock - None while you are here. Only the nap reads
        #: it (NAP_AFTER_AWAY_MS); the greeting is sized by the app's own wall-clock gone_s.
        self._away_since_ms: float | None = None
        #: A nap rather than a sulk: State.SLEEP that ends when you come back. See the nap section
        #: above, _maybe_nap, and _enter for every way it ends.
        self._napping = False
        self._nap_since_ms = 0.0
        #: The greeting owed once he is up from a nap, as ``(gone_s,)`` - or None for none owed.
        #: Kept here rather than queued as antics: _enter empties _antic_queue on the way into
        #: WAKE, and computing it early would raise the "greeted" event while he is still asleep,
        #: where nothing may be said. _tick_wake plays it, through the same _greet as set_away.
        self._nap_greeting: tuple[float | None] | None = None
        #: Treats given today, as ``(date_key, count)`` - the cap's own count, kept whatever memory
        #: says. memory.json is the count that survives a restart, but with memory_enabled off it
        #: records nothing and reads 0, and the cap would never bite. This one forgets on restart.
        self._treats_today: tuple[str, int] = ("", 0)
        #: How long you have been present but not interacted with HIM. Distinct from the system
        #: idle clock, which only knows whether you are at the machine at all.
        self._neglect_ms = 0.0
        self._neglect_stage = 0
        self._neglect_glance_ms = 0.0
        self._world: WorldState | None = None
        self._reaction_cooldown_ms = 0.0
        self._hot_cooldown_ms = 0.0
        self._task_finished_cooldown_ms = 0.0
        self._morning_cooldown_ms = 0.0
        self._seasonal_cooldown_ms = 0.0
        self._needs_moment_cooldown_ms = 0.0
        #: Antics to queue after a moment that is a short sequence rather than one antic - the
        #: tired yawn's heavy blink. _pick_moment hands back one (name, ms, costume) like every
        #: moment, and _begin_antic clears the queue, so the rest waits here until it has started.
        self._moment_tail: list[tuple[str, float]] = []
        self._persona_costume = ""
        #: The costume a quick-change is hopping into, while the hop is in the air - see quick_change.
        self._quick_change: str | None = None
        self._quick_change_swapped = False
        #: Raised for exactly one drain (take_costume_swap) when the new costume should become visible.
        self._costume_swap_due = False
        #: The snippet billboard: a list of lines, never written to config or disk. Restarting
        #: the app - or just quitting - empties it, the same way a real one would.
        self._pocket: list[str] = []
        self._antic: str | None = None
        self._antic_ms = 0.0
        #: Set on the tick an antic's clock runs out: how much longer it may take to reach a clean
        #: frame (one cycle, in real ms at the current pace). None while the clock is still running.
        self._antic_overrun_ms: float | None = None
        self._antic_overrun_from = 0
        self._antic_hold = False
        #: Antics still to play, in order, after this one - see _begin_sequence.
        self._antic_queue: list[tuple[str, float]] = []
        self._last_antic: str | None = None
        self._working_ms = 0.0
        self._sleep_ms = 0.0
        self._grumble_ms = 0.0
        self._vx = 0.0
        self._vy = 0.0
        self._falling = False
        self._rolling = False
        self._pending_injury = False
        self._fall_from_y = 0.0
        #: What the landing in progress means, judged at the release - by the time he stops, a
        #: bounce has replaced both the speed and the height. Set by drag_end, read and cleared by
        #: _settle, and dropped by anything that cuts the fall short (a catch, a perch, a hide).
        #: See GENTLE_ANIM and PLAY_ANIM.
        self._gentle_pending = False
        self._throw_pending = False
        #: When he last sparkled at being set down gently, on the behaviour clock. A timestamp
        #: rather than a countdown, the same pattern as _back_at_ms: _settle runs on the near side
        #: of the pause check, and the cooldown loop does not.
        self._gentle_at_ms = -1e9
        #: (dt, dx, dy) over the tail of the current drag - see THROW_SAMPLE_MS.
        self._drag_samples: deque[tuple[float, float, float]] = deque()
        self._drag_last: tuple[float, float] | None = None
        self._injured_ms = 0.0
        #: Counts down after an impact and spreads the contact shadow while it runs.
        self._land_squash_ms = 0.0
        self._struggle_ms = 0.0
        self._struggle_dx = 0.0
        self._struggle_dy = 0.0
        #: The window he is standing on, as (left, top, right, bottom) - or None. Purely cosmetic:
        #: he reads its rectangle and follows it, the same as the overhead-window ledge does, and
        #: nothing here ever moves, resizes or restacks it. Fed in from main.py every tick while
        #: perched, the same way set_ledge/set_world feed in things only Win32 can see.
        self._perch_rect: tuple[int, int, int, int] | None = None
        #: Where along the window's top edge he stands, as a 0..1 fraction of its width - set once
        #: on landing and then held, so a resize or a move carries him along proportionally rather
        #: than pinning him to a screen coordinate the window has since moved out from under.
        self._perch_frac = 0.5
        self._resume_sleep_ms = 0.0

        #: Being petted. ``_pet_armed`` is what stops it looping: after a pet the pointer has to
        #: actually LEAVE him before another can start, so a cursor parked on the taskbar does not
        #: put him in a permanent trance.
        self._cursor_anchor: tuple[int, int] | None = None
        self._cursor_still_ms = 0.0
        self._pet_armed = True
        self._cursor_was_over = False
        self._pet_cooldown_ms = 0.0
        self._pet_drain_ms = 0.0
        #: What to play once the working animation finishes - see PROMPT_REACTIONS.
        self._post_prompt: tuple[str, float, str] | None = None
        #: The involuntary lot. Scheduled rather than rolled - see the note by SNEEZES_PER_HOUR.
        self._sneeze_due_ms = self._next_quirk(SNEEZES_PER_HOUR)
        self._trip_due_ms = self._next_quirk(TRIPS_PER_HOUR)
        self._trip_armed = False
        self._hiccups_left = 0
        self._spin_cooldown_ms = 0.0
        #: When he was last hidden, and when he last waved on the way back, both on the behaviour
        #: clock. Timestamps rather than a countdown because the cooldown loop does not run while
        #: hidden, and hidden is exactly where this one has to keep counting. See BACK_ANTIC.
        self._hidden_since_ms = 0.0
        self._back_at_ms = -1e9
        #: A point he has been asked to look at, overriding the cursor for one eye-track antic.
        self._forced_look: tuple[int, int] | None = None

        #: Sound events raised since the app last drained them. A queue rather than a callback so
        #: this module stays free of Qt and of the audio layer entirely: it says what happened,
        #: and something else decides whether that makes a noise.
        self._sounds: list[str] = []
        #: Things worth a line, raised since the app last drained them - a greeting, a milestone.
        #: The same queue-not-callback shape as the sounds, and for the same reason: this module
        #: says what happened, and voice.py (with the chatter setting) decides whether he says
        #: anything about it. Each entry is ``(kind, data)``.
        self._events: list[tuple[str, dict]] = []

        self._pokes: deque[float] = deque()
        self._residual = 0.0
        self._clock_ms = 0.0
        self._last_click_ms = -1e9

        self.paused = False

    # --- geometry -----------------------------------------------------------------

    @property
    def sprite_w(self) -> int:
        return self._sheet.sprite_w

    @property
    def sprite_h(self) -> int:
        return self._sheet.sprite_h

    @property
    def sheet(self) -> SpriteSheet:
        return self._sheet

    def set_sheet(self, sheet: SpriteSheet) -> None:
        """Swap in a sheet at a different scale, e.g. he moved to a monitor with another DPI.

        The current animation is carried over so a rescale mid-stride does not restart the walk.
        """
        state, index = self.animator.state, self.animator.frame_index
        self._sheet = sheet
        keep = sheet.has_state(state)
        self.animator = Animator(sheet, state if keep else "idle")
        if keep:
            self.animator.set_state(state, restart=True)
            self.animator._index = index % sheet.frame_count(state)

    @property
    def x(self) -> int:
        # The struggle offset is applied at render time only, never folded into _x, so it cannot
        # drift or feed back into the cursor-driven drag anchor.
        return int(round(self._x + self._struggle_dx))

    @property
    def state(self) -> State:
        return self._state

    @property
    def facing(self) -> str:
        return self._facing

    def rest_top(self) -> int:
        """Window top that puts the soles exactly on the walk strip."""
        return self.baseline_y - (self._sheet.frame_h - self._sheet.baseline_offset) * self._sheet.scale

    @property
    def peek_sink(self) -> int:
        """How far below his resting height he currently is, in screen px. 0 unless peeking."""
        if self._state != State.PEEK:
            return 0
        depth = PEEK_SINK_PX * self._sheet.scale
        elapsed = self._state_ms
        if elapsed < PEEK_DOWN_MS:
            return int(round(depth * _smoothstep(elapsed / PEEK_DOWN_MS)))
        if elapsed < PEEK_DOWN_MS + PEEK_HOLD_MS:
            return depth
        rising = (elapsed - PEEK_DOWN_MS - PEEK_HOLD_MS) / PEEK_UP_MS
        return int(round(depth * (1.0 - _smoothstep(rising))))

    @property
    def top(self) -> int:
        if self._state == State.DRAG:
            return int(round(self._y + self._struggle_dy))
        if self._falling:
            return int(round(self._y))
        if self._perch_rect is not None:
            # Keyed on the rectangle rather than on State.PERCH specifically: a sneeze, a wave, or
            # clicking him to ask something all route through _begin_antic/_enter and leave PERCH
            # for as long as they run, but none of them are a reason to snap him back down to the
            # taskbar mid-antic - only _step_down_from_perch clearing the rectangle is.
            return int(round(self._y))
        return self.rest_top() + self.peek_sink

    @property
    def visible_height(self) -> int:
        """How tall his window may be. Shorter than the sprite while he is peeking.

        Sinking him and then *shortening the window* is what hides his lower half: whatever falls
        below the taskbar's top edge is simply never drawn. No z-order games, no clipping region to
        keep in sync, and no way for him to end up trapped behind the bar.
        """
        if self._state != State.PEEK:
            return self.sprite_h
        return max(1, min(self.sprite_h, self.baseline_y - self.top))

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """(x, y, w, h) of his window on screen - h shrinks while he peeks."""
        return (self.x, self.top, self.sprite_w, self.visible_height)

    @property
    def shadow(self) -> tuple[int, int, int, int, int] | None:
        """``(left, top, width, height, alpha)`` in WINDOW-LOCAL pixels, or None for no shadow.

        None in every case where a shadow drawn inside his own window would be a lie: while he is
        in the air or in your hand it cannot stay on the ground where it belongs, and while he is
        peeking the window is cropped to the top of the taskbar so those rows are not drawn at all.
        """
        if not bool(self._config.get("shadow_enabled", True)):
            return None
        if self._state in (State.HIDDEN, State.DRAG, State.PEEK) or self._falling:
            return None

        left, right, lift = self._sheet.foot_print(
            self.animator.state, self.animator.frame_index
        )
        if right <= left:
            return None
        fade = 1.0 - lift / max(1.0, self.sprite_h * SHADOW_FADE_LIFT_FRACTION)
        if fade <= 0.0:
            return None

        squash = self._land_squash_ms / LAND_SQUASH_MS if LAND_SQUASH_MS else 0.0
        spread = 1.0 + LAND_SQUASH_SPREAD * squash
        scale = self._sheet.scale
        width = int(round((right - left + 1) * SHADOW_WIDTH_FACTOR * spread))
        # Quantised, because the window rebuilds its click mask - a SetWindowRgn call - whenever
        # this rectangle changes. Left continuous, a 260ms landing widened the shadow a pixel at a
        # time and forced a rebuild on every single tick: measured at 29 mask rebuilds for one
        # landing against 13 with the shadow switched off. On the quantum it is a handful, and
        # four logical pixels is well below what reads as a step in a soft-edged ellipse.
        quantum = SHADOW_WIDTH_QUANTUM_PX * scale
        width = max(quantum, (width // quantum) * quantum)
        height = max(1, SHADOW_H_PX * scale)
        centre = (left + right) // 2
        # Sits on the sole line, straddling it, so the darkest part is exactly where he meets the
        # ground rather than a band floating just below his feet.
        top = self.sprite_h - self._sheet.sole_gap - height // 2
        # Spreading on impact also thins it out; a landing shadow that got darker as it widened
        # would read as him sinking into the taskbar.
        alpha = int(round(SHADOW_ALPHA * fade / spread))
        if alpha <= 0:
            return None
        return (centre - width // 2, top, width, height, alpha)

    def set_strip(self, baseline_y: int, x_min: int, x_max: int) -> None:
        """Adopt a new walk strip (resolution, DPI, monitor, or taskbar change)."""
        self.baseline_y = int(baseline_y)
        self.x_min = int(x_min)
        self.x_max = max(int(x_min), int(x_max) - self.sprite_w)
        self._x = min(max(self._x, self.x_min), self.x_max)
        self._walk_target = min(max(self._walk_target, self.x_min), self.x_max)

    def place_at(self, x: float | None) -> None:
        """Position him, or send him to his configured home when ``x`` is None."""
        if x is None:
            fraction = min(1.0, max(0.0, float(self._config.get("home_fraction", 0.78))))
            self._x = self.x_min + (self.x_max - self.x_min) * fraction
        else:
            self._x = min(max(float(x), self.x_min), self.x_max)

    # --- things that happen to him ---------------------------------------------------

    def _quirks_on(self) -> bool:
        return bool(self._config.get("quirks_enabled", True)) and not self._away

    def _next_quirk(self, per_hour: float) -> float:
        """Milliseconds until the next occurrence of something that happens this often."""
        if per_hour <= 0:
            return float("inf")
        return random.uniform(*QUIRK_SPREAD) * (3_600_000.0 / per_hour)

    def _tick_quirks(self, dt_ms: float) -> None:
        """Count down to the next sneeze and the next stumble.

        Deliberately outside ``_neglect_suppressed``'s list of exemptions: a sneeze is involuntary,
        so being ignored or being in a good mood has nothing to do with whether one arrives. It is
        held off only while he is asleep, being carried, or off screen - places where playing it
        would look like a glitch rather than a creature. PERCH is held off for a sharper reason
        than the others: a quirk fires through _begin_antic, which knows how to get back to every
        *other* held state (away, the pocket, focus) via _choose_next_action's reassertion, but the
        perch position is not decided there at all - it comes back only through update_perch. A
        sneeze mid-perch would leave him standing back on the taskbar the moment it ended, with no
        code path that ever puts him back on the window.
        """
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN, State.PERCH):
            return
        if self._falling or self._rolling or not self._quirks_on():
            return
        self._sneeze_due_ms -= dt_ms
        self._trip_due_ms -= dt_ms
        if self._trip_due_ms <= 0:
            # Armed rather than fired: a stumble only means anything while he is walking, so it
            # waits for the next walk instead of happening on the spot.
            self._trip_due_ms = self._next_quirk(TRIPS_PER_HOUR)
            self._trip_armed = True

    def _maybe_sneeze(self) -> bool:
        if self._sneeze_due_ms > 0 or not self._quirks_on():
            return False
        if not self._sheet.has_state(SNEEZE_ANIM):
            return False
        self._sneeze_due_ms = self._next_quirk(SNEEZES_PER_HOUR)
        # The recoil throws him the way he is NOT facing, which is what a sneeze does to you.
        recoil = SNEEZE_RECOIL_PX * self._sheet.scale
        self._x = min(max(self._x + (-recoil if self._facing == "right" else recoil),
                          self.x_min), self.x_max)
        log.debug("sneeze")
        self._sound("sneeze")
        self._begin_antic(SNEEZE_ANIM, SNEEZE_MS)
        return True

    def _maybe_trip(self) -> bool:
        """Stumble mid-stride. Called from the walk, which is the only place it makes sense."""
        if not self._trip_armed or not self._quirks_on():
            return False
        if not self._sheet.has_state(TRIP_ANIM):
            self._trip_armed = False
            return False
        self._trip_armed = False
        log.debug("tripped over nothing")
        self._sound("trip")
        self._begin_antic(TRIP_ANIM, TRIP_MS)
        return True

    def _maybe_hiccup(self) -> bool:
        if self._hiccups_left <= 0 or not self._sheet.has_state(HICCUP_ANIM):
            self._hiccups_left = 0
            return False
        self._hiccups_left -= 1
        self._sound("hiccup")
        self._begin_antic(HICCUP_ANIM, HICCUP_MS)
        return True

    def start_hiccups(self) -> None:
        """Give him the hiccups. Earned by landing hard."""
        if not self._quirks_on() or not self._sheet.has_state(HICCUP_ANIM):
            return
        self._hiccups_left = random.randint(HICCUP_MIN, HICCUP_MAX)
        log.info("got the hiccups")

    def spin(self) -> bool:
        """Somebody scrolled the wheel over him. Returns True if he actually span."""
        if not bool(self._config.get("scroll_to_spin", True)):
            return False
        if self._spin_cooldown_ms > 0 or not self._sheet.has_state(TUMBLE_ANIM):
            return False
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN, State.ANGRY):
            return False
        if self._falling or self._rolling:
            return False
        self._spin_cooldown_ms = SPIN_COOLDOWN_MS
        self.noticed()
        self._sound("spin")
        self._begin_antic(TUMBLE_ANIM, SPIN_MS)
        return True

    #: What "Do something" can pick from, and how often. The involuntary lot are weighted heavily
    #: because they are the whole reason the button exists: on their own schedule you wait twenty
    #: to thirty minutes to see one, and the trip additionally waits for him to be mid-walk.
    #: The ambient pool is in there too, at a lower weight, so the button doubles as a way to
    #: browse what he can actually do rather than only as a test harness for the rare three.
    ON_DEMAND: tuple[tuple[str, float, float, float], ...] = (
        (SNEEZE_ANIM, 3.0, SNEEZE_MS, SNEEZE_MS),
        (TRIP_ANIM, 3.0, TRIP_MS, TRIP_MS),
        (HICCUP_ANIM, 2.0, HICCUP_MS * 3, HICCUP_MS * 4),
        (TUMBLE_ANIM, 2.0, SPIN_MS, SPIN_MS),
    )

    def do_something(self) -> str:
        """Play one of his rarer animations now, chosen at random. Returns what he did.

        Deliberately NOT "make him sneeze": a control for one specific reflex would turn the three
        involuntary things into puppet strings, which is the one property that makes them work.
        Asking him to do *something* keeps the character and still lets you see them on demand.
        """
        if self._state in (State.DRAG, State.HIDDEN):
            return ""
        self.noticed()
        pool = [entry for entry in self.ON_DEMAND if self._sheet.has_state(entry[0])]
        # The ambient antics, at a lower weight - see the note on ON_DEMAND.
        pool += [
            (name, weight * 0.35, low, high)
            for name, weight, low, high, _cat in ANTICS
            if self._sheet.has_state(name)
        ]
        if not pool:
            return ""
        name, _weight, low, high = random.choices(pool, weights=[e[1] for e in pool], k=1)[0]
        # The same noise it would have made had it happened on its own - otherwise the button
        # produces a silent sneeze, which is the one thing a sneeze cannot be.
        self._sound({SNEEZE_ANIM: "sneeze", TRIP_ANIM: "trip",
                     HICCUP_ANIM: "hiccup", TUMBLE_ANIM: "spin"}.get(name, ""))
        # Straight into the animation rather than through the schedules: this is you asking, so it
        # must not consume the next natural sneeze or leave a trip armed behind it.
        self._begin_antic(name, random.uniform(low, high))
        log.info("on demand: %s", name)
        return name

    def treat(self) -> bool:
        """You gave him a treat, from the tray or the wheel. Returns whether he took it.

        "Took it" includes being too full for it: past ``TREATS_PER_DAY`` he sits down with a polite
        no-thank-you (``treat_full``), with no "treated" nudge to mood or needs and nothing counted
        - offering it is still noticing him, which knocks boredom down as anything you do does.
        Otherwise it is counted, nudges the mood and needs (both "treated"), makes a small noise,
        raises a ``treat`` event, and he eats it.

        Refused, returning False, wherever a biscuit cannot be handed over: in your hand, hidden,
        mid-air or rolling, and paused - update() would freeze him on the first frame, and the count
        would go up for a treat he never ate. Refused while he is cross, too, exactly as click() is:
        asleep after a sulk he grumbles and stays put, and the tantrum and the storm-off before it
        are refused the same way, or a biscuit would be the way round every sulk. A nap is not a
        sulk: he is woken with no grumble (``_wake_from_nap``) and eats it straight away, so the
        biscuit replaces the WAKE beat. The greeting that nap owed goes with it, which loses
        nothing - napping means you still read as away, so _tick_wake would drop it too, and
        set_away greets you the ordinary way when you are back. Mid-way up from a nap he is left
        to finish getting up, as with a second click. Landed on his head, he takes it - the eat cuts
        the daze short, as Do something and a costume's opinion already do.
        """
        if self._state in (State.DRAG, State.HIDDEN) or self._falling or self._rolling:
            return False
        if self.paused:
            return False
        self.noticed()
        if self.napping:
            self._wake_from_nap()
        elif self._waking_from_nap:
            return False
        elif self._state in (State.SLEEP, State.WAKE):
            # As at a click: a grumble, and he is not getting up for you. The click time also keeps
            # _tick_sleep's near-the-end hover wake off him for a moment, as wheel_opened does.
            self._last_click_ms = self._clock_ms
            self._grumble_ms = GRUMBLE_MS
            return False
        if self._state == State.ANGRY or self._pending_sleep:
            return False
        key = self._date_key()
        given = self._treats_today[1] if self._treats_today[0] == key else 0
        # Whichever count is higher: memory's survives a restart, this one counts with memory off.
        if max(self.memory.day(key).treats, given) >= TREATS_PER_DAY:
            log.info("offered a treat, but he has had %d today", TREATS_PER_DAY)
            self._event("treat_full")
            self._begin_sequence([(TREAT_FULL_ANIM, TREAT_FULL_MS)])
            return True
        self._treats_today = (key, given + 1)
        self.memory.record("treats", key)
        self.mood.nudge("treated")
        self.needs.nudge("treated")
        self._sound("nom")
        self._event("treat")
        anim = EAT_ANIM if self._sheet.has_state(EAT_ANIM) else EAT_FALLBACK_ANIM
        log.info("a treat (%d today)", max(self.memory.day(key).treats, given + 1))
        self._begin_antic(anim, EAT_MS)
        # From the first frame, as in quick_change: ANTIC -> ANTIC does not restart the animation,
        # so a second treat from the tray while he is still eating the first would carry on from
        # wherever that one had got to - often the last frame, with no biscuit left in it.
        self.animator.set_state(anim, restart=True)
        return True

    def costume_chosen(self, costume: str) -> bool:
        """You just put something on him. He has an opinion about it; returns whether he showed it.

        Costs no new art: every reaction here is an animation he already had. What it buys is that
        the wardrobe stops being a wardrobe - fifty-three costumes he was previously completely
        indifferent to now get a reaction each. Paused, he keeps it to himself: update() would
        freeze the reaction on its first frame until you resumed him.
        """
        if not bool(self._config.get("costume_reactions", True)) or self.paused:
            return False
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN):
            return False
        animation = self._costume_opinion(costume)
        if animation is None:
            return False
        self.noticed()
        log.debug("opinion on %r: %s", costume or "nothing", animation)
        self._begin_antic(animation, random.uniform(*COSTUME_OPINION_MS))
        return True

    def quick_change(self, costume: str) -> bool:
        """Crouch, hop, and change at the top of it. Returns whether he is doing it.

        False means "put it on now": he is asleep, held, hidden, cross, paused, mid-flight or on a
        window (an antic from PERCH has no way back onto it - see _tick_quirks), or reactions are
        off. The caller has already written the config; only the visible change waits for the apex,
        and take_costume_swap says when that is.
        """
        if not bool(self._config.get("costume_reactions", True)) or self.paused:
            return False
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN, State.ANGRY,
                           State.PERCH) or self._pending_sleep:
            return False
        if self._falling or self._rolling or not self._sheet.has_state(QUICK_CHANGE_ANIM):
            return False
        self.noticed()
        # A hop this one replaces has nothing left to show: the config already holds the newer
        # costume, so it must not flush as it is replaced.
        self._quick_change = None
        self._begin_antic(QUICK_CHANGE_ANIM, QUICK_CHANGE_MAX_MS)
        # Always from the crouch. ANTIC -> ANTIC does not restart the animation, so a greeting hop
        # already in the air would otherwise reach its apex - and swap - at once.
        self.animator.set_state(QUICK_CHANGE_ANIM, restart=True)
        self._quick_change = costume
        self._quick_change_swapped = False
        log.debug("quick-change into %r", costume or "nothing")
        return True

    def take_costume_swap(self) -> bool:
        """True exactly once when the quick-change wants the new costume shown. Drained like
        take_sounds."""
        due, self._costume_swap_due = self._costume_swap_due, False
        return due

    def _flush_quick_change(self) -> None:
        """Knocked out of a quick-change mid-hop: the costume is already yours, so show it now."""
        if self._quick_change is None:
            return
        if not self._quick_change_swapped:
            self._costume_swap_due = True
        self._quick_change = None

    def _costume_opinion(self, costume: str) -> str | None:
        """The named reaction, else the category's, else the default - the first this sheet has."""
        category = self._sheet.costume_category(costume) if costume else ""
        for animation in (COSTUME_OPINIONS.get(costume), COSTUME_CATEGORY_OPINIONS.get(category),
                          COSTUME_OPINION_DEFAULT):
            if animation and self._sheet.has_state(animation):
                return animation
        return None

    def skin_chosen(self) -> None:
        """You just changed his colour. A sparkle - same switch and same gates as a costume."""
        if not bool(self._config.get("costume_reactions", True)) or self.paused:
            return
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN):
            return
        if not self._sheet.has_state(SKIN_REACTION_ANIM):
            return
        self.noticed()
        self._begin_antic(SKIN_REACTION_ANIM, random.uniform(*COSTUME_OPINION_MS))

    def look_at(self, x: int, y: int) -> None:
        """Glance at a point on screen - where you just dropped something, for instance."""
        if not self._sheet.has_state(EYE_TRACK_STATE):
            return
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN):
            return
        self._forced_look = (int(x), int(y))
        self._begin_antic(EYE_TRACK_STATE, 650.0)

    # --- sound ----------------------------------------------------------------------

    def _sound(self, event: str) -> None:
        # Bounded: if nothing is draining the queue - sound switched off, or a headless test -
        # it must not grow for the lifetime of the process.
        if len(self._sounds) < 8:
            self._sounds.append(event)

    def take_sounds(self) -> list[str]:
        """Hand over everything that has happened since the last call, and forget it."""
        if not self._sounds:
            return []
        events, self._sounds = self._sounds, []
        return events

    # --- events worth a line ----------------------------------------------------------

    def _event(self, kind: str, **data) -> None:
        """Note that something happened that he might say a line about.

        Bounded like the sound queue, but it drops the *oldest* rather than the newest when full:
        a greeting from an hour ago is the stalest thing in it, and if anything is to be said it
        should be about now.
        """
        self._events.append((kind, dict(data)))
        if len(self._events) > MAX_PENDING_EVENTS:
            del self._events[:-MAX_PENDING_EVENTS]

    def take_events(self) -> list[tuple[str, dict]]:
        """Hand over every event since the last call, oldest first, and forget them."""
        if not self._events:
            return []
        events, self._events = self._events, []
        return events

    def memory_forgotten(self) -> None:
        """Forget everything was pressed: the celebrations he has had go with the memory.

        The two latches live in the config, not in memory.json, so ``Memory.forget`` cannot reach
        them. Left alone, the 7th day after a wipe would get no party whenever 7 was the last count
        celebrated - the latch is the count, and the count starts again. The focus streak's goes
        too, so three sessions after a mid-day wipe are a streak again, just as today's count says.
        So does the treat cap's own count, for the same reason: today's treats read 0 again.
        """
        self._config["last_milestone_days"] = ""
        self._config["last_focus_streak_date"] = ""
        self._treats_today = ("", 0)

    # --- the personality preset ---------------------------------------------------

    def _preset(self) -> dict:
        """The active preset's multipliers. ``balanced`` is an empty table, i.e. no lens at all."""
        return PERSONALITIES.get(str(self._config.get("personality", "balanced")), {})

    def _preset_factor(self, key: str) -> float:
        try:
            return max(0.01, float(self._preset().get(key, 1.0)))
        except (TypeError, ValueError):
            return 1.0

    # --- annoyance ----------------------------------------------------------------

    @property
    def annoyance(self) -> int:
        """Pokes counted inside the configured window."""
        return len(self._pokes)

    def _register_poke(self) -> None:
        self._pokes.append(self._clock_ms)
        self._residual += 1.0
        log.debug("poke registered (window=%d residual=%.1f)", len(self._pokes), self._residual)

    def _decay_annoyance(self, dt_ms: float) -> None:
        window_ms = float(self._config["annoyance_window_s"]) * 1000.0
        while self._pokes and self._clock_ms - self._pokes[0] > window_ms:
            self._pokes.popleft()
        decay_ms = max(1.0, float(self._config["annoyance_decay_s"]) * 1000.0)
        self._residual = max(0.0, self._residual - dt_ms / decay_ms)

    def _annoyance_threshold(self) -> int:
        """Pokes before he snaps, after the preset's patience multiplier. Never below one."""
        base = int(self._config["annoyance_threshold"])
        return max(1, int(round(base * self._preset_factor("annoyance"))))

    def _annoyance_exceeded(self) -> bool:
        threshold = self._annoyance_threshold()
        # Burst is the documented trigger ("4 pokes in 15s"). The residual is a slower backstop so
        # someone poking steadily just under the burst rate still eventually gets a reaction.
        return len(self._pokes) >= threshold or self._residual >= threshold * 2

    def _clear_annoyance(self) -> None:
        self._pokes.clear()
        self._residual = 0.0

    # --- external events ----------------------------------------------------------

    def noticed(self) -> None:
        """You did something with him. Resets the being-ignored clock.

        Every route that counts has to call this, including the ones that never touch his window:
        the global hotkey opens the bubble at your pointer without going anywhere near him, and so
        does the tray. Missing one means he sulks at somebody who has just used him.
        """
        if self._neglect_stage:
            log.debug("noticed after %.0fs of being ignored", self._neglect_ms / 1000.0)
        self._neglect_ms = 0.0
        self._neglect_stage = 0
        self._neglect_glance_ms = 0.0
        # Whatever it was, it was something to do: boredom gets knocked right back down.
        self.needs.nudge("interacted")

    def click(self) -> bool:
        """Handle a click on the mascot. Returns True if the ask bubble should open."""
        self._last_click_ms = self._clock_ms
        self.noticed()
        if self.napping:
            # Woken, not pestered: he gets up, with no grumble - that is the sulk's. No bubble
            # either; the greeting comes once he is on his feet, if you are back (see _tick_wake).
            self._wake_from_nap()
            return False
        if self._waking_from_nap:
            return False  # a second click while he is getting up is not worth a scowl
        if self._state in (State.SLEEP, State.WAKE):
            self._grumble_ms = GRUMBLE_MS  # a grumble, but he is not getting up for you
            return False
        if self._state in (State.ANGRY, State.HIDDEN) or self._pending_sleep:
            # _pending_sleep means he is mid-storm-off. That walk is part of the angry beat, so a
            # click must not hijack it into a bubble and cancel the sulk.
            return False
        # WORKING is deliberately interruptible: the brief only blocks clicks while he is asleep,
        # angry, or hidden, and making the user wait out the animation to send a second prompt
        # would be worse than cutting it short.
        self._sound("open")
        self._enter(State.ASK)
        return True

    def wheel_opened(self) -> bool:
        """The tool wheel just fanned out round him. Stand still for it; returns whether he did.

        It is drawn centred on where he is, so he cannot wander off out of the middle of it - the
        same reason the ask bubble holds him in ``State.ASK``, which is reused here rather than
        inventing a state that would look identical. Unlike a click it is not refused while he is
        asleep or cross, because nothing about it needs him to perform: he is not held, and
        grumbles if asleep, and the caller closes the wheel if he moves out from under it.
        """
        self.noticed()
        self._wheel_hold = False
        self._wheel_look = None
        if self.napping:
            # Same as a click: up from a nap with no grumble. The wheel still opens round him.
            self._wake_from_nap()
        elif self._state in (State.SLEEP, State.WAKE) and not self._waking_from_nap:
            # Same as a click: a grumble, and he is not getting up for you - the wheel still opens.
            # The click time also keeps _tick_sleep's near-the-end hover wake off him meanwhile.
            self._last_click_ms = self._clock_ms
            self._grumble_ms = GRUMBLE_MS
        # PERCH too: standing on a window he is already still, and leaving that state would stop
        # him following the window - while he stayed flagged as standing on it. Grabbing him is the
        # only way off a perch, and right-clicking him must not become a second one.
        if self._state in (State.SLEEP, State.WAKE, State.ANGRY, State.HIDDEN, State.DRAG,
                           State.PERCH):
            return False
        if self._pending_sleep:
            return False
        self._enter(State.ASK)
        self._wheel_hold = True
        return True

    def wheel_aimed(self, point: tuple[int, int] | None) -> None:
        """The wheel's pointer moved onto a tool (its centre, screen px) or off all of them (None).

        Only while the wheel holds him: asleep or perched he is not watching it. The frame is held
        here as well as on the tick, so his eyes move the moment yours do rather than a tick later.
        """
        if not self._wheel_hold or self._state != State.ASK or point is None:
            self._wheel_look = None
            self._sync_animation()
            return
        self._wheel_look = (int(point[0]), int(point[1]))
        # A blink in flight would never see its end once the eye frame is held over it, and he
        # would stop blinking for the rest of the hold.
        self._blinking = False
        if self._sheet.has_state(EYE_TRACK_STATE):
            self.animator.hold(EYE_TRACK_STATE, self._track_frame_index(self._wheel_look))

    def wheel_closed(self, picked: bool = True) -> None:
        """The wheel went away. Let him go - unless a tool it picked has already given him a job.

        Deliberately not a poke, unlike dismissing the bubble: putting the wheel away without
        choosing is browsing, not brushing him off. It does get a head scratch, though, so a cancel
        looks different from a pick. Called before the chosen tool runs, so a tool that sets his
        state (Ask, Focus) still gets the last word.

        The app's own closes - he drifted out from under it, the guard went up, a DPI rebuild -
        arrive as cancels too. Drifting means he already left ASK, and the guard hides him straight
        afterwards, so only the rare rebuild ever scratches, which is harmless. Paused, he gets no
        scratch: update() would freeze it mid-pose until you resumed him.
        """
        # Only a hold the wheel took itself: whatever state he was left in otherwise is not the
        # wheel's to end.
        held = self._wheel_hold and self._state == State.ASK
        self._wheel_hold = False
        self._wheel_look = None
        if not held:
            return
        if (not picked and not self.paused and not (self._falling or self._rolling)
                and self._sheet.has_state(WHEEL_CANCEL_ANIM)):
            self._begin_antic(WHEEL_CANCEL_ANIM, WHEEL_CANCEL_MS)
        else:
            self._enter(State.IDLE)

    def tool_picked(self, key: str) -> bool:
        """A wheel tool just ran. Gives the silent ones a beat (TOOL_BEATS); returns whether he did.

        Runs after the tool's own handler, from the IDLE wheel_closed left him in. Anything else -
        a handler that already gave him a job, asleep, perched, paused - is left exactly as it is.
        Paused is checked after the handler on purpose: pausing leaves him IDLE but frozen, so the
        beat lands only on resume. No noticed() here: the wheel opening already counted.
        """
        beat = TOOL_BEATS.get(key)
        if beat is None or self.paused or self._state != State.IDLE:
            return False
        if self._falling or self._rolling or not self._sheet.has_state(beat[0]):
            return False
        log.debug("wheel beat: %s -> %s", key, beat[0])
        self._begin_antic(*beat)
        return True

    def bubble_dismissed(self) -> None:
        """The bubble closed without sending - that is a poke."""
        self.noticed()
        self._register_poke()
        self._sound("poke")
        self.mood.nudge("poked")
        if self._annoyance_exceeded():
            self._begin_anger()
        elif self._sheet.has_state(POKE_FLINCH_ANIM):
            # A brief glare. Every poke below the threshold used to be completely invisible, so
            # the fourth one arriving as a full storm-off had no build-up at all.
            self._begin_antic(POKE_FLINCH_ANIM, POKE_FLINCH_MS)
        elif self._state == State.ASK:
            self._enter(State.IDLE)

    def prompt_sent(self, text: str = "") -> None:
        """A prompt actually went to Claude. Not a poke, and it takes the edge off.

        ``text`` is optional and defaults to empty, because the launch path is what matters and
        several callers legitimately have nothing to hand it. When it is supplied he reacts to the
        *shape* of it once the working animation finishes - see ``_prompt_reaction``.
        """
        self.noticed()
        self._pokes.clear()
        self._residual = max(0.0, self._residual - 1.0)
        self.mood.nudge("prompt_sent")
        self.needs.nudge("prompt_sent")
        self.memory.record("prompts", self._date_key())
        self._sound("send")
        self._post_prompt = self._prompt_reaction(text)
        self._working_ms = random.uniform(WORKING_MIN_MS, WORKING_MAX_MS)
        self._enter(State.WORKING)

    def launch_failed(self) -> None:
        """You asked, and the app did not open. A double-take, never "on it".

        Not a poke, and none of what a send does either: no mood nudge, no "send" sound, no
        working beat and no reaction to the prompt - nothing went anywhere to react to. The pokes
        stay too, since it is a *successful* send that takes the edge off.
        """
        self.noticed()
        self._post_prompt = None
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN, State.ANGRY):
            return
        if self._pending_sleep:
            return
        # Paused, no double-take - it would freeze on its first frame - but ASK still has to end.
        if not self.paused and self._sheet.has_state(LAUNCH_FAILED_ANIM):
            self._begin_antic(LAUNCH_FAILED_ANIM, LAUNCH_FAILED_MS)
        elif self._state == State.ASK:
            self._enter(State.IDLE)  # the bubble submitted from ASK, and nothing else would end it

    def _prompt_reaction(self, text: str) -> tuple[str, float, str] | None:
        """Pick an animation from the shape of what you just asked, or None for no comment.

        Matched against a fixed keyword table and then discarded: nothing about the prompt is
        stored here and nothing about it is logged, including at DEBUG. The log line below names
        the *animation*, never the text that chose it.
        """
        if not text or not bool(self._config.get("prompt_reactions", True)):
            return None
        lowered = text.lower()
        for keywords, animation, low, high in PROMPT_REACTIONS:
            if any(word in lowered for word in keywords):
                moment = self._moment(animation, low, high)
                if moment is not None:
                    log.debug("reacting to the prompt with %s", animation)
                    return moment
        if len(text) >= PROMPT_LONG_CHARS:
            moment = self._moment(PROMPT_LONG_ANIM, 900, 1300)
            if moment is not None:
                log.debug("that was a long one - %s", PROMPT_LONG_ANIM)
                return moment
        return None

    def calculated(self, ok: bool) -> None:
        """The inline calculator just answered something, instead of a prompt going anywhere.

        Deliberately NOT routed through ``prompt_sent``: a calculation is not a thing that was
        asked of the assistant, so it must not clear annoyance, nudge the mood, make the "send"
        sound, or enter ``State.WORKING`` the way a real prompt does - see docs/PRIVACY.md and
        docs/USING-HIM.md on why it stays out of prompt history too. He still needs to know you
        were just here, though, or a calculator-heavy stretch reads as being ignored - see
        ``noticed``'s own docstring on why every real interaction has to call it.

        ``ok`` is whether the answer came out clean. A broken one - dividing by zero, say - gets
        acknowledged by simply not being ignored; it is not a fist-pump moment, so it earns no
        animation of its own.
        """
        self.noticed()
        if not ok or self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN):
            return
        if not self._sheet.has_state(CALCULATED_ANIM):
            return
        self._begin_antic(CALCULATED_ANIM, random.uniform(*CALCULATED_MS))

    def read_image(self, ok: bool) -> None:
        """He just read the text out of an image you Shift-dropped on him.

        Same shape as ``calculated`` and the same reasoning: this is not a prompt, so it stays
        out of ``prompt_sent`` entirely - no mood nudge, no "send" sound, no ``State.WORKING``,
        no prompt history. ``noticed`` still fires unconditionally so a stretch of dropped
        screenshots never reads as ignoring him. ``ok`` false covers everything from "no text in
        that image" to "OCR isn't available right now" - none of it is worth an animation, only
        acknowledgement that you were here.
        """
        self.noticed()
        if not ok or self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN):
            return
        if not self._sheet.has_state(READING_ANIM):
            return
        self._begin_antic(READING_ANIM, random.uniform(*READING_MS))

    def talking_while_muted(self) -> bool:
        """You have been talking into a muted microphone. Returns whether he showed it.

        The one thing in here he tells *you*, rather than a reaction to something you did to him -
        which is exactly why it must not call ``noticed``. Nothing about your microphone is an
        interaction with him, and counting it as one would let a long muted call quietly suppress
        the being-ignored stages that are supposed to fire when you are at the machine and not
        looking at him.

        It also does not touch the mood. Being unheard is not his good or bad day, and a meeting
        you spent muted should not still be colouring how he behaves an hour later.

        Suppressed while he is asleep, in your hand, or hidden, the same as every other antic -
        the caller still puts a toast up in those cases, so the message never depends on him being
        in a state to perform it. See main.NibApp._tick_devices for the dwell and the cooldown.
        """
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN):
            return False
        if not self._sheet.has_state(MUTED_ANIM):
            return False
        self._begin_antic(MUTED_ANIM, MUTED_MS, costume=MUTED_COSTUME)
        return True

    def drag_start(self) -> None:
        if self._state == State.HIDDEN:
            return
        self.noticed()
        # A sleeping mascot can still be picked up. He ignores *clicks* while sulking, but refusing
        # to be moved at all for the whole cooldown reads as the app having hung. The sleep is not
        # cancelled - the remaining time is restored once he lands. A nap keeps its flag through
        # the carry and the fall (see _enter), and never counts its _sleep_ms down, so it lands
        # back as the same nap with no clock rather than as a sulk-length sleep.
        if self._state == State.SLEEP:
            self._resume_sleep_ms = max(self._resume_sleep_ms, self._sleep_ms)
        # Grabbing him off a perch is the "remove it" gesture - deliberately un-graceful, the same
        # way picking him up mid-fall or mid-tantrum interrupts whatever he was doing rather than
        # waiting for a tidy moment. main.py stops polling the window on its next tick, once
        # perched reads False here.
        self._perch_rect = None
        self._falling = False
        self._rolling = False
        self._pending_injury = False
        # Caught again mid-flight: the landing that was on its way never happens.
        self._gentle_pending = self._throw_pending = False
        self._vx = self._vy = 0.0
        self._drag_samples.clear()
        self._drag_last = None
        self._y = float(self.rest_top())
        self._struggle_ms = 0.0
        self._enter(State.DRAG)

    def drag_to(self, x: int, y: int) -> None:
        if self._state != State.DRAG:
            return
        # Horizontally clamped to the strip, vertically free so he can still be lifted right up.
        # Without the clamp he follows the pointer clean off the end of the desktop and is only
        # snapped back on the next geometry poll - and a throw started from out there reflects off
        # a wall he was already past, firing him back the way he came.
        self._x = min(max(float(x), self.x_min), self.x_max)
        self._y = float(y)

    def drag_end(self, perch_rect: tuple[int, int, int, int] | None = None) -> None:
        """Let go. Lands standing on ``perch_rect`` if you set him down gently on a window -
        judged by the same release speed a throw already measures, so a real flick still flies
        through it like anywhere else rather than sticking. Otherwise falls exactly as he always
        has - ``perch_rect`` only ever arrives from main.py having already found a real window
        under the drop point; nothing here goes looking for one."""
        if self._state != State.DRAG:
            return
        vx, vy = self._throw_velocity()
        if perch_rect is not None and math.hypot(vx, vy) <= PERCH_MAX_RELEASE_SPEED_PX_MS:
            self.perch_on(perch_rect)
            return
        # Let go of the squirm so the fall reads as a clean drop rather than a wobble.
        self._y += self._struggle_dy
        self._x += self._struggle_dx
        self._struggle_dx = 0.0
        self._struggle_dy = 0.0
        self._falling = True
        self._rolling = False
        self._vx, self._vy = vx, vy
        self._fall_from_y = self._y  # remembered so the landing knows how far he fell
        self._drag_samples.clear()
        self._drag_last = None
        self._throw_pending = abs(self._vx) > THROWN_ABOVE_PX_MS
        # Never both. A release between 0.4 and 0.5 px/ms sideways is slow enough to perch, but it
        # is still a flick, and a flick is play rather than care. Under the drop that hurts is the
        # injury rule's own figure, so a gentle set-down can never also be a hard landing.
        self._gentle_pending = (
            not self._throw_pending
            and math.hypot(vx, vy) <= PERCH_MAX_RELEASE_SPEED_PX_MS
            and self.rest_top() - self._y < INJURY_FALL_PX
        )
        if self._throw_pending:
            log.debug("thrown at %.2f,%.2f px/ms", self._vx, self._vy)
            self.memory.record("throws", self._date_key())
            self.needs.nudge("thrown")
        self._enter(State.IDLE)

    # --- perching on a window -------------------------------------------------------

    @property
    def perched(self) -> bool:
        """True whenever a perch rectangle is held - not quite the same as State.PERCH.

        A sneeze, a wave, or clicking him to ask something all leave State.PERCH for as long as
        they run without releasing the perch itself (see the ``top`` property and
        _choose_next_action's reassertion). main.py polls the target window on this, not on the
        state, so a brief interruption never silently stops the window-following or the
        gone-window detection - both would otherwise resume only once _choose_next_action next
        happened to run, which is not guaranteed to be soon, or at all, once he's off doing
        something with an antic that holds itself open-ended.
        """
        return self._perch_rect is not None

    def perch_on(self, rect: tuple[int, int, int, int]) -> None:
        """Land standing on ``rect`` instead of falling - called from drag_end when the drop
        point was over a window. Purely cosmetic: he reads the rectangle and follows it, the same
        as the overhead-window ledge already does, and nothing here ever moves, resizes or
        restacks the window itself - see update_perch for the follow, and _reposition_perch for
        where exactly he stands on it.
        """
        left, right = rect[0], rect[2]
        width = max(1, (right - left) - self.sprite_w)
        self._perch_frac = max(0.0, min(1.0, (self._x - left) / width))
        self._perch_rect = rect
        self._falling = False
        self._rolling = False
        self._pending_injury = False
        # Standing on a window is a landing of its own, and never reaches _settle: no sparkle.
        self._gentle_pending = self._throw_pending = False
        self._vx = self._vy = 0.0
        self._enter(State.PERCH)
        self._reposition_perch()

    def _reposition_perch(self) -> None:
        """Recompute (x, y) from the held rectangle - his own feet stay welded to its top edge,
        proportionally along its width, the same idea as how the taskbar peek keeps his window's
        bottom edge welded to the bar rather than tracking an absolute screen position."""
        if self._perch_rect is None:
            return
        left, top, right, _bottom = self._perch_rect
        width = max(1, (right - left) - self.sprite_w)
        self._x = left + self._perch_frac * width
        self._y = float(top - (self._sheet.frame_h - self._sheet.baseline_offset) * self._sheet.scale)

    def update_perch(self, rect: tuple[int, int, int, int] | None) -> None:
        """Called every tick while perched, with the window's current rectangle - or None once
        it's gone, minimized, or hidden behind something covering the whole monitor. Losing it
        steps him back down with the same fall physics an ordinary drag-release uses, straight
        down rather than thrown, since nothing flicked him.
        """
        if self._state != State.PERCH:
            return
        if rect is None:
            log.info("perched window is gone - stepping back down")
            self._release_perch()
            self._falling = True
            self._rolling = False
            self._vx = self._vy = 0.0
            self._fall_from_y = self._y
            self._enter(State.IDLE)
            return
        self._perch_rect = rect
        self._reposition_perch()

    def _release_perch(self) -> None:
        """Clear the held rectangle - the shared first step of every way off a perch.

        Deliberately just the clear, nothing else: update_perch's window-gone case wants a fall
        afterward, but _begin_anger and come_here move him some other way immediately after this
        (storming off, walking to your pointer) and a fall on top of that would fight whichever
        one it was. Either way, the point is the same - both are paths that move him without going
        through _choose_next_action's reassertion, which a live rectangle would otherwise survive:
        the ``top`` override reads it unconditionally, so his feet would stay welded to the window
        while the walk slid his x out from underneath them.
        """
        self._perch_rect = None

    # --- being petted -------------------------------------------------------------

    def _cursor_is_over(self, cursor: tuple[int, int] | None) -> bool:
        """Is the pointer actually on his sprite? Not "near" - on."""
        if cursor is None:
            return False
        cx, cy = cursor
        return (
            self.x <= cx < self.x + self.sprite_w
            and self.top <= cy < self.top + self.visible_height
        )

    def _track_cursor(self, dt_ms: float, cursor: tuple[int, int] | None) -> None:
        """Accumulate how long the pointer has held still, and re-arm the pet when it leaves.

        Runs on every tick in every state, because both halves have to be true across state
        changes: the stillness that earns a pet is measured while he is walking about, and the
        re-arm has to happen even while he is asleep or being dragged.
        """
        over = self._cursor_is_over(cursor)
        if not over:
            self._pet_armed = True
        elif not self._cursor_was_over:
            # Your pointer arriving ON him counts as noticing him, right now - not in half a
            # second once the pet has earned its hold. This used to fall out of ``_maybe_wave``,
            # which called ``noticed()``; now that the wave stands aside for a pending pet, the
            # reset has to live here or he carries on sulking at somebody whose cursor is
            # literally on top of him.
            self.noticed()
        self._cursor_was_over = over
        if cursor is None:
            self._cursor_anchor = None
            self._cursor_still_ms = 0.0
            return
        anchor = self._cursor_anchor
        if anchor is None or max(abs(cursor[0] - anchor[0]), abs(cursor[1] - anchor[1])) > PET_JITTER_PX:
            self._cursor_anchor = cursor
            self._cursor_still_ms = 0.0
        else:
            self._cursor_still_ms += dt_ms

    def _pet_hold_ms(self) -> float:
        return max(0.0, float(self._config.get("pet_hold_ms", 550)))

    def _maybe_pet(self, cursor: tuple[int, int] | None) -> bool:
        """Start a pet if the pointer is resting on him. Returns True if it began.

        Checked *before* ``_maybe_wave``, and that order is load-bearing: the wave fires at
        ``hover_wave_distance_px`` (120px by default), so a pointer coming to rest on him is
        already deep inside wave range. Checked second, this would never once have run.
        """
        if not bool(self._config.get("petting_enabled", True)):
            return False
        if self._pet_cooldown_ms > 0 or not self._pet_armed or self._away:
            return False
        if self._falling or self._rolling:
            return False
        if not self._cursor_is_over(cursor):
            return False
        if self._cursor_still_ms < self._pet_hold_ms():
            return False
        self._pet_armed = False
        self.noticed()
        self.mood.nudge("petted")
        self.needs.nudge("petted")
        self.memory.record("pets", self._date_key())
        log.info("being petted")
        self._enter(State.PET)
        return True

    def _pet_forgive_every_ms(self) -> float:
        """How long a pet takes to forgive one poke: less, the better he knows you.

        Exactly ``PET_DRAIN_MS`` at a bond of 0. Read on every pet tick rather than cached when
        the pet begins: the bond is four logarithms, and the pet that started it has just been
        counted towards it.
        """
        return PET_DRAIN_MS * (1.0 - FORGIVE_BOND_SPEEDUP * self.memory.bond())

    def _tick_pet(self, dt_ms: float, cursor) -> None:
        """Lean into it for as long as the hand stays, forgiving a poke as he goes."""
        self._advance(dt_ms)
        # Draining the annoyance is the entire point of the feature: until this existed there was
        # no way at all to take it back off, only to wait it out.
        every = self._pet_forgive_every_ms()
        self._pet_drain_ms += dt_ms
        while self._pet_drain_ms >= every:
            self._pet_drain_ms -= every
            if self._pokes:
                self._pokes.popleft()
            self._residual = max(0.0, self._residual - 1.0)
        if not self._cursor_is_over(cursor) or self._state_ms >= PET_MAX_MS:
            self._pet_cooldown_ms = PET_COOLDOWN_MS
            self._pet_drain_ms = 0.0
            self._enter(State.IDLE)

    # --- coming when called -------------------------------------------------------

    def come_here(self, x: float) -> bool:
        """Walk to screen ``x`` (his centre, clamped to the strip). True if he set off.

        Being *called* counts as attention, which is why this ends a sulk rather than being
        refused by one: he is put out because you were not paying attention, and you just did.
        Only a drag refuses outright - he is already in your hand - and being hidden, where there
        is nothing on screen to come.

        Note ``_begin_walk`` drops straight back to idle for a target under 4px away. A pointer
        already sitting on him is therefore a no-op rather than a bug, and returns False.
        """
        if self._state in (State.HIDDEN, State.DRAG) or self._falling or self._rolling:
            return False
        self.noticed()
        if self._state in (State.SLEEP, State.WAKE, State.ANGRY):
            log.info("called while %s - getting up", self._state.value)
            self._pending_sleep = False
            self._clear_annoyance()
            self._enter(State.IDLE)
        if self._focus_active:
            # Being called relocates him across the screen by explicit request - the same reason
            # this ends a sulk also means it cannot leave a focus session claiming he is still at
            # his post. Cancelled, not paused: you moved him yourself.
            self.set_focus(False)
        if self._perch_rect is not None:
            # Same reasoning again: the walk below moves _x directly, and a live perch rectangle
            # would keep his feet welded to the window while it does.
            self._release_perch()
        target = float(x) - self.sprite_w / 2.0
        self._begin_walk(min(max(target, self.x_min), self.x_max))
        return self._state == State.WALK

    def set_hidden(self, hidden: bool, *, greet: bool = False) -> None:
        """Hide him, or bring him back. ``greet`` is for a return worth a wave (BACK_ANTIC).

        Only the snooze and the fullscreen return pass it. The guard does not: it has its own way
        back in.
        """
        if hidden and self._state != State.HIDDEN:
            self._prev_state = self._state
            self._hidden_since_ms = self._clock_ms
            # A fall cut short here finishes when he comes back, which can be hours later: by then
            # a sparkle for how he was let go would answer nothing anybody remembers doing.
            self._gentle_pending = self._throw_pending = False
            self._enter(State.HIDDEN)
        elif not hidden and self._state == State.HIDDEN:
            # Only resume states that still make sense after an arbitrary gap. A fullscreen game
            # can last hours, by which point a tantrum, a nap, a drag, or a half-typed bubble is
            # stale - so anything transient comes back as plain idle. PERCH is the one exception
            # to "transient": it is a deliberate, stable arrangement the same way WALK's
            # destination is. update_perch is a no-op outside State.PERCH, so the held rectangle
            # is simply whatever it last was - stale for at most the one tick it takes to resume
            # and re-poll, at which point a window that closed while hidden is caught exactly the
            # same way as one that closes in plain sight.
            resumable = self._prev_state in (State.IDLE, State.WALK, State.PERCH)
            self._enter(self._prev_state if resumable else State.IDLE)
            # Only from plain idle: a wave would pull him off a perch or lose a walk's target. And
            # not over a hold he is about to resume - the away-sit, the sign, the focus pose. Not
            # paused either: update() would hold the wave on its first frame until you resumed him.
            if (
                greet
                and not self.paused
                and self._state == State.IDLE
                and not (self._away or self.pocket_full or self._focus_active)
                and self._clock_ms - self._hidden_since_ms >= BACK_MIN_HIDDEN_MS
                and self._clock_ms - self._back_at_ms >= BACK_COOLDOWN_MS
                and self._sheet.has_state(BACK_ANTIC)
            ):
                log.debug("back from hiding - waving")
                self._back_at_ms = self._clock_ms
                self._begin_antic(BACK_ANTIC, BACK_MS)

    # --- state helpers ------------------------------------------------------------

    def _enter(self, state: State) -> None:
        if state == self._state:
            return
        log.debug("state %s -> %s", self._state.value, state.value)
        self._state = state
        self._state_ms = 0.0
        self._blinking = False

        # _pending_sleep is only meaningful across the ANGRY -> storm-off WALK -> SLEEP sequence.
        # Leaving that sequence by any other route (a drag, a fullscreen hide, a send) must clear
        # it: click() refuses while it is set, so a stranded flag would make him unclickable for
        # the rest of the session.
        if state not in (State.ANGRY, State.WALK):
            self._pending_sleep = False
        if state not in (State.WALK, State.ANTIC):
            # Same reasoning as _pending_sleep: leaving the walk-then-act sequence by any other
            # route must drop the action, or it fires after some unrelated later walk.
            self._pending_action = None
        # A nap is SLEEP, carried through a drag and the fall after it - both land back in SLEEP
        # (_settle) - and nothing else. Every other way out ends it here, so none of the dozen
        # routes out of SLEEP (a send, a call, a hide, a focus session) can leave the flag behind.
        if (self._napping and state not in (State.SLEEP, State.DRAG)
                and not (self._falling or self._rolling)):
            self._end_nap()
        # The greeting a nap owes lives through the nap and the WAKE after it, and no further.
        if state != State.WAKE and not self._napping:
            self._nap_greeting = None
        if state != State.ANTIC:
            self._persona_costume = ""  # the hat belongs to the antic, not to him
            self._antic_queue = []      # and the rest of a sequence belongs to the sequence
            self._flush_quick_change()  # grabbed, hidden, petted or clicked mid-hop

        if state == State.IDLE:
            self._idle_timer_ms = self._next_wander_delay()
            self._blink_timer_ms = random.uniform(BLINK_MIN_MS, BLINK_MAX_MS)
        elif state == State.SLEEP:
            self._sleep_ms = float(self._config["sleep_cooldown_s"]) * 1000.0
        elif state == State.PEEK:
            # Spent here rather than at the call site, so entering a peek always costs the
            # cooldown. Leaving that to the caller let him peek, land back in idle, and duck
            # straight down again a beat later.
            self._peek_cooldown_ms = PEEK_COOLDOWN_MS
        elif state == State.PET:
            self._pet_drain_ms = 0.0
        elif state in (State.PERCH, State.ASK):
            self._blink_timer_ms = random.uniform(BLINK_MIN_MS, BLINK_MAX_MS)
        if state in (State.SLEEP, State.WAKE, State.PET):
            self._sound(state.value)
        self._sync_animation(restart=True)

    def _next_wander_delay(self) -> float:
        beat = self._preset_factor("beat")
        low = float(self._config["wander_min_interval_s"]) * 1000.0 * beat
        high = float(self._config["wander_max_interval_s"]) * 1000.0 * beat
        if high < low:
            low, high = high, low
        return random.uniform(low, high)

    def _animation_for_state(self) -> str:
        if self._grumble_ms > 0 and self._state in (State.SLEEP, State.WAKE):
            return "angry"
        # Airborne, or still rolling along the ground: he tumbles either way, whatever he was
        # doing beforehand. Rolling reuses the spin frames exactly, which is why it cost no art.
        if (self._falling or self._rolling) and self._sheet.has_state(TUMBLE_ANIM):
            return TUMBLE_ANIM
        if self._state == State.ANTIC:
            return self._antic or "idle"
        return {
            State.IDLE: self._blink_state if self._blinking else "idle",
            State.WALK: f"walk_{self._facing}",
            State.INJURED: INJURY_ANIM,
            State.HOVER_WAVE: "wave",
            # The wheel's tool being aimed at: one frame of the eye grid, held by _tick_ask - the
            # PEEK pattern, because anything that left ASK to look would end the wheel's hold.
            State.ASK: (
                EYE_TRACK_STATE
                if self._wheel_look is not None and self._sheet.has_state(EYE_TRACK_STATE)
                else self._blink_state if self._blinking else "idle"
            ),
            State.WORKING: "working",
            State.ANGRY: "angry",
            State.SLEEP: "sleep",
            State.WAKE: "wake",
            State.HIDDEN: "idle",
            # Peeking holds one frame of the eye-direction grid, so he is looking upward over the
            # edge rather than staring straight ahead while only his eyes are showing.
            State.PEEK: (
                EYE_TRACK_STATE if self._sheet.has_state(EYE_TRACK_STATE) else "idle"
            ),
            # A sheet without the pet frames falls back to the heart eyes, which says the same
            # thing with art that has been there all along.
            State.PET: (
                PET_ANIM if self._sheet.has_state(PET_ANIM)
                else "heart_eyes" if self._sheet.has_state("heart_eyes") else "idle"
            ),
            # Being hoisted around by the cursor is undignified - he scowls about it.
            State.DRAG: "angry",
            # Standing on a window: plain idle, blinks included - _tick_perch drives the blink
            # exactly the way _tick_idle does, just without the wander clock underneath it.
            State.PERCH: self._blink_state if self._blinking else "idle",
        }[self._state]

    def _sync_animation(self, *, restart: bool = False) -> None:
        desired = self._animation_for_state()
        if desired != self.animator.state:
            self.animator.set_state(desired, restart=True)
        elif restart:
            self.animator.set_state(desired, restart=True)

    # --- anger / sleep ------------------------------------------------------------

    def _begin_anger(self) -> None:
        log.info("annoyance threshold crossed - getting grumpy")
        if self._focus_active:
            # The storm-off walk below bypasses _choose_next_action entirely, which is the only
            # place the working-pose hold gets reasserted. Left running, focus_active would keep
            # reading True while he stomps off and sleeps - contradicting both his posture and the
            # tray tooltip. Cancelled outright rather than paused: same as stopping it yourself,
            # no completion reward for a session you got angry through.
            self.set_focus(False)
        if self._perch_rect is not None:
            # Same bypass, same fix: the storm-off walk moves _x directly, and a live perch
            # rectangle would otherwise keep his feet welded to the window while it does.
            self._release_perch()
        self._sound("grump")
        self.mood.nudge("angered")
        self._pending_sleep = True
        # Reacting consumes the accumulated annoyance. Without this the same pokes stay inside the
        # burst window and re-trigger anger on every further poke, compounding the reaction.
        self._clear_annoyance()
        self._enter(State.ANGRY)

    def _storm_off(self, cursor: tuple[int, int] | None) -> None:
        """Turn away from the pest and walk a short distance before sleeping it off."""
        distance = random.uniform(STORM_OFF_MIN_PX, STORM_OFF_MAX_PX)
        centre = self._x + self.sprite_w / 2
        away_left = cursor is not None and cursor[0] > centre
        target = self._x - distance if away_left else self._x + distance
        if target < self.x_min or target > self.x_max:
            target = self._x + distance if away_left else self._x - distance
        self._begin_walk(min(max(target, self.x_min), self.x_max))
        if self._state != State.WALK:
            # Cornered at the end of the strip with nowhere to storm off to - sleep on the spot.
            self._pending_sleep = False
            self._enter(State.SLEEP)

    # --- walking ------------------------------------------------------------------

    def _begin_walk(self, target: float) -> None:
        self._walk_from = self._x
        self._walk_target = min(max(target, self.x_min), self.x_max)
        if abs(self._walk_target - self._x) < 4:
            self._enter(State.IDLE)
            return
        self._facing = "right" if self._walk_target > self._x else "left"
        self._enter(State.WALK)

    # --- antics -------------------------------------------------------------------

    def _begin_antic(self, name: str, duration_ms: float, costume: str = "") -> None:
        self._flush_quick_change()  # any other antic cutting in mid-hop shows the costume now
        self._antic = name
        self._antic_ms = duration_ms
        self._last_antic = name
        # Assigned on every antic, not only the ones that want a hat: entering ANTIC from ANTIC is
        # a no-op in _enter, so a stale value here would leave him wearing the last persona's
        # glasses through whatever he does next.
        self._persona_costume = costume
        log.debug("antic: %s for %.0fms%s", name, duration_ms,
                  f" wearing {costume}" if costume else "")
        self._antic_overrun_ms = None
        self._antic_hold = duration_ms >= ANTIC_HOLD_MS
        self._antic_queue = []  # anything starting an antic directly drops a pending sequence
        self._enter(State.ANTIC)

    def _begin_sequence(self, steps: list[tuple[str, float]]) -> None:
        """Play antics back to back, each ending cleanly before the next starts.

        Steps this sheet has no art for are skipped rather than failing the lot. Anything that
        takes him out of ANTIC on the way - a click, a drag, a pet, a hide - drops the rest.
        """
        steps = [step for step in steps if self._sheet.has_state(step[0])]
        if not steps:
            self._enter(State.IDLE)
            return
        self._begin_antic(*steps[0])
        self._antic_queue = list(steps[1:])  # after the call, which clears it

    def _pick_antic(self, cursor: tuple[int, int] | None) -> tuple[str, float] | None:
        """Weighted random antic, never the same one twice in a row.

        The previous antic is excluded outright rather than merely de-weighted, so a repeat is
        impossible rather than just unlikely - the same thing twice running reads as a glitch.
        It comes back as soon as anything else has played, and it stays selectable if it is
        somehow the only option left.
        """
        audience = self._cursor_is_near(cursor, self._eye_track_distance())
        fidgets_on = bool(self._config.get("fidgets_enabled", True))
        available = [
            a for a in ANTICS
            if self._sheet.has_state(a[0]) and (fidgets_on or a[4] != FIDGET_CATEGORY)
        ]
        if not available:
            return None  # a trimmed-down sprite sheet should not crash the scheduler
        candidates = [a for a in available if a[0] != self._last_antic] or available

        mood = self._mood_weights()
        pool = []
        weights = []
        for name, weight, low, high, category in candidates:
            if category == "eyes" and audience:
                weight *= EYES_ANTIC_AUDIENCE_BOOST
            # The mood never picks anything itself; it only makes what he already does more or
            # less likely - press-ups at two in the morning, dancing on a Friday afternoon.
            weight *= mood.get(name, 1.0)
            pool.append((name, low, high))
            weights.append(weight)
        if not any(weights):
            weights = [1.0] * len(pool)  # a mood must never leave him with nothing to do
        if not pool:
            return None
        name, low, high = random.choices(pool, weights=weights, k=1)[0]
        return name, random.uniform(low, high)

    def _antic_settled(self, dt_ms: float, shown: int) -> bool:
        """The antic's clock has run out: may it end on this tick, or is he mid-gesture?

        A loop waits until the frame on screen - ``shown``, the index as this tick began, since the
        one this tick advanced to has not been painted yet - has his feet down, or until it has
        wrapped back round to its first frame. Judging by the advanced frame instead would cut the
        hop from its last airborne frame straight to idle and skip the landing. A one-shot plays
        to its last frame. Either way at most one more cycle, in real time at the
        current pace - a local cap, not a rescale of the durations themselves. Only ``hop`` ever
        leaves the ground today, and it used to end mid-air about half the time. The one-shot case
        is the night bug: ``task_finished`` runs on the mood-paced clock, so at 0.6 pace a
        real-time timer cut it off before the settle frame. Eye tracking and the indefinite holds
        are exempt - the frame there is chosen, not played, and a hold is ended on purpose.
        """
        if self._antic == EYE_TRACK_STATE or self._antic_hold:
            return True
        state = self.animator.state  # what is actually on screen, not what was asked for
        if self._antic_overrun_ms is None:
            cycle_ms = self._sheet.total_duration(state)
            self._antic_overrun_ms = cycle_ms / max(0.1, self._mood_pace())
            self._antic_overrun_from = shown
        else:
            self._antic_overrun_ms -= dt_ms
        if self._antic_overrun_ms <= 0:
            return True
        if not self._sheet.loops(state):
            return self.animator.finished
        if shown < self._antic_overrun_from:
            return True  # wrapped back round to the first frame
        return self._sheet.foot_print(state, shown)[2] == 0

    # --- reactions to you ---------------------------------------------------------

    def set_away(self, away: bool, gone_s: float | None = None) -> None:
        """Tell him whether you are at the machine. He settles to wait, and greets you back.

        ``gone_s`` is how long you were away, measured by the app on the wall clock; it sizes the
        greeting (see GREET_OVERNIGHT). None gets the plain hop.
        """
        if away == self._away:
            return
        self._away = away
        # Ahead of the state check below: he can be asleep or in your hand as you leave, and the
        # ten minutes the nap waits for still start now.
        self._away_since_ms = self._clock_ms if away else None
        if not away:
            # You have just come back. Whatever he had worked himself up to before you left is
            # stale; start the clock again rather than carrying on where he left off.
            self.noticed()
            if self._napping or self._nap_greeting is not None:
                # Back to him mid-nap, or while he is still getting up from one. The greeting
                # waits until he is on his feet (_tick_wake): this one's absence replaces any owed.
                # Carried or mid-air, he wakes once he has landed back in it - see _tick_sleep.
                self._nap_greeting = (gone_s,)
                if self.napping:
                    self._wake_from_nap()
                return
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN, State.ANGRY):
            return
        if away:
            if self._sheet.has_state(AWAY_ANTIC):
                log.debug("user away - settling down to wait")
                self._begin_antic(AWAY_ANTIC, 1e9)  # held until you come back
        else:
            self._greet(gone_s)

    def _greet(self, gone_s: float | None) -> None:
        """Greet you back - from set_away, or once he is up from a nap (_tick_wake)."""
        log.debug("user back - greeting")
        self.mood.nudge("greeted")
        self.needs.nudge("greeted")
        self._begin_sequence(self._greeting_for(gone_s))

    def _greeting_for(self, gone_s: float | None) -> list[tuple[str, float]]:
        """The greeting for coming back after ``gone_s`` seconds, sized by the absence.

        Two things he remembers can add one beat to the end of it - never replace it, so the
        greeting you know is always there first:

        * **A heavy yesterday** (``Memory.heavy_yesterday``): the overnight wake-up on a morning
          after a long day ends with a coffee. Only overnight and only in the morning - a coffee
          after lunch says nothing about yesterday.
        * **A close bond** (``GREET_BOND_MIN``): the ordinary hop is followed by heart eyes. Not the
          wave (a coffee break is not a reunion) and not the overnight one, which is long enough.

        Raises a ``greeted`` event either way, for the lines that arrive with voice.py.
        """
        overnight = heavy = False
        if gone_s is None or GREET_HOP_AFTER_S <= gone_s < GREET_OVERNIGHT_AFTER_S:
            steps = [(GREET_ANTIC, GREET_MS)]
            if self.memory.bond() >= GREET_BOND_MIN:
                steps.append(GREET_BOND_EXTRA)
        elif gone_s < GREET_HOP_AFTER_S:
            steps = [(GREET_WAVE_ANTIC, GREET_WAVE_MS)]
        else:
            overnight = True
            # The overnight greeting already has the stretch in it, so it counts as today's first
            # sight of you - otherwise the "first sight today" stretch follows straight after it.
            if self._world is not None and self._world.hour >= NEW_DAY_HOUR:
                self._latch("last_seen_date", self._world.date_key)
            steps = list(GREET_OVERNIGHT)
            heavy = (
                self._world is not None
                and self._world.part_of_day == PART_MORNING
                and self.memory.heavy_yesterday(self._date_key())
            )
            if heavy:
                steps.append(GREET_HEAVY_EXTRA)
        self._event("greeted", gone_s=gone_s, overnight=overnight, heavy=heavy)
        return steps

    @property
    def away(self) -> bool:
        return self._away

    # --- the nap ------------------------------------------------------------------------

    @property
    def napping(self) -> bool:
        """Asleep because he was worn out and you were away - not sulking. The tray says so.

        Only while actually in SLEEP: carried or mid-air the flag survives (he lands back in the
        nap), but he is not lying there asleep, so nothing outside should say he is.
        """
        return self._napping and self._state == State.SLEEP

    @property
    def _waking_from_nap(self) -> bool:
        """In the WAKE that ends a nap, rather than the one that ends a sulk."""
        return self._state == State.WAKE and self._nap_greeting is not None

    def _maybe_nap(self, part_of_day: str | None) -> None:
        """Doze off, if he is worn out, it is night, and you have been gone ten minutes.

        Only from his away pose - sitting waiting for you - so never mid-antic, in your hand, on a
        window, mid-air, or mid-tantrum. A focus session needs its own check: away outranks focus
        in _choose_next_action, so a session paused by your absence *is* in the away pose. Hidden
        (a fullscreen app, the snooze, the guard) and paused never get here: update has returned.
        """
        if not self._away or self._away_since_ms is None:
            return
        if self._state != State.ANTIC or self._antic != AWAY_ANTIC:
            return
        if not bool(self._config.get("nap_enabled", True)) or not self.needs.enabled:
            return
        if part_of_day != PART_NIGHT or self.needs.energy >= NAP_ENERGY_BELOW:
            return
        if self._clock_ms - self._away_since_ms < NAP_AFTER_AWAY_MS:
            return
        if self._focus_active or self._perch_rect is not None or self._falling or self._rolling:
            return
        log.info("worn out, late, and you have gone - dozing off")
        self._napping = True
        self._nap_since_ms = self._clock_ms
        self._enter(State.SLEEP)

    def _wake_from_nap(self) -> None:
        """Up from a nap: no grumble, and a greeting owed for once he is on his feet.

        A greeting already owed (set_away stored this absence) is kept. One that is not - a click
        woke him - is owed with no length, and _tick_wake drops it if you still read as away by
        then: set_away(False) greets you the ordinary way when you are.
        """
        if self._nap_greeting is None:
            self._nap_greeting = (None,)
        self._enter(State.WAKE)  # ends the nap - see _enter

    def _end_nap(self) -> None:
        """The nap is over, however it ended - called from _enter, the one way out of SLEEP.

        Rested in proportion to how long it lasted (needs' "nap_woke", whole at NAP_FULL_H). The
        ten minutes before he may drop off again start over: you just woke him, so a click from a
        hand nobody has registered as back yet must not see him nod straight off again.
        """
        self._napping = False
        # drag_start owes a carried nap the whole cooldown back. A throw has already spent that
        # in _settle, which zeroes it before landing him back in SLEEP; any other end - set down
        # on a window, or hidden or a focus session started mid-carry - must drop it here, or the
        # next ordinary landing would put him down for a sulk-length sleep he never earned.
        self._resume_sleep_ms = 0.0
        napped_h = max(0.0, self._clock_ms - self._nap_since_ms) / 3_600_000.0
        self.needs.nudge("nap_woke", scale=min(1.0, napped_h / NAP_FULL_H))
        if self._away:
            self._away_since_ms = self._clock_ms
        log.info("up from a %.0f-minute nap", napped_h * 60.0)

    # --- focus sessions -------------------------------------------------------------

    def set_focus(self, active: bool) -> None:
        """Tray toggle: start a session at the configured length, or cancel it early.

        Mirrors set_away's shape on purpose. A cancelled session earns nothing - no sound, no
        mood nudge, no celebration - because it was not finished. That closes the obvious
        start-then-immediately-stop loophole for free.
        """
        if active == self._focus_active:
            return
        if active:
            minutes = max(1.0, float(self._config.get("focus_duration_min", 25)))
            self._focus_active = True
            self._focus_total_ms = minutes * 60_000.0
            self._focus_remaining_ms = self._focus_total_ms
            self._focus_burst_due_ms = random.uniform(
                FOCUS_BURST_INTERVAL_MIN_MS, FOCUS_BURST_INTERVAL_MAX_MS)
            log.info("focus session started: %.0f min", minutes)
            # Right here, not left to wait for _choose_next_action's reassertion: that check
            # exists to survive an INTERRUPTION, but the start itself is a direct tray click and
            # deserves the same instant feedback pocket_grab/set_away's away-branch already give -
            # otherwise he would keep wandering for however long was left of whatever he was
            # already doing before the pose caught up.
            if self._sheet.has_state(FOCUS_HOLD_ANIM):
                self._begin_antic(FOCUS_HOLD_ANIM, 1e9, costume=FOCUS_COSTUME)
        else:
            log.info("focus session cancelled with %.0fs left", self.focus_remaining_s)
            self._focus_active = False
            self._focus_remaining_ms = 0.0
            self._focus_total_ms = 0.0

    @property
    def focus_active(self) -> bool:
        return self._focus_active

    @property
    def focus_remaining_s(self) -> float:
        return max(0.0, self._focus_remaining_ms) / 1000.0

    @property
    def focus_total_s(self) -> float:
        return max(0.0, self._focus_total_ms) / 1000.0

    def take_focus_finished(self) -> bool:
        """True exactly once, on the tick a session completes on its own. Drained like take_sounds."""
        finished, self._focus_finished = self._focus_finished, False
        return finished

    def _tick_focus(self, dt_ms: float) -> None:
        """Advance the session clock. Runs ahead of the HIDDEN/paused early returns in update() -
        a length you committed to is not "behaviour" to freeze, or hiding behind a fullscreen app
        (the default) would silently stall a session for as long as it stayed up. Auto-pause comes
        from gating on _away instead - the exact idle signal set_away already reads, on its own
        700ms poll, so a session left running while you are away just holds rather than lying
        about how long you actually worked.
        """
        if not self._focus_active or self._away:
            return
        self._focus_remaining_ms -= dt_ms
        if self._focus_remaining_ms <= 0:
            self._finish_focus()
            return
        # The occasional press-up break, only while he is actually holding the plain working pose
        # (not mid-burst already, and not off doing something else that will snap back on its own).
        if (
            self._state == State.ANTIC and self._antic == FOCUS_HOLD_ANIM
            and self._sheet.has_state(FOCUS_BURST_ANIM)
        ):
            self._focus_burst_due_ms -= dt_ms
            if self._focus_burst_due_ms <= 0:
                self._begin_antic(FOCUS_BURST_ANIM, FOCUS_BURST_MS, costume=FOCUS_COSTUME)
                self._focus_burst_due_ms = random.uniform(
                    FOCUS_BURST_INTERVAL_MIN_MS, FOCUS_BURST_INTERVAL_MAX_MS)

    def _finish_focus(self) -> None:
        """The countdown reached zero on its own - as opposed to set_focus(False), an early stop,
        which earns none of this."""
        self._focus_active = False
        self._focus_remaining_ms = 0.0
        self._focus_total_ms = 0.0
        self._focus_finished = True
        # Mood, sound and the celebration antic are gated together, same as the CPU-driven
        # task_finished moment: a "focus_done" chime playing over a mascot who is visibly asleep
        # or being dragged around would contradict what is on screen worse than just staying quiet.
        if (
            self._state not in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN, State.ANGRY)
            and not self._falling and not self._rolling
        ):
            self.mood.nudge("focus_done")
            self.needs.nudge("focus_done")
            # Counted inside the gate for the same reason the mood is: the session it celebrates
            # is the one he was there to see.
            self.memory.record("focus_done", self._date_key())
            # Its own sound, not "cheer" - that one is already spoken for by the CPU-driven
            # task_finished moment below, and the two completing back to back (a session ending
            # right as a build finishes) should not sound like the same event twice.
            self._sound("focus_done")
            if self._sheet.has_state(TASK_FINISHED_ANIM):
                self._begin_antic(TASK_FINISHED_ANIM, TASK_FINISHED_MS, costume="party")
                # Charged, not gated: this must never be swallowed by an unrelated CPU
                # celebration's own cooldown, but a CPU-drop landing moments later should not
                # immediately echo it.
                self._task_finished_cooldown_ms = TASK_FINISHED_COOLDOWN_MS
        log.info("focus session complete")

    def foreground_changed(self, process: str) -> None:
        """React to you switching apps. Only the executable name is ever seen."""
        if self._foreground_cooldown_ms > 0 or self._away:
            return
        if self._state not in (State.IDLE, State.WALK, State.ANTIC):
            return
        if not bool(self._config.get("antics_enabled", True)):
            return

        if process in CLAUDE_PROCESSES:
            choices = [a for a in CLAUDE_ANTICS if self._sheet.has_state(a)]
            if not choices:
                return
            self._foreground_cooldown_ms = FOREGROUND_COOLDOWN_MS
            log.debug("Claude came to the front - %s", choices)
            # Every branch here that starts an antic is something happening around him, which
            # takes the edge off boredom the way a world moment does.
            self.needs.nudge("world_event")
            self._begin_antic(random.choice(choices), random.uniform(1400, 2200))
            return

        persona = self._persona_for(process)
        if persona is not None:
            antic, costume, low, high = persona
            if self._sheet.has_state(antic):
                self._foreground_cooldown_ms = FOREGROUND_COOLDOWN_MS
                # Only dress him up if the user has not picked a hat themselves - theirs wins.
                wants_hat = costume and self._may_dress()
                log.debug("persona for %s: %s", process, antic)
                self.needs.nudge("world_event")
                self._begin_antic(antic, random.uniform(low, high), costume if wants_hat else "")
                return

        # No persona for this one, so he just reacts to the fact that something appeared. The
        # startle sits AFTER the persona checks on purpose: jumping is what he does when he has
        # nothing better to say about an app, and putting it first swallowed every persona.
        # Its own short cooldown keeps a burst of alt-tabbing to one jump rather than a twitch.
        if (
            self._startle_cooldown_ms <= 0
            and self._sheet.has_state(STARTLE_ANIM)
            and random.random() < STARTLE_CHANCE
        ):
            self._startle_cooldown_ms = STARTLE_COOLDOWN_MS
            self._foreground_cooldown_ms = FOREGROUND_COOLDOWN_MS
            self.needs.nudge("world_event")
            self._begin_antic(STARTLE_ANIM, STARTLE_MS)
            return

        if random.random() < FOREGROUND_REACT_CHANCE and self._sheet.has_state("look_around"):
            self._foreground_cooldown_ms = FOREGROUND_COOLDOWN_MS
            self.needs.nudge("world_event")
            self._begin_antic("look_around", random.uniform(1200, 1900))

    # --- world awareness ----------------------------------------------------------

    def _date_key(self) -> str:
        """Today, as the token memory files its counts under.

        The world's own key whenever there is one, so a count and the once-a-day latches agree on
        what day it is even across midnight; the wall clock only before the first world reading.
        """
        if self._world is not None:
            return self._world.date_key
        return time.strftime("%Y-%m-%d")

    def set_world(self, state: WorldState) -> None:
        """Adopt a fresh snapshot from :mod:`world`. Polled on the app's slow timer, not the tick."""
        self._world = state

    @property
    def persona_costume(self) -> str:
        """Temporary headwear the current activity wants, or "" for none.

        The app polls this and rebuilds the sprite sheet when it changes - measured at ~3ms, and
        ~18ms even if every frame's pixmap and click mask has to be rebuilt, against a 12s
        per-app cooldown. Never set while the user has chosen a costume of their own: a hat that
        keeps vanishing reads as a bug, not as a feature.
        """
        return self._persona_costume

    def clear_persona_costume(self) -> None:
        """Drop any persona headwear immediately.

        Called when the user picks a costume from the tray. Without it, choosing a hat *during* a
        persona antic put the new hat on for a single frame and then had it replaced by the
        persona's own for the rest of the antic - the exact "my hat keeps vanishing" behaviour the
        theirs-wins rule exists to prevent.
        """
        self._persona_costume = ""

    def _latch(self, key: str, token: str) -> bool:
        """True the first time ``token`` is seen for ``key``, and never again.

        Backed by the config, so "once a day" survives a restart rather than meaning "once per
        run" - otherwise quitting and relaunching would replay the day's moments.
        """
        if str(self._config.get(key, "")) == token:
            return False
        self._config[key] = token
        return True

    def _may_dress(self) -> bool:
        """May he put a costume on by himself right now?

        One rule, applied everywhere: **yours wins**. If you have chosen a costume from the tray,
        nothing here ever overrides it - not a persona, not a moment, not the season. A hat that
        keeps vanishing reads as a bug rather than as a feature, which is why this check predates
        all of them and why every new automatic costume has to route through it.
        """
        return (
            bool(self._config.get("persona_costumes", True))
            and not str(self._config.get("costume", "")).strip()
        )

    @property
    def auto_costume(self) -> str:
        """What the season says he should be wearing, or "" for nothing.

        Ambient, unlike ``persona_costume``: it is not tied to an antic and does not expire, so he
        keeps it on for as long as the season lasts. The app treats the persona one as taking
        precedence, so a code editor still swaps the beanie for reading glasses and swaps back
        afterwards.
        """
        state = self._world
        if state is None or not bool(self._config.get("auto_costumes", True)):
            return ""
        if not bool(self._config.get("world_enabled", True)):
            return ""
        if not bool(self._config.get("seasonal_enabled", True)) or not self._may_dress():
            return ""
        return SEASON_COSTUMES.get(state.season, "")

    # --- the snippet billboard -------------------------------------------------------

    @property
    def pocket_full(self) -> bool:
        return bool(self._pocket)

    @property
    def pocket_text(self) -> str:
        """What the sign currently shows, without taking it down. "" if there is nothing."""
        return "\n\n".join(self._pocket)

    def pocket_grab(self, text: str) -> bool:
        """Add a snippet to the sign - it grows, rather than replacing what was already up.

        Returns False for blank/whitespace-only text, so an empty clipboard does not silently put
        an empty sign up.
        """
        text = text.strip()
        if not text:
            return False
        self._pocket.append(text)
        log.info("pocket: %d snippet(s) on the sign, %d character(s) total",
                  len(self._pocket), len(self.pocket_text))
        # Held with both arms braced rather than idling beside it: a sign is not much of a prop if
        # he looks like he could wander off with it in one hand. 1e9 ms is "until told otherwise" -
        # the same trick set_away uses for AWAY_ANTIC - and pocket_take is what tells him otherwise.
        if self._sheet.has_state(POCKET_HOLD_ANIM):
            self._begin_antic(POCKET_HOLD_ANIM, 1e9)
        return True

    def pocket_take(self) -> str:
        """Take the sign down and return everything it showed - "" if it was already empty."""
        text, self._pocket = self.pocket_text, []
        if self._antic == POCKET_HOLD_ANIM:
            self._antic_ms = 0.0  # the tick already in flight ends the antic and returns to idle
        return text

    def pocket_empty(self) -> None:
        """You tried to pin the clipboard and there was no text on it. He has a look round.

        Not while the sign is already up: the toast says it on its own, and the braced hold is not
        worth breaking for. Nor while paused: the look round would freeze on its first frame.
        """
        self.noticed()
        if self._state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN, State.ANGRY):
            return
        if self.paused or self.pocket_full or not self._sheet.has_state(POCKET_EMPTY_ANIM):
            return
        self._begin_antic(POCKET_EMPTY_ANIM, random.uniform(*POCKET_EMPTY_MS))

    def _moment(
        self, name: str, low: float, high: float, costume: str = ""
    ) -> tuple[str, float, str] | None:
        """Package a moment, or None if this sheet does not have the animation for it.

        ``costume`` is worn for the length of THIS moment only. It travels with the moment rather
        than being looked up from the animation name, because the same animation means different
        things in different places: the knocking-off dance earns a party hat and the Friday
        afternoon dance, which is the identical animation from the ambient pool, does not.
        """
        if not name or not self._sheet.has_state(name):
            return None
        return name, random.uniform(low, high), (costume if self._may_dress() else "")

    def _pick_moment(
        self, cursor: tuple[int, int] | None = None
    ) -> tuple[str, float, str] | None:
        """A one-off reaction to something about the world, or None for "nothing to say".

        ``cursor`` is only for the fond moment, which wants your pointer near him; without it (the
        rate scripts call this directly) that one moment simply never fires.
        """
        self._moment_tail = []
        state = self._world
        if state is None or not bool(self._config.get("world_enabled", True)):
            return None
        if self._reaction_cooldown_ms > 0 or self._away:
            return None

        # First sight of you today. Gated on it being properly morning, so a machine left running
        # overnight does not get its stretch the instant the date rolls over.
        if state.hour >= NEW_DAY_HOUR and self._latch("last_seen_date", state.date_key):
            moment = self._moment("stretch", 1500, 1650)
            if moment is not None:
                log.info("first sight of you today - having a stretch")
                return moment

        end = parse_clock(self._config.get("workday_end", ""))
        if end is not None:
            if (
                not state.is_weekend
                and end <= state.hour <= end + CLOCK_OFF_WINDOW_H
                and self._latch("last_clock_off_date", state.date_key)
            ):
                moment = self._moment("dance", 2600, 3800, costume="party")
                if moment is not None:
                    log.info("knocking-off time")
                    return moment
            flop_from = end + WEEK_OVER_DELAY_H
            if (
                state.is_friday
                and flop_from <= state.hour <= flop_from + WEEK_OVER_WINDOW_H
                and self._latch("last_week_over_week", state.week_key)
            ):
                # Riding on the flop's own latch and window, so it is asked once a week at the
                # moment the week is over - not on a Monday about a week that has barely begun.
                # Nothing to see: it is only something he might say (voice.py).
                if self.memory.big_week(state.date_key):
                    log.info("a big week - more asked of him than last week")
                    self._event("big_week")
                moment = self._moment("sit_dangle", 5000, 8000)
                if moment is not None:
                    log.info("that is the week done")
                    return moment

        if (
            state.cpu_busy
            and self._hot_cooldown_ms <= 0
            and bool(self._config.get("cpu_reaction_enabled", True))
        ):
            # A hard hat while he fans himself: the machine is under load, so, work in progress.
            moment = self._moment(CPU_ANIM, 2400, 3600, costume="hardhat")
            if moment is not None:
                self._hot_cooldown_ms = HOT_COOLDOWN_MS
                log.info("machine pinned at %.0f%% - fanning himself", state.cpu_percent)
                return moment

        if (
            state.task_just_finished
            and self._task_finished_cooldown_ms <= 0
            and bool(self._config.get("task_finished_reaction_enabled", True))
        ):
            # A fixed duration, not a random range like the other moments: this is a multi-beat
            # celebration rather than a loopable mood, so playing it for anything shorter would
            # cut it off mid-gesture.
            moment = self._moment(TASK_FINISHED_ANIM, TASK_FINISHED_MS, TASK_FINISHED_MS,
                                   costume="party")
            if moment is not None:
                self._task_finished_cooldown_ms = TASK_FINISHED_COOLDOWN_MS
                log.info("sustained load just dropped - celebrating a finished task")
                self._sound("cheer")
                return moment

        if (
            state.part_of_day == PART_MORNING
            and self._morning_cooldown_ms <= 0
            and random.random() < MORNING_MOMENT_CHANCE
        ):
            moment = self._moment(random.choice(("yawn", "coffee")), 2200, 3400)
            if moment is not None:
                self._morning_cooldown_ms = MORNING_COOLDOWN_MS
                return moment

        if (
            bool(self._config.get("seasonal_enabled", True))
            # Not in the small hours: complaining about the summer heat at two in the morning is
            # not seasonal, it is just baffling. At night the mood already has him winding down.
            and state.part_of_day != PART_NIGHT
            and self._seasonal_cooldown_ms <= 0
            and random.random() < SEASONAL_MOMENT_CHANCE
        ):
            name = SEASON_ANTICS.get(state.season, "")
            # Summer's reaction and the machine-under-load one are the same fanning animation, so
            # summer stands aside while the load reaction is enabled. Otherwise "it is July" and
            # "your machine is pinned" become the same signal and neither of them reads as
            # anything. Summer then behaves like spring: a mood, not a moment.
            if name == CPU_ANIM and bool(self._config.get("cpu_reaction_enabled", True)):
                name = ""
            moment = self._moment(name, 2600, 4000)
            if moment is not None:
                self._seasonal_cooldown_ms = SEASONAL_COOLDOWN_MS
                return moment

        moment = self._needs_moment(state, cursor)
        if moment is not None:
            return moment
        return self._remembered_moment(state)

    def _needs_moment(
        self, state: WorldState, cursor: tuple[int, int] | None
    ) -> tuple[str, float, str] | None:
        """The two moments energy and affection earn, and the one line boredom may.

        * **Tired** - energy under ``TIRED_ENERGY_BELOW`` in the evening or at night: a big yawn,
          then a heavy blink (the blink waits in ``_moment_tail``).
        * **Fond** - affection over ``FOND_AFFECTION_ABOVE`` with your pointer near him: heart eyes.
        * **Bored** - boredom over the "bored" edge: an event for voice.py and nothing to see. He
          already shows boredom through the weights (checking his watch, tapping his foot); this
          only lets him mention it, and returns None so the slot goes to whatever is next.

        All three share ``NEEDS_MOMENT_COOLDOWN_MS``, so this is one beat per 90 minutes at most,
        on top of the shared moment budget. Each raises its event only once it is actually
        happening - a sheet without the art gets neither the antic nor the line about it.
        """
        needs = self.needs
        if not needs.enabled or self._needs_moment_cooldown_ms > 0:
            return None
        if needs.energy < TIRED_ENERGY_BELOW and state.part_of_day in (PART_EVENING, PART_NIGHT):
            name, ms = TIRED_SEQUENCE[0]
            moment = self._moment(name, ms, ms)
            if moment is not None:
                self._needs_moment_cooldown_ms = NEEDS_MOMENT_COOLDOWN_MS
                self._moment_tail = list(TIRED_SEQUENCE[1:])
                log.info("worn out this late - a big yawn")
                self._event("tired")
                return moment
        if needs.affection > FOND_AFFECTION_ABOVE and self._cursor_is_near(cursor):
            moment = self._moment(FOND_ANIM, *FOND_MS)
            if moment is not None:
                self._needs_moment_cooldown_ms = NEEDS_MOMENT_COOLDOWN_MS
                log.info("feeling fond - heart eyes at you")
                self._event("fond")
                return moment
        if needs.boredom > BORED_ABOVE:
            self._needs_moment_cooldown_ms = NEEDS_MOMENT_COOLDOWN_MS
            log.info("bored")
            self._event("bored")
        return None

    def _remembered_moment(self, state: WorldState) -> tuple[str, float, str] | None:
        """The two moments that come from what he remembers rather than from the clock.

        * **A day milestone** - the day he has seen you for the 7th, 30th, 100th or 365th time.
        * **A focus streak** - the third focus session finished today.

        Both get the task-finished party: the same animation, hat and cheer as a build finishing,
        because that is his one big celebration and these are the same kind of news. Last in
        ``_pick_moment``, after everything with a window or a chance: these have neither, they wait
        on a latch, so being pre-empted only delays them.

        Both are **gated on the task-finished cooldown as well as charging it**. A focus session
        already ends in that party, so the third one would otherwise get two back to back - the
        session's own and then the streak's, the moment the first finished. Waiting out the
        cooldown (three minutes at most) costs nothing, since the latch keeps the moment owed. The
        latch is only spent once the cooldown has passed, which is why it is checked last.
        """
        memory = self.memory
        if not memory.enabled or self._task_finished_cooldown_ms > 0:
            return None
        # Not in the small hours, for the same reason as the morning stretch: a machine left on at
        # the desk past midnight counts a new day at the rollover, and a party at 00:01 for it
        # would be for nobody. The latch keeps it owed until morning.
        if state.hour < NEW_DAY_HOUR:
            return None
        days = memory.days_seen
        if days in MILESTONE_DAYS and self._latch("last_milestone_days", str(days)):
            log.info("%d days together - celebrating", days)
            self._event("milestone", days=days)
            return self._celebrate()
        if (
            memory.day(state.date_key).focus_done == FOCUS_STREAK
            and self._latch("last_focus_streak_date", state.date_key)
        ):
            log.info("third focus session today - celebrating")
            self._event("focus_streak", count=FOCUS_STREAK)
            return self._celebrate()
        return None

    def _celebrate(self) -> tuple[str, float, str] | None:
        """The task-finished party as a moment: hat, cheer, and the cooldown charged."""
        moment = self._moment(TASK_FINISHED_ANIM, TASK_FINISHED_MS, TASK_FINISHED_MS,
                              costume="party")
        if moment is not None:
            self._task_finished_cooldown_ms = TASK_FINISHED_COOLDOWN_MS
            self._sound("cheer")
        return moment

    def _mood_weights(self) -> dict[str, float]:
        """Multipliers for the ambient pool.

        Six independent sources, composed **multiplicatively** rather than by replacement: the
        time of day, the season, the persistent mood, his needs, a week of rough handling (see
        _wary), and the personality preset - plus the attention arc's boost. Every one of them
        means something different, and letting any one overwrite the others would produce
        nonsense like a good mood cancelling the fact that it is two in the morning.
        """
        weights: dict[str, float] = {}
        for name, factor in self.mood.weights().items():
            weights[name] = weights.get(name, 1.0) * factor
        # Energy, affection and boredom compose the same way, and like the mood they are about him
        # rather than the clock, so they stay in even with world awareness switched off.
        for name, factor in self.needs.weights().items():
            weights[name] = weights.get(name, 1.0) * factor
        # Wary after a week of rough handling - about him too, so also in with the world off. The
        # keys are absent rather than 1.0 otherwise, so a fresh install's weights are unchanged.
        if self._wary():
            for name, factor in WARY_WEIGHTS.items():
                weights[name] = weights.get(name, 1.0) * factor
        for name, factor in self._preset().get("weights", {}).items():
            weights[name] = weights.get(name, 1.0) * float(factor)

        state = self._world
        if state is None or not bool(self._config.get("world_enabled", True)):
            return weights
        for name, factor in _PART_OF_DAY_WEIGHTS.get(state.part_of_day, {}).items():
            weights[name] = weights.get(name, 1.0) * factor
        if bool(self._config.get("seasonal_enabled", True)):
            for name, factor in _SEASON_WEIGHTS.get(state.season, {}).items():
                weights[name] = weights.get(name, 1.0) * factor
        if self._neglect_stage >= 1:
            # Stage one of being ignored: he does not do anything new, he just aims what he
            # already does at you. Nothing here is visible as an event.
            for name, _w, _lo, _hi, category in ANTICS:
                if category == "eyes":
                    weights[name] = weights.get(name, 1.0) * NEGLECT_EYES_BOOST
        end = parse_clock(self._config.get("workday_end", ""))
        if end is None:
            end = 24.0
        if state.is_friday and FRIDAY_AFTERNOON_FROM_H <= state.hour < end:
            # He can feel it coming. A mood rather than a moment, so it colours the whole
            # afternoon instead of firing once and clashing with the knocking-off dance.
            weights["dance"] = weights.get("dance", 1.0) * FRIDAY_DANCE_BOOST
        return weights

    def _wary(self) -> bool:
        """Hurt at least WARY_MIN_HURTS times in the last WARY_DAYS days, and more than petted.

        Read off the counts memory already keeps, so it mends by itself and there is nothing to
        reset: pet him as often as you hurt him, or let the hard landings age out of the week, and
        it is gone. Never with memory off - no counts, no week.
        """
        week = self.memory.recent(self._date_key(), WARY_DAYS)
        return week.hurts >= WARY_MIN_HURTS and week.hurts > week.pets

    # --- being ignored --------------------------------------------------------------

    def _neglect_step_ms(self) -> float:
        """Seconds before the first stage, or 0 when the whole thing is switched off."""
        if not bool(self._config.get("attention_enabled", True)):
            return 0.0
        return max(0.0, float(self._config.get("attention_after_s", 240))) * 1000.0

    def _neglect_suppressed(self) -> bool:
        """Times when asking for attention would be obnoxious rather than endearing."""
        if self._away or self.paused:
            return True
        if self._state in (State.SLEEP, State.WAKE, State.ANGRY, State.DRAG, State.HIDDEN,
                           State.PET, State.PERCH):
            return True
        if self._falling or self._rolling:
            return True
        # Mid-build. The worst possible moment to want to be noticed.
        return self._world is not None and self._world.cpu_busy

    def _tick_neglect(self, dt_ms: float) -> None:
        """Count how long you have been here and not looked at him, and act on it."""
        step = self._neglect_step_ms()
        if step <= 0 or self._neglect_suppressed():
            # Deliberately not decayed - stepping away for a moment should not wipe the clock,
            # but it must not run up while you are gone either, or coming back from lunch would
            # drop you straight into the last stage.
            return
        self._neglect_ms += dt_ms
        if self._neglect_glance_ms > 0:
            self._neglect_glance_ms = max(0.0, self._neglect_glance_ms - dt_ms)
            return

        # The give-up sit is held indefinitely, so _choose_next_action never runs again and the
        # glance has to be started from here. Without it he sits stock still and is
        # indistinguishable from the away-sit, which means something quite different.
        if (
            self._neglect_stage >= 3
            and self._state == State.ANTIC
            and self._antic == NEGLECT_SIT_ANTIC
            and self._sheet.has_state(NEGLECT_GLANCE_ANTIC)
        ):
            self._neglect_glance_ms = NEGLECT_GLANCE_EVERY_MS
            self._begin_antic(NEGLECT_GLANCE_ANTIC, NEGLECT_GLANCE_MS)

    def _neglect_action(self) -> bool:
        """Fire whichever stage is due. Returns True if he did something visible."""
        step = self._neglect_step_ms()
        if step <= 0 or self._neglect_suppressed():
            return False
        elapsed = self._neglect_ms

        # Stage 3: he gives up and sits down to wait. Same animation as the away-sit, so he gets a
        # glance upward every so often - that is the only thing distinguishing "still hoping" from
        # "settled in until you come back".
        if elapsed >= step * NEGLECT_GIVE_UP_AT:
            if self._neglect_stage < 3:
                log.info("ignored for %.0fs - giving up and sitting down", elapsed / 1000.0)
                # Charged once, on the transition, not on every re-seat: he returns to this sit
                # after each glance, and nudging there would grind him down all afternoon.
                self.mood.nudge("ignored")
            self._neglect_stage = 3
            if not self._sheet.has_state(NEGLECT_SIT_ANTIC):
                return False
            # Re-seated every time he lands back in idle, which is how he returns to sitting
            # after one of the glances rather than wandering off mid-sulk.
            self._neglect_glance_ms = NEGLECT_GLANCE_EVERY_MS
            self._begin_antic(NEGLECT_SIT_ANTIC, 1e9)
            return True

        # Stage 2: one wave, whether or not your pointer is anywhere near him.
        if elapsed >= step * NEGLECT_WAVE_AT and self._neglect_stage < 2:
            self._neglect_stage = 2
            log.debug("ignored for %.0fs - waving", elapsed / 1000.0)
            self._enter(State.HOVER_WAVE)
            return True

        # Stage 1: nothing visible fires; he just starts looking your way more often. The weight
        # boost lives in _mood_weights.
        if elapsed >= step * NEGLECT_GLANCE_AT and self._neglect_stage < 1:
            self._neglect_stage = 1
            log.debug("ignored for %.0fs - starting to glance over", elapsed / 1000.0)
        return False

    def _pick_blink(self) -> str:
        """Which kind of blink to play, weighted by the time of day.

        A sleepy blink is only meaningful because it is rare in the morning and common at night;
        picked uniformly it would just be a slower blink and would say nothing at all.
        """
        part = self._world.part_of_day if self._world is not None else None
        double_chance, slow_chance = BLINK_MIX_BY_PART.get(part, (0.25, 0.05))
        # Being fed up and being tired look much the same around the eyes, so a flat mood adds to
        # whatever the clock already asks for rather than getting an expression of its own.
        # Tiredness adds to it the same way: it is the most literal reason for a heavy blink.
        slow_chance = min(0.9, slow_chance + self.mood.slow_blink_bonus()
                          + self.needs.slow_blink_bonus())
        roll = random.random()
        if roll < slow_chance and self._sheet.has_state(BLINK_SLOW):
            return BLINK_SLOW
        if roll < slow_chance + double_chance and self._sheet.has_state(BLINK_DOUBLE):
            return BLINK_DOUBLE
        return BLINK_SINGLE

    def _mood_pace(self) -> float:
        """How fast the ambient animation runs: clock x persistent mood x energy x preset.

        All four multiply. They bottom out together around 0.7 * 0.9 * 0.9 * 0.85 = 0.48, which is
        slow but still unmistakably moving - the floor that matters is in ``_speed_multiplier``,
        which clamps the walk so a slow mood can never stall him mid-stride.
        """
        pace = self.mood.pace() * self.needs.pace() * self._preset_factor("pace")
        state = self._world
        if state is not None and bool(self._config.get("world_enabled", True)):
            pace *= _PACE_BY_PART.get(state.part_of_day, 1.0)
        return pace

    def _advance(self, dt_ms: float) -> None:
        """Advance the animation at the mood's pace, for the states the mood is allowed to touch."""
        if self._state in _MOOD_PACED_STATES:
            dt_ms *= self._mood_pace()
        self.animator.advance(dt_ms)

    # --- what is hanging above him ------------------------------------------------

    @property
    def wants_ledge(self) -> bool:
        """True when he is in the mood to look for something overhead.

        The app only enumerates windows while this is set. Window enumeration is the most
        expensive probe in the whole application - a Python callback per top-level window - so it
        must not sit on the geometry timer; behind a 15-minute cooldown it runs perhaps four times
        an hour, which costs nothing worth measuring.
        """
        return (
            self._ledge_cooldown_ms <= 0
            and self._reaction_cooldown_ms <= 0
            and not self._away
            and self._state in (State.IDLE, State.WALK, State.ANTIC)
            and bool(self._config.get("play_with_windows", True))
            and bool(self._config.get("antics_enabled", True))
            and self._sheet.has_state(LEDGE_ANIM)
        )

    @property
    def ledge_reach_px(self) -> int:
        """How far above his head a window's bottom edge may hang and still be worth reaching for."""
        return int(self.sprite_h * LEDGE_REACH_FRACTION)

    def set_ledge(self, ledge: tuple[int, int, int] | None) -> None:
        """Adopt ``(bottom_y, left, right)`` of a window hanging within reach, or None.

        Only ever a rectangle. He is told *where* something is and nothing about what it is, and
        he has no way to move it - the reaction is a strain that visibly fails.

        Spending a cooldown here, on the *look*, is what keeps the probe cheap. Charging it only
        when something was found meant that on a desktop with nothing overhead - the usual case -
        ``wants_ledge`` never latched off and the app re-enumerated every top-level window on the
        next poll, and the one after that, indefinitely.
        """
        self._ledge = ledge
        self._ledge_cooldown_ms = max(self._ledge_cooldown_ms, LEDGE_LOOK_AGAIN_MS)

    def _try_ledge(self) -> bool:
        """Walk under an overhead window and heave at it. Returns True if he set off."""
        ledge = self._ledge
        self._ledge = None
        if ledge is None or not self._sheet.has_state(LEDGE_ANIM):
            return False
        _bottom, left, right = ledge
        # Stand under the middle of its edge, but not past the ends of his own strip.
        centre = (left + right) / 2.0 - self.sprite_w / 2.0
        target = min(max(centre, self.x_min), self.x_max)
        self._ledge_cooldown_ms = LEDGE_COOLDOWN_MS
        log.info("something is hanging overhead - going to have a go at it")
        if abs(target - self._x) < 4:
            self._begin_antic(LEDGE_ANIM, random.uniform(LEDGE_MIN_MS, LEDGE_MAX_MS))
            return True
        self._pending_action = LEDGE_ANIM
        self._begin_walk(target)
        return self._state == State.WALK

    def _persona_for(self, process: str) -> tuple[str, str, float, float] | None:
        """The persona for ``process``: (animation, headwear, min ms, max ms)."""
        custom = self._config.get("persona_apps") or {}
        if isinstance(custom, dict) and process in custom:
            # A user-supplied mapping is animation-only; it wins over the built-in table.
            return str(custom[process]), "", *CUSTOM_PERSONA_MS
        return PERSONAS.get(process)

    def _eye_track_distance(self) -> float:
        return float(self._config.get("eye_track_distance_px", 420))

    def _track_frame_index(self, cursor: tuple[int, int]) -> int:
        """Pick the 3x3 eye-direction frame that points at the cursor."""
        cx, cy = cursor
        centre_x = self._x + self.sprite_w / 2
        centre_y = self.top + self.sprite_h / 2
        dead = max(10.0, self.sprite_w * 0.22)
        col = 1 + (1 if cx > centre_x + dead else 0) - (1 if cx < centre_x - dead else 0)
        row = 1 + (1 if cy > centre_y + dead else 0) - (1 if cy < centre_y - dead else 0)
        return row * 3 + col

    def _choose_next_action(self, cursor: tuple[int, int] | None) -> None:
        """Decide what he does next. He does *something* - standing about is not an option."""
        self._idle_timer_ms = self._next_wander_delay()

        # Above everything else, including the pocket: a live perch rectangle means either an
        # antic (a sneeze, a wave, asking him something) has left State.PERCH behind for as long
        # as it ran, or - if _tick_perch's own never-calls-this-function guarantee is ever
        # violated some other way - he is already there. Either way this must return, unconditional
        # on the current state: falling through to the wander gate at the bottom is exactly the
        # bug this exists to prevent, and checking only "!= State.PERCH" let it happen the one time
        # this got called while already perched.
        if self._perch_rect is not None:
            if self._state != State.PERCH:
                self._enter(State.PERCH)
                self._reposition_perch()
            return

        # Holding the sign overrides everything else here, not just the wander at the bottom: a
        # pet, a persona change, or anything else that knocks him out of the brace has to land back
        # in it, not in an ordinary idle antic, or "gripping it hard" only lasts until the first
        # interruption. pocket_grab already does this once for the initial reaction; this is what
        # makes it hold no matter what else asks for his attention in between.
        if self.pocket_full and self._sheet.has_state(POCKET_HOLD_ANIM):
            self._begin_antic(POCKET_HOLD_ANIM, 1e9)
            return

        # Reasserted here for the same reason as the pocket above: set_away already seizes control
        # directly the moment you go idle, but nothing previously reasserted it if something else
        # (a manual costume change, a pet) interrupted it afterward - so an interrupted away could
        # fall through to an ordinary antic while the tray still (correctly) called him "away",
        # the one disagreement between his posture and the tray this feature cannot afford to
        # introduce. Above the focus check: a session never overrides a genuine away, because
        # posture and tooltip must never say two different things about whether you are here.
        if self._away and self._sheet.has_state(AWAY_ANTIC):
            self._begin_antic(AWAY_ANTIC, 1e9)
            return

        if self.focus_active and self._sheet.has_state(FOCUS_HOLD_ANIM):
            self._begin_antic(FOCUS_HOLD_ANIM, 1e9, costume=FOCUS_COSTUME)
            return

        antics_on = bool(self._config.get("antics_enabled", True))

        # Involuntary things go first. They are not something he weighs up against having a dance,
        # and they are not gated on antics_enabled either: switching off his hobbies should not
        # stop him sneezing.
        if self._maybe_sneeze() or self._maybe_hiccup():
            return

        # Noticing your pointer wins: it is the only reaction that is actually about you.
        if (
            antics_on
            and self._eye_track_cooldown_ms <= 0
            and self._sheet.has_state(EYE_TRACK_STATE)
            and self._cursor_is_near(cursor, self._eye_track_distance())
        ):
            self._eye_track_cooldown_ms = EYE_TRACK_COOLDOWN_MS
            self._begin_antic(EYE_TRACK_STATE, random.uniform(EYE_TRACK_MIN_MS, EYE_TRACK_MAX_MS))
            return

        # Then anything the world has to say - the clock, the calendar, the machine's load. These
        # are events rather than pool members, so they get their own check rather than a weight.
        if antics_on:
            moment = self._pick_moment(cursor)
            if moment is not None:
                # Something happened, so he is a little less bored.
                self.needs.nudge("world_event")
                self._reaction_cooldown_ms = REACTION_COOLDOWN_MS
                self._begin_antic(*moment)
                # After the call, which clears the queue. Steps without art are skipped, the same
                # as _begin_sequence does.
                self._antic_queue = [step for step in self._moment_tail
                                     if self._sheet.has_state(step[0])]
                self._moment_tail = []
                return

        # Being ignored. After the world's moments, before the ambient pool: it is about you,
        # so it should not pre-empt the clock, but it should beat him amusing himself.
        if antics_on and self._neglect_action():
            return

        # Playing with what is around him. Both of these are read-only about other windows.
        if antics_on and self._reaction_cooldown_ms <= 0 and not self._away:
            if self._ledge is not None and self._try_ledge():
                self._reaction_cooldown_ms = REACTION_COOLDOWN_MS
                return
            if (
                self._peek_cooldown_ms <= 0
                and bool(self._config.get("play_with_windows", True))
                # A bored mascot goes looking - up to 1.6x as likely to peek. Exactly 1.0 unless
                # he is properly bored, so the roll is the same as ever.
                and random.random() < PEEK_CHANCE * self.needs.peek_boost()
            ):
                self._reaction_cooldown_ms = REACTION_COOLDOWN_MS
                log.debug("ducking behind the taskbar for a peek")
                self._enter(State.PEEK)  # spends the peek cooldown itself
                return

        chance = min(1.0, float(self._config.get("antic_chance", 0.5))
                     * self._preset_factor("antic_chance"))
        if antics_on and random.random() < chance:
            chosen = self._pick_antic(cursor)
            if chosen is not None:
                self._begin_antic(*chosen)
                return

        # Holding the sign up, or a focus session: he finishes whatever he was already doing but
        # does not wander off to a new spot, or the whole point of either is defeated. (A genuine
        # away already returned above, via its own reassertion, before this line is ever reached.)
        if self._config["wander_enabled"] and not self.pocket_full and not self.focus_active:
            self._begin_walk(self._pick_wander_target())

    def _pick_wander_target(self) -> float:
        low = float(self._config.get("walk_min_px", 120))
        high = float(self._config.get("walk_max_px", 700))
        if high < low:
            low, high = high, low
        distance = random.uniform(low, high)
        going_right = self._facing == "right"
        if random.random() < TURN_AROUND_CHANCE:
            going_right = not going_right
        target = self._x + distance if going_right else self._x - distance
        if target > self.x_max or target < self.x_min:  # bounce off the end of the strip
            target = self._x - distance if going_right else self._x + distance
        return min(max(target, self.x_min), self.x_max)

    def _speed_multiplier(self) -> float:
        span = abs(self._walk_target - self._walk_from)
        if span < 1:
            return 1.0
        progress = abs(self._x - self._walk_from) / span
        if progress < EASE_FRACTION:
            ramp = _smoothstep(progress / EASE_FRACTION)
        elif progress > 1.0 - EASE_FRACTION:
            ramp = _smoothstep((1.0 - progress) / EASE_FRACTION)
        else:
            ramp = 1.0
        multiplier = MIN_SPEED_MULTIPLIER + (1.0 - MIN_SPEED_MULTIPLIER) * ramp

        override = float(self._config["walk_speed_px_s"])
        if override > 0:
            natural = self._natural_speed_px_s()
            if natural > 0:
                multiplier *= override / natural
        # Applied last, and to the walk as a whole rather than to the animation alone: _walk
        # derives distance from the animation's own progress, so scaling both together slows him
        # down without letting his feet start sliding.
        return max(0.05, multiplier * self._mood_pace())

    def _natural_speed_px_s(self) -> float:
        frame_ms = max(1, self.animator.frame_duration)
        return self._sheet.walk_step_px * self._sheet.scale * 1000.0 / frame_ms

    def _walk(self, dt_ms: float) -> None:
        multiplier = self._speed_multiplier()
        scaled = dt_ms * multiplier
        frame_ms = max(1, self.animator.frame_duration)
        step_px = self._sheet.walk_step_px * self._sheet.scale
        # Distance comes from the animation's own progress, so stride length is exact.
        distance = step_px * (scaled / frame_ms)

        if self._facing == "left":
            distance = -distance
        self._x += distance
        self.animator.advance(scaled)

        reached = (
            self._x >= self._walk_target if self._facing == "right" else self._x <= self._walk_target
        )
        at_edge = self._x <= self.x_min or self._x >= self.x_max
        self._x = min(max(self._x, self.x_min), self.x_max)

        if reached or at_edge:
            if self._pending_sleep:
                self._pending_sleep = False
                self._enter(State.SLEEP)
            elif self._pending_action is not None:
                # He walked over here to do something. Play it even if he only got as far as the
                # end of the strip - giving up silently on arrival would read as him forgetting.
                action, self._pending_action = self._pending_action, None
                self._begin_antic(action, random.uniform(LEDGE_MIN_MS, LEDGE_MAX_MS))
            elif at_edge and not reached:
                self._facing = "left" if self._facing == "right" else "right"
                self._begin_walk(self._pick_wander_target())
            else:
                self._enter(State.IDLE)

    # --- the tick -----------------------------------------------------------------

    def update(self, dt_ms: float, cursor: tuple[int, int] | None = None) -> None:
        """Advance the mascot by ``dt_ms``. ``cursor`` is the global pointer position."""
        if dt_ms <= 0:
            return
        dt_ms = min(float(dt_ms), 250.0)  # a long stall must not teleport him
        self._clock_ms += dt_ms
        # Ahead of the HIDDEN/pause returns below on purpose - see _tick_focus's own docstring.
        self._tick_focus(dt_ms)

        if self._state == State.HIDDEN:
            return

        # Before the pause check: the stillness clock and the pet re-arm both have to keep running
        # while he is frozen, or unpausing with the pointer already on him fires an instant pet
        # that nobody asked for.
        self._track_cursor(dt_ms, cursor)

        # Settle a drop even while paused. Pausing is meant to freeze *behaviour*, and leaving him
        # hanging in mid-air until the user unpauses is a bug, not a feature.
        if self._falling or self._rolling:
            self._apply_flight(dt_ms)
        # Runs on this side of the pause check for the same reason: a landing *during* a pause
        # sets the squash, and the cooldown loop below never runs to clear it - so the shadow
        # would stay spread until whenever the user happened to unpause.
        if self._land_squash_ms > 0:
            self._land_squash_ms = max(0.0, self._land_squash_ms - dt_ms)

        if self.paused:
            return

        self._state_ms += dt_ms
        self._decay_annoyance(dt_ms)
        self.mood.tick(dt_ms)
        # Behind the same HIDDEN and pause returns as the mood, and for the same reason: frozen is
        # frozen. A restart is aged on the wall clock instead - see Needs._load.
        world = self._world
        # Boredom builds only while the being-ignored clock is actually running. Checked here
        # rather than read off _neglect_ms alone: on hold (asleep, cross, held, petted, perched,
        # mid-build, the arc switched off) that clock is frozen at whatever it had reached, which
        # would otherwise keep boredom climbing all the way through a pinned build or a sleep.
        neglect_running = self._neglect_step_ms() > 0 and not self._neglect_suppressed()
        part_of_day = (world.part_of_day if world is not None
                       and bool(self._config.get("world_enabled", True)) else None)
        self.needs.tick(
            dt_ms,
            away=self._away,
            part_of_day=part_of_day,
            present_interacting=(not neglect_running
                                 or self._neglect_ms < INTERACTING_WITHIN_MS),
        )
        # Straight after the needs, so it reads this tick's energy. Night as the needs see it:
        # with the world switched off there is no night, and so no nap.
        self._maybe_nap(part_of_day)
        self._tick_quirks(dt_ms)
        self._tick_neglect(dt_ms)
        if self._grumble_ms > 0:
            self._grumble_ms = max(0.0, self._grumble_ms - dt_ms)
        if self._wave_cooldown_ms > 0:
            self._wave_cooldown_ms = max(0.0, self._wave_cooldown_ms - dt_ms)
        if self._eye_track_cooldown_ms > 0:
            self._eye_track_cooldown_ms = max(0.0, self._eye_track_cooldown_ms - dt_ms)
        if self._foreground_cooldown_ms > 0:
            self._foreground_cooldown_ms = max(0.0, self._foreground_cooldown_ms - dt_ms)
        for attr in (
            "_reaction_cooldown_ms",
            "_hot_cooldown_ms",
            "_task_finished_cooldown_ms",
            "_morning_cooldown_ms",
            "_seasonal_cooldown_ms",
            "_needs_moment_cooldown_ms",
            "_peek_cooldown_ms",
            "_ledge_cooldown_ms",
            "_startle_cooldown_ms",
            "_pet_cooldown_ms",
            "_spin_cooldown_ms",
        ):
            remaining = getattr(self, attr)
            if remaining > 0:
                setattr(self, attr, max(0.0, remaining - dt_ms))

        # Ahead of every state handler, because it has to beat _maybe_wave - which fires at 120px
        # and would otherwise consume every pointer that came to rest on him. Only from the states
        # where stopping to be petted makes sense: not asleep, mid-tantrum or mid-throw.
        if self._state in (State.IDLE, State.WALK, State.ANTIC) and self._maybe_pet(cursor):
            self._sync_animation()
            return

        handler = {
            State.IDLE: self._tick_idle,
            State.WALK: self._tick_walk,
            State.INJURED: self._tick_injured,
            State.ANTIC: self._tick_antic,
            State.HOVER_WAVE: self._tick_wave,
            State.ASK: self._tick_ask,
            State.WORKING: self._tick_working,
            State.ANGRY: self._tick_angry,
            State.SLEEP: self._tick_sleep,
            State.WAKE: self._tick_wake,
            State.PEEK: self._tick_peek,
            State.PET: self._tick_pet,
            State.DRAG: self._tick_drag,
            State.PERCH: self._tick_perch,
        }[self._state]
        handler(dt_ms, cursor)
        self._sync_animation()

    def next_tick_ms(self, floor_ms: float, ceiling_ms: float) -> float:
        """How long the app may sleep before anything visible changes.

        A fixed 33fps tick spends most of its budget recomputing state nobody can see: an idle
        breathing frame lasts 450ms. Anything that *moves* still needs the full rate, so this
        returns the floor while he is in motion and otherwise the time until the next frame flip or
        pending timer, whichever comes first.
        """
        if self._state == State.DRAG:
            # Full rate while he is actually in your hand. The throw velocity is sampled from
            # these ticks, and at 50ms a flick is only three samples to average over.
            return floor_ms
        if self._state in (State.WALK, State.PEEK) or self._falling or self._rolling:
            # Moving still needs regular updates, but not 33 a second: he travels ~54px/s and the
            # art is made of 2px blocks, so ~20fps is already below the visible step size. Frames
            # stay punctual because the next frame flip caps the interval.
            return max(floor_ms, min(MOVE_TICK_MS, self.animator.remaining_ms))
        if self._state == State.ANTIC and self._antic == EYE_TRACK_STATE:
            return max(floor_ms, min(MOVE_TICK_MS, self.animator.remaining_ms))

        candidates = [self.animator.remaining_ms]
        if self._state == State.IDLE:
            candidates.append(self._idle_timer_ms)
            if not self._blinking:
                candidates.append(self._blink_timer_ms)
        elif self._state == State.ANTIC:
            if self._antic_overrun_ms is not None:
                candidates.append(max(0.0, self._antic_overrun_ms))
            else:
                candidates.append(max(0.0, self._antic_ms))
        elif self._state == State.ASK and not self._blinking and self._wheel_look is None:
            candidates.append(self._blink_timer_ms)
        elif self._state == State.SLEEP:
            candidates.append(self._sleep_ms)
        elif self._state == State.INJURED:
            candidates.append(self._injured_ms)
        elif self._state == State.WORKING:
            candidates.append(self._working_ms - self._state_ms)
        elif self._state == State.HOVER_WAVE:
            candidates.append(WAVE_DURATION_MS - self._state_ms)
        elif self._state == State.ANGRY:
            candidates.append(ANGRY_DURATION_MS - self._state_ms)
        elif self._state == State.PET:
            # It ends when your hand leaves, which this clock cannot see - so it has to keep
            # sampling the cursor rather than sleeping until the next frame flip.
            return max(floor_ms, min(MOVE_TICK_MS, self.animator.remaining_ms))
        if self._grumble_ms > 0:
            candidates.append(self._grumble_ms)

        soonest = min(c for c in candidates if c is not None)
        return max(floor_ms, min(ceiling_ms, soonest))

    def _sample_drag(self, dt_ms: float) -> None:
        """Remember how fast he has been moving over the tail of this drag."""
        if self._drag_last is not None:
            self._drag_samples.append(
                (dt_ms, self._x - self._drag_last[0], self._y - self._drag_last[1])
            )
            span = sum(s[0] for s in self._drag_samples)
            while len(self._drag_samples) > 1 and span - self._drag_samples[0][0] >= THROW_SAMPLE_MS:
                span -= self._drag_samples.popleft()[0]
        self._drag_last = (self._x, self._y)

    def _throw_velocity(self) -> tuple[float, float]:
        """Average velocity over the tail of the drag, in px/ms, capped."""
        span = sum(s[0] for s in self._drag_samples)
        if span <= 0:
            return 0.0, 0.0
        vx = sum(s[1] for s in self._drag_samples) / span
        vy = sum(s[2] for s in self._drag_samples) / span
        speed = math.hypot(vx, vy)
        if speed > MAX_THROW_SPEED_PX_MS:
            scale = MAX_THROW_SPEED_PX_MS / speed
            vx, vy = vx * scale, vy * scale
        return vx, vy

    def _apply_flight(self, dt_ms: float) -> None:
        """Carry a throw through the air, off the ends of the strip, and along the ground."""
        if self._falling:
            self._vy = min(MAX_FALL_SPEED_PX_MS, self._vy + GRAVITY_PX_PER_MS2 * dt_ms)
            self._vx *= AIR_DRAG_PER_MS**dt_ms
            self._y += self._vy * dt_ms
        else:
            self._vx *= ROLL_FRICTION_PER_MS**dt_ms

        self._x += self._vx * dt_ms
        self._reflect_off_ends()

        if self._falling:
            resting = self.rest_top()
            if self._y >= resting:
                self._y = float(resting)
                self._touch_down(resting)
        elif abs(self._vx) < MIN_ROLL_SPEED_PX_MS:
            self._rolling = False
            self._settle()

    def _reflect_off_ends(self) -> None:
        """Bounce off the ends of the strip, losing energy.

        Reflecting *here*, rather than letting the position get clamped later, is load-bearing:
        ``set_strip`` clamps ``_x`` into bounds on every 700ms geometry poll, so an out-of-range
        position with the velocity still pointing outward would pin him to the edge and jitter
        there until the throw finished.
        """
        hit = 0.0
        if self._x < self.x_min:
            self._x = self.x_min + (self.x_min - self._x)
            hit, self._vx = abs(self._vx), abs(self._vx) * WALL_RESTITUTION
        elif self._x > self.x_max:
            self._x = self.x_max - (self._x - self.x_max)
            hit, self._vx = abs(self._vx), -abs(self._vx) * WALL_RESTITUTION
        # A single huge step could reflect straight past the other end; the clamp is the backstop.
        self._x = min(max(self._x, self.x_min), self.x_max)
        # Not while a sleep is pending: being moved mid-sleep never hurts him, and a wall is no
        # different from the floor in that respect.
        if (
            hit >= WALL_IMPACT_HURT_PX_MS
            and self._resume_sleep_ms <= 0
            and not self._pending_injury
            and self._would_injure(0.0, impact_px_ms=hit)
        ):
            log.info("hit the end of the strip at %.2f px/ms - ouch", hit)
            self._pending_injury = True
            self._vx = 0.0  # he crumples and drops rather than bouncing away

    def _touch_down(self, resting: float) -> None:
        """He has reached the strip. Bounce, roll, or stop.

        Injury is *judged* here, at the impact - that is what hurts him, and ``INJURY_FALL_PX``
        measures the drop that caused it - but it is *shown* once he stops moving. Entering the
        state at touchdown froze him mid-skid: any throw from a real height hurts, so the roll
        this feature exists for never appeared at all. Skidding to a halt and then sitting up
        dazed is both better looking and better physics.
        """
        impact = self._vy
        fallen = resting - self._fall_from_y
        self._falling = False
        self._vy = 0.0
        # Every touchdown, including each bounce - so a ball-like landing gets a shadow that
        # spreads on each contact rather than only on the first.
        if impact > 0.05:
            self._land_squash_ms = LAND_SQUASH_MS
            if impact >= HICCUP_IMPACT_PX_MS and self._resume_sleep_ms <= 0:
                self.start_hiccups()
            # Only a real landing, not the tail of a bounce settling out. The sound layer has its
            # own repeat guard as well, since he can touch down three times in half a second.
            if impact >= MIN_BOUNCE_SPEED_PX_MS * 0.5:
                self._sound("land")

        if self._resume_sleep_ms <= 0 and self._would_injure(fallen):
            log.info("dropped %.0fpx - ouch", fallen)
            self._pending_injury = True

        if impact >= MIN_BOUNCE_SPEED_PX_MS:
            self._vy = -impact * BOUNCE_RESTITUTION
            self._falling = True
            self._fall_from_y = self._y  # the next landing measures the bounce, not the throw
            return
        if abs(self._vx) >= MIN_ROLL_SPEED_PX_MS:
            self._rolling = True
            return
        self._settle()

    def _settle(self) -> None:
        """Everything has stopped moving: go back to sleep, sit up hurt, or answer how he landed."""
        self._vx = self._vy = 0.0
        self._rolling = False
        # Read and cleared first, so no branch below can leave one set for the next landing.
        gentle, thrown = self._gentle_pending, self._throw_pending
        self._gentle_pending = self._throw_pending = False
        if self._resume_sleep_ms > 0:
            # He was thrown asleep: now that he has stopped, back to sleep. A sulk serves out the
            # rest of its cooldown; a nap lands back in the nap, whose flag rode the throw and
            # whose _tick_sleep never reads the clock set here.
            remaining = self._resume_sleep_ms
            self._resume_sleep_ms = 0.0
            self._pending_injury = False
            self._enter(State.SLEEP)
            self._sleep_ms = remaining
            return
        if self._pending_injury:
            self._pending_injury = False
            self.mood.nudge("hurt")
            self.needs.nudge("hurt")
            self.memory.record("hurts", self._date_key())
            self._injured_ms = float(self._config.get("injury_duration_s", 3.0)) * 1000.0
            self._enter(State.INJURED)
            return
        self._answer_landing(gentle=gentle, thrown=thrown)

    def _answer_landing(self, *, gentle: bool, thrown: bool) -> None:
        """Set down gently: a sparkle and a little affection. Thrown, and fond of you: "again!".

        Only from IDLE, where drag_end left him: a click that opened the bubble mid-flight is not
        overridden by it. Never while paused - _settle runs on the near side of the pause check so
        that a drop still lands, but an antic started there would sit frozen on its first frame.
        A sleep resumed and an injury have already returned before this; a perch never gets here.
        """
        if self.paused or self._state != State.IDLE:
            return
        if gentle:
            if self._clock_ms - self._gentle_at_ms < GENTLE_COOLDOWN_MS:
                return
            self._gentle_at_ms = self._clock_ms
            log.debug("set down gently")
            # A fraction of a pet to affection, and not a pet in memory: the count says pets.
            self.needs.nudge("petted", GENTLE_PET_SCALE)
            if self._sheet.has_state(GENTLE_ANIM):
                self._begin_antic(GENTLE_ANIM, GENTLE_MS)
        elif (thrown and self.needs.affection > PLAY_AFFECTION_ABOVE
              and self._sheet.has_state(PLAY_ANIM)):
            log.debug("thrown, and up for another")
            self._begin_antic(PLAY_ANIM, PLAY_MS)

    def _would_injure(self, fallen_px: float, *, impact_px_ms: float = 0.0) -> bool:
        """Was that hard enough to hurt? A long drop, or a hard sideways impact."""
        if not bool(self._config.get("injury_enabled", True)):
            return False
        if fallen_px < INJURY_FALL_PX and impact_px_ms < WALL_IMPACT_HURT_PX_MS:
            return False
        if not self._sheet.has_state(INJURY_ANIM):
            return False
        return float(self._config.get("injury_duration_s", 3.0)) > 0

    def _tick_injured(self, dt_ms: float, cursor) -> None:
        self._advance(dt_ms)
        self._injured_ms -= dt_ms
        if self._injured_ms <= 0:
            self._enter(State.IDLE)

    def _cursor_is_near(
        self, cursor: tuple[int, int] | None, threshold: float | None = None
    ) -> bool:
        if cursor is None:
            return False
        if threshold is None:
            threshold = float(self._config["hover_wave_distance_px"])
        if threshold <= 0:
            return False
        cx, cy = cursor
        centre_x = self._x + self.sprite_w / 2
        centre_y = self.top + self.sprite_h / 2
        return abs(cx - centre_x) <= threshold and abs(cy - centre_y) <= threshold

    def _pet_pending(self, cursor: tuple[int, int] | None) -> bool:
        """A pet is on its way: the pointer is on him and only the hold time is outstanding."""
        return (
            bool(self._config.get("petting_enabled", True))
            and self._pet_armed
            and self._pet_cooldown_ms <= 0
            and not self._away
            and not (self._falling or self._rolling)
            and self._cursor_is_over(cursor)
        )

    def _maybe_wave(self, cursor: tuple[int, int] | None) -> bool:
        if self._wave_cooldown_ms > 0 or self._falling or self._rolling or self._away:
            return False
        # Stand aside for a pet. Checking petting first in ``update`` is not enough on its own:
        # the wave fires the instant a pointer comes within 120px, and a pet needs half a second
        # of stillness first - so the wave always got there before the hold could complete, and
        # petting was then unreachable from inside the 2.1s wave. Deferring is also the truer
        # reading: a hand resting ON him is not a hello from across the desk.
        if self._pet_pending(cursor):
            return False
        if not self._cursor_is_near(cursor):
            return False
        self.noticed()  # your pointer came to him; that counts
        self._enter(State.HOVER_WAVE)
        return True

    def _tick_ask(self, dt_ms: float, cursor) -> None:
        """Held for the bubble or the wheel: blinks as he would standing about, and while a wheel
        tool is aimed at, his eyes stay on it instead."""
        if self._wheel_look is not None and self._sheet.has_state(EYE_TRACK_STATE):
            self.animator.hold(EYE_TRACK_STATE, self._track_frame_index(self._wheel_look))
            return
        self._advance(dt_ms)
        self._tick_blink(dt_ms)

    def _struggle_amplitude_px(self) -> float:
        """How hard he squirms in your hand, in screen px: less, the better he knows you.

        Exactly ``STRUGGLE_AMPLITUDE_PX`` times the sprite scale at a bond of 0. Read on every drag
        tick, like the scale already was - the bond is four logarithms, and a drag lasts seconds.
        """
        calm = 1.0 - SQUIRM_BOND_CALM * self.memory.bond()
        return STRUGGLE_AMPLITUDE_PX * calm * self._sheet.scale

    def _tick_drag(self, dt_ms: float, cursor) -> None:
        """Squirm while held: he is trying to kick free and get back down."""
        self._struggle_ms += dt_ms
        t = self._struggle_ms
        amplitude = self._struggle_amplitude_px()
        # Amplitude-modulated on x so the wriggling comes in bursts, and a slower, independent
        # rate on y, which keeps the motion from collapsing into a straight diagonal line.
        self._struggle_dx = amplitude * math.sin(t * STRUGGLE_X_RATE) * math.cos(
            t * STRUGGLE_X_BURST_RATE
        )
        self._struggle_dy = amplitude * 0.75 * math.sin(t * STRUGGLE_Y_RATE + 1.1)
        self._sample_drag(dt_ms)
        self._advance(dt_ms)

    def _tick_blink(self, dt_ms: float) -> bool:
        """The blink clock IDLE, PERCH and ASK share. True while a blink owns the frame - playing,
        just started, or just finished this tick - so the caller skips the rest of its tick."""
        if self._blinking:
            if self.animator.finished:
                self._blinking = False
                self.animator.set_state("idle", restart=False)
            return True
        self._blink_timer_ms -= dt_ms
        if self._blink_timer_ms <= 0:
            self._blink_timer_ms = random.uniform(BLINK_MIN_MS, BLINK_MAX_MS)
            self._blinking = True
            self._blink_state = self._pick_blink()
            self.animator.set_state(self._blink_state, restart=True)
            return True
        return False

    def _tick_idle(self, dt_ms: float, cursor) -> None:
        if self._maybe_wave(cursor):
            return
        self._advance(dt_ms)
        if self._tick_blink(dt_ms):
            return

        if self._falling or self._rolling:
            return
        self._idle_timer_ms -= dt_ms
        if self._idle_timer_ms <= 0:
            self._choose_next_action(cursor)

    def _tick_perch(self, dt_ms: float, cursor) -> None:
        """Standing on a window. update_perch (driven from main.py's own poll of the window's
        rectangle) is the only thing that repositions or releases him - all that happens on this
        tick is the ordinary blink clock (_tick_blink), so he still reads as alive rather than a
        sprite pasted onto someone else's title bar. No wander clock here at all: leaving is never
        a timeout.
        """
        self._advance(dt_ms)
        self._tick_blink(dt_ms)

    def _tick_antic(self, dt_ms: float, cursor) -> None:
        if self._quick_change is not None:
            self._tick_quick_change(dt_ms)
            return
        shown = self.animator.frame_index  # what is on screen until this tick paints
        if self._antic == EYE_TRACK_STATE:
            if self._forced_look is not None:
                # He was asked to look at something specific - where you dropped a file, say - so
                # for this one antic the pointer does not get a vote, and he does not give up early
                # just because you moved the mouse away afterwards.
                self.animator.hold(EYE_TRACK_STATE, self._track_frame_index(self._forced_look))
            else:
                # The frame is chosen by where your pointer is, not by a clock.
                if cursor is not None:
                    self.animator.hold(EYE_TRACK_STATE, self._track_frame_index(cursor))
                # He gives up early if you take the pointer away entirely.
                if not self._cursor_is_near(cursor, self._eye_track_distance() * 1.4):
                    self._antic_ms = min(self._antic_ms, 250.0)
        else:
            self._advance(dt_ms)

        self._antic_ms -= dt_ms
        if self._antic_ms > 0 or not self._antic_settled(dt_ms, shown):
            return
        self._antic = None
        self._forced_look = None
        self._antic_overrun_ms = None
        if self._antic_queue:
            (name, ms), rest = self._antic_queue[0], self._antic_queue[1:]
            self._begin_antic(name, ms)
            self._antic_queue = rest
            return
        self._enter(State.IDLE)

    def _tick_quick_change(self, dt_ms: float) -> None:
        """Frame-driven, not timed: ANTIC is mood-paced, so a wall-clock end would land mid-air.

        The swap is raised on the tick the hop reaches its apex, so main.py rebuilds before that
        frame is painted. The hop ends when it wraps back round past the apex - the landing has
        played by then - and his opinion follows. QUICK_CHANGE_MAX_MS is only the safety net.
        """
        self._advance(dt_ms)
        self._antic_ms -= dt_ms
        index = self.animator.frame_index
        if not self._quick_change_swapped and (index >= QUICK_CHANGE_SWAP_FRAME
                                               or self._antic_ms <= 0):
            self._quick_change_swapped = True
            self._costume_swap_due = True
        if self._quick_change_swapped and (index < QUICK_CHANGE_SWAP_FRAME or self._antic_ms <= 0):
            costume, self._quick_change = self._quick_change, None
            self._antic = None
            # costume_chosen -> _begin_antic -> _flush_quick_change is a no-op: already None.
            if not self.costume_chosen(costume):
                self._enter(State.IDLE)

    def _tick_walk(self, dt_ms: float, cursor) -> None:
        if not self._pending_sleep and self._maybe_wave(cursor):
            return
        # Only mid-stride, and never while storming off - a tantrum undercut by a pratfall reads
        # as a bug rather than as comedy.
        if not self._pending_sleep and self._maybe_trip():
            return
        self._walk(dt_ms)

    def _tick_wave(self, dt_ms: float, cursor) -> None:
        self._advance(dt_ms)
        if self._state_ms >= WAVE_DURATION_MS or not self._cursor_is_near(cursor):
            self._wave_cooldown_ms = WAVE_COOLDOWN_MS
            self._enter(State.IDLE)

    def _tick_working(self, dt_ms: float, cursor) -> None:
        self._advance(dt_ms)
        if self._state_ms < self._working_ms:
            return
        # He has finished thinking about it. If the prompt had a shape worth commenting on, this
        # is where it lands - after the working beat, so the sequence reads as "on it... oh, THAT
        # kind of job" rather than as a reaction that arrives before he has looked at it.
        reaction, self._post_prompt = self._post_prompt, None
        if reaction is not None:
            self._begin_antic(*reaction)
            return
        self._enter(State.IDLE)

    def _tick_angry(self, dt_ms: float, cursor) -> None:
        self._advance(dt_ms)
        if self._state_ms >= ANGRY_DURATION_MS:
            if self._pending_sleep:
                self._storm_off(cursor)
            else:
                self._enter(State.IDLE)

    def _tick_peek(self, dt_ms: float, cursor) -> None:
        """Sink, look about over the edge, rise again."""
        if self._sheet.has_state(EYE_TRACK_STATE):
            self.animator.hold(EYE_TRACK_STATE, PEEK_LOOK_FRAME)
        else:
            self._advance(dt_ms)
        if self._state_ms >= PEEK_DOWN_MS + PEEK_HOLD_MS + PEEK_UP_MS:
            self._enter(State.IDLE)

    def _tick_sleep(self, dt_ms: float, cursor) -> None:
        self._advance(dt_ms)
        if self._napping:
            # No clock and no hover-wake: a nap ends when you come back, or click him. This is the
            # one place that sees a nap you came back to while he was in your hand or in the air -
            # set_away could not wake him there, and _settle has just put him back down in it.
            if not self._away:
                self._wake_from_nap()
            return
        self._sleep_ms -= dt_ms
        if self._sleep_ms <= 0:
            self._enter(State.WAKE)
            return
        # A gentle hover near the end of the sulk - and no recent poking - wakes him early.
        total = max(1.0, float(self._config["sleep_cooldown_s"]) * 1000.0)
        quiet = self._clock_ms - self._last_click_ms > 3000
        if self._sleep_ms < total * 0.25 and quiet and self._cursor_is_near(cursor):
            self._enter(State.WAKE)

    def _tick_wake(self, dt_ms: float, cursor) -> None:
        self._advance(dt_ms)
        if self.animator.finished:
            self._clear_annoyance()
            owed, self._nap_greeting = self._nap_greeting, None
            if owed is not None and not self._away:
                # Up from a nap and you are here: the greeting set_away would have given, now he
                # can give it. The nap's own line first, so it is the one that gets said - main
                # says at most one line at a time, and the greeting's is the fallback.
                self._event("nap_wake")
                self._greet(owed[0])
                return
            self._enter(State.IDLE)


