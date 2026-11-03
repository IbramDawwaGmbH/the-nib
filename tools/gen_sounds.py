"""Generate Nib's sound effects as WAV files.

Usage:
    python tools/gen_sounds.py [out_dir]

Same principle as ``gen_sprites.py``: **no binary assets in the repository**. Every sound is
synthesised here from a handful of oscillators and envelopes, which means the whole palette is one
table you can read, the tone stays consistent by construction, and changing the character of the
whole set is an edit rather than a trip to a sample library.

Deliberately tiny and deliberately dull: 22050Hz mono 16-bit, nothing over a fifth of a second.
These punctuate what he is already doing. A mascot whose noises are interesting is a mascot you
mute within the hour - which is also why the feature ships switched off.

The standard library only. ``wave`` and ``struct`` are enough, and adding a dependency for a
handful of sine waves would be absurd.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

RATE = 22050
AMPLITUDE = 0.5          # headroom: these are mixed against whatever else is playing

Sample = list[float]


# --- oscillators ---------------------------------------------------------------------
#
# Square and triangle rather than sine, because a pure sine at this length reads as a system beep.
# The pixel art is made of hard edges; so is this.


def _osc(kind: str, phase: float) -> float:
    phase %= 1.0
    if kind == "square":
        return 1.0 if phase < 0.5 else -1.0
    if kind == "triangle":
        return 4.0 * abs(phase - 0.5) - 1.0
    if kind == "saw":
        return 2.0 * phase - 1.0
    if kind == "noise":
        # Deterministic hash noise, so a rebuild produces a byte-identical file. random() would
        # make every regeneration a diff.
        h = int(phase * 1_000_003) * 1103515245 + 12345
        return ((h >> 16) & 0x7FFF) / 16383.5 - 1.0
    return math.sin(2.0 * math.pi * phase)


def tone(
    freq_from: float,
    freq_to: float | None = None,
    ms: float = 90.0,
    kind: str = "square",
    *,
    attack: float = 0.02,
    decay: float = 0.75,
    gain: float = 1.0,
    vibrato: float = 0.0,
) -> Sample:
    """One swept note with an attack/decay envelope.

    ``freq_to`` sweeps: up reads as a question or an appearance, down as a settle or a refusal.
    That single parameter does most of the emotional work in the table below.
    """
    freq_to = freq_from if freq_to is None else freq_to
    count = max(1, int(RATE * ms / 1000.0))
    out: Sample = []
    phase = 0.0
    for i in range(count):
        t = i / count
        freq = freq_from + (freq_to - freq_from) * t
        if vibrato:
            freq *= 1.0 + vibrato * math.sin(2.0 * math.pi * 11.0 * t)
        phase += freq / RATE
        if t < attack:
            envelope = t / attack if attack else 1.0
        elif t > 1.0 - decay:
            envelope = (1.0 - t) / decay if decay else 1.0
        else:
            envelope = 1.0
        out.append(_osc(kind, phase) * envelope * gain)
    return out


def silence(ms: float) -> Sample:
    return [0.0] * max(0, int(RATE * ms / 1000.0))


def chain(*parts: Sample) -> Sample:
    out: Sample = []
    for part in parts:
        out.extend(part)
    return out


def blend(*parts: Sample) -> Sample:
    """Overlay, for the ones that want two voices at once."""
    length = max((len(p) for p in parts), default=0)
    out = [0.0] * length
    for part in parts:
        for i, value in enumerate(part):
            out[i] += value
    return out


# --- the palette ---------------------------------------------------------------------
#
# Name -> how it is built. The names are EVENTS, not descriptions of the noise, so re-tuning one
# never means renaming it. behavior/main only ever ask for an event.

SOUNDS: dict[str, Sample] = {
    # Opening the bubble: a short rising blip. The one you hear most, so it is the quietest.
    "open": tone(520, 700, 70, "triangle", gain=0.5),
    # Sending: two notes up. Resolution, not celebration.
    "send": chain(tone(600, 640, 55, "square", gain=0.45),
                  tone(800, 860, 90, "square", gain=0.45)),
    # Dismissing without sending, which is the thing that annoys him: two notes DOWN.
    "poke": chain(tone(420, 400, 50, "square", gain=0.4),
                  tone(310, 280, 80, "square", gain=0.4)),
    # Crossing the annoyance threshold. Buzzy and flat.
    "grump": tone(180, 120, 240, "saw", gain=0.5, vibrato=0.08),
    # Landing after a drop. A thud is mostly noise with a very fast decay.
    "land": blend(tone(90, 55, 110, "noise", attack=0.005, decay=0.9, gain=0.45),
                  tone(150, 70, 90, "triangle", attack=0.005, decay=0.9, gain=0.3)),
    # Being petted. The only warm one in the set: a slow wobble, near enough to a purr.
    "pet": tone(230, 260, 420, "triangle", attack=0.25, decay=0.55, gain=0.32, vibrato=0.06),
    # Dropping off, and waking up: the same two notes in opposite orders.
    "sleep": chain(tone(360, 330, 130, "triangle", gain=0.3),
                   tone(250, 220, 200, "triangle", gain=0.3)),
    "wake": chain(tone(280, 300, 90, "triangle", gain=0.32),
                  tone(400, 440, 120, "triangle", gain=0.32)),
    # The involuntary lot. Each is two parts, because that is what makes them read as one event
    # happening TO him rather than a beep: an intake and a release, a slip and a landing.
    #
    # A sneeze is "ah-CHOO" - a rising breathy note, then a burst. The burst is noise rather than a
    # tone because a pitched sneeze sounds like a bird.
    "sneeze": chain(
        tone(300, 430, 190, "triangle", attack=0.35, decay=0.15, gain=0.24),
        blend(tone(760, 190, 170, "noise", attack=0.002, decay=0.88, gain=0.52),
              tone(520, 150, 150, "square", attack=0.002, decay=0.92, gain=0.22)),
    ),
    # A slip and then a small thud. The downward sweep is the whole joke.
    "trip": chain(
        tone(540, 190, 120, "square", attack=0.01, decay=0.7, gain=0.33),
        blend(tone(95, 55, 95, "noise", attack=0.004, decay=0.9, gain=0.4),
              tone(150, 70, 85, "triangle", attack=0.004, decay=0.9, gain=0.26)),
    ),
    # Short, high, and pitched UP - a hiccup is a catch, not a note.
    "hiccup": tone(360, 720, 75, "square", attack=0.02, decay=0.55, gain=0.3),
    # A whirr up and back down again, with enough vibrato to sound mechanical.
    "spin": chain(tone(280, 640, 280, "saw", gain=0.26, vibrato=0.16),
                  tone(640, 250, 210, "saw", gain=0.22, vibrato=0.13)),
    # The task-finished celebration: a rising three-note major arpeggio (A4-C#5-E5) ending on a
    # flourish up to A5. Where send is "two notes up, resolution not celebration", this is the one
    # sound in the set actually meant to sound like a small triumph.
    "cheer": chain(
        tone(440, 440, 65, "square", gain=0.34),
        tone(554, 554, 65, "square", gain=0.34),
        tone(659, 880, 150, "square", attack=0.02, decay=0.55, gain=0.42, vibrato=0.05),
    ),
    # A focus session completing on its own: two soft triangle notes a fifth apart (C5-G5), the
    # way a meditation timer or a kitchen bell rings you back rather than "cheer"'s punchier
    # square-wave triumph - calmer, because the two need to read as different accomplishments even
    # when they land back to back (a session ending right as a build finishes).
    "focus_done": chain(
        tone(523, 523, 160, "triangle", attack=0.05, decay=0.6, gain=0.36),
        tone(784, 784, 280, "triangle", attack=0.05, decay=0.7, gain=0.4),
    ),
}


def write_wav(path: Path, samples: Sample) -> int:
    frames = bytearray()
    for value in samples:
        clipped = max(-1.0, min(1.0, value * AMPLITUDE))
        frames += struct.pack("<h", int(clipped * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(bytes(frames))
    return len(frames) + 44


def render(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, samples in SOUNDS.items():
        size = write_wav(out_dir / f"{name}.wav", samples)
        total += size
        print(f"  {name + '.wav':14s} {len(samples) / RATE * 1000:5.0f}ms  {size / 1024:5.1f} KB")
    print(f"wrote {len(SOUNDS)} sounds to {out_dir} ({total / 1024:.0f} KB total)")


if __name__ == "__main__":
    target = (
        Path(sys.argv[1]) if len(sys.argv) > 1
        else Path(__file__).parent.parent / "assets" / "sounds"
    )
    render(target)
