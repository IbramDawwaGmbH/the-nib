# Changelog

Every release, newest first. Versions follow [semantic versioning](CONTRIBUTING.md#releases).

## [1.0.1] - 2026-11-04

The screen guard's PIN field now always takes your typing after a blackout.

**Download** `Nib-v1.0.1-windows-x64.zip` below, extract it over the old folder (or anywhere), and
run `Nib.exe`. Running from source? `git pull`.

### Fixed

- **Every guard has a PIN now, even where Windows Hello works.** Before, a PC with Hello never got a
  guard PIN, so after a blackout there was no field to type into. The next Ctrl+Alt+G asks you to
  set one once; Hello still works alongside it.
- **No key stays stuck down.** A click that swapped windows at the wrong moment could leave Ctrl,
  Shift or Win held for the whole system, so every PIN key arrived as a shortcut and typed nothing.
- **Opening Windows Hello yourself no longer blacks the screen out**, and a Hello prompt left over
  from an earlier guard can't keep the cursor out of the PIN field.
- **A Nib dialog that was open when the guard started** (Settings → Restore defaults, for one) no
  longer freezes every click and key on the guard.
- **Second monitors:** a click or keys on another screen now hand the keyboard to the PIN field.
- **A safety net:** after any click on the guard, Nib checks that the PIN field is visible, focused
  and receiving keys, takes the keyboard back if not, and notes it in `nib.log` (never your PIN).
- A guard rebuilt mid-countdown shows only the time that's left, not a fresh countdown.

## [1.0.0] - 2026-11-03

The first public release.

**Download** `Nib-v1.0.0-windows-x64.zip` below, extract it anywhere, and run `Nib.exe`. No Python
needed. Windows 10 or 11. The build isn't code-signed, so the first time Windows SmartScreen may
say it "protected your PC": choose **More info → Run anyway**. Prefer to run from source? See
[Getting started](https://github.com/IbramDawwaGmbH/the-nib/blob/main/docs/GETTING-STARTED.md).

### What he does

- **Opens your assistant with the prompt already typed.** Click him, press **Ctrl+Alt+C** anywhere,
  or drop a file on him. Claude (desktop app), ChatGPT or Gemini opens with the prompt in the
  composer, never sent for you.
- **A game-style tool wheel** on right-click: flick toward a tool and let go, scroll for more. His
  eyes follow the tool you aim at, and nearly every pick gets its own little reaction.
- **A wardrobe** of 53 costumes on the wheel, as thumbnails with the ones you wore last first. He
  hops and changes at the top of the hop, then tells you what he thinks of it.
- **Answers sums and unit conversions himself**, inline, without sending them anywhere.
- **A pixel ruler**, **drop-to-OCR** (Shift-drop an image, read locally), and **the snippet
  billboard** (Ctrl+Alt+P pins your clipboard to a sign he holds).
- **The screen guard** (Ctrl+Alt+G): fog over every monitor, giant eyes, a PIN or Windows Hello to
  get back in, while your builds and agents keep running.
- **Focus sessions**, a **mic and camera indicator** with a "you're muted" nudge, and **drop him on
  a window** to pin it on top.
- **A personality**: he knows the time of day and the season, remembers how the day went, notices
  when a long job finishes, and sulks when you pester him.

### Privacy

No window titles, no keystrokes, no files read (one named exception: an image you Shift-drop for
OCR), and no network requests of his own. See
[Privacy](https://github.com/IbramDawwaGmbH/the-nib/blob/main/docs/PRIVACY.md).
