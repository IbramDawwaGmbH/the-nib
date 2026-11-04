# Changelog

Every release, newest first. Versions follow [semantic versioning](CONTRIBUTING.md#releases).

## [Unreleased]

### Added

- **He has a name**, and you can change it: `name`, first on Settings → Personality. It is what
  the tray tooltip, his toasts and his lines call him, and a rename applies on Apply with no
  restart. Window titles still say Nib — that's the app.
- **You can see how he is.** Hover over the tray icon: when nothing more pressing is showing, the
  tooltip is his name and his mood in a word — `Nib — fine`, `Nib — in good spirits` — and it
  changes as soon as you pet him. A new **About him** tab in Settings says the same in a sentence.
- A fifth first-run tip: that you can rename him, and where to see how he's doing.

### Changed

- The tray tooltip's focus, microphone and pinned-window lines start with his name instead of a
  fixed "Nib", and so does the focus-complete notification.

## [1.2.0] - 2026-11-04

His wardrobe, redone.

### Changed

- **Every costume is shaded now**, lit from the top left as he is, instead of flat colour, and a
  hat worn on his head puts a soft shadow on him, so it looks worn rather than pasted on. Done
  when the sheet is built, so it costs nothing while he runs.
- **22 costumes redrawn**: the top hat, cowboy, witch and wizard hats, the mortarboard, the
  eyepatch (one eye now, not his whole face), Groucho glasses, the monocle, the VR headset, the
  jetpack, backpack, tool belt, cape, wings, scarf, halo, antennae, bunny ears, flower crown,
  leaf and snow cap.
- **Seven new costume colours**: purple, pink, brown, charcoal, silver, flame orange and teal.

### Added

- **Ten costumes move**: the propeller spins, the jetpack's flame flickers, the wings flap, the
  cape and the scarf's tail sway, the halo bobs, the antennae wobble, a bunny ear twitches, the
  mortarboard's tassel swings and the VR headset blinks. They move with him — faster when he
  walks — and take nothing extra to draw.

## [1.1.1] - 2026-11-04

Two small fixes to Update and restart. On 1.1.0, pick **Update and restart** on the wheel and he
fetches this one himself.

### Fixed

- A screen guard started in the same instant a git checkout's update got the go-ahead could slip
  past the check that holds new guards back until the restart. The go-ahead is now decided on the
  same thread every guard starts on, so one or the other wins, never both.
- Quitting from the tray while a downloaded release waited for its restart (held up by a guard or a
  focus session) left the download in your temp folder. It is thrown away now, and any left behind
  by a Nib that crashed or was killed are cleared out the next time he starts.

## [1.1.0] - 2026-11-04

Nib can now update himself: pick **Update and restart** on the tool wheel.

**Download** `Nib-v1.1.0-windows-x64.zip` below, extract it over the old folder (or anywhere), and
run `Nib.exe`. Running from source? `git pull` once more; after that, the wheel does it for you.

### Added

- **Update and restart**, last in the tool wheel's App group, with its own icon. Running from a git
  checkout, it runs `git fetch origin` and fast-forwards the branch (never a merge, rebase, reset or
  stash; local changes or a diverged branch are left alone, with the reason), reinstalls
  `requirements.txt` if it changed, and restarts. Running the release build, it fetches the latest
  release from GitHub if it's newer, checks the zip's digest and contents, and a small helper swaps
  it in after Nib quits — all of it or none of it — and starts him again (the old version, put back
  whole, if the swap fails). Already up to date: he says so and stays put. Not while the screen
  guard is up or during a focus session; one that starts while it runs stops it before anything
  changes, or holds the restart until it's over. See
  [Using him](https://github.com/IbramDawwaGmbH/the-nib/blob/main/docs/USING-HIM.md#update-and-restart).
- `version.py` holds the version the release build compares itself against, and a release is not
  built from a tag that doesn't match it.

### Changed

- **Nib makes one network request of its own now** — only when you pick Update and restart: to
  GitHub, and, from a git checkout whose update changed `requirements.txt`, to PyPI through pip.
  Nothing checks for updates by itself. [Privacy](https://github.com/IbramDawwaGmbH/the-nib/blob/main/docs/PRIVACY.md)
  names it as the one exception.

## [1.0.2] - 2026-11-04

The screen guard's PIN field now always takes your typing after a blackout. (1.0.1 was tagged with
these same fixes, but its build stopped on a flaky test, so it has no download; 1.0.2 is that
release.)

**Download** `Nib-v1.0.2-windows-x64.zip` below, extract it over the old folder (or anywhere), and
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
