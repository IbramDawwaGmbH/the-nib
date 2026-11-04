# Security

## Reporting a problem

Please **don't open a public issue** for a security problem. Report it privately through GitHub's
[private vulnerability reporting](https://github.com/IbramDawwaGmbH/the-nib/security/advisories/new)
instead, with what you found and how to reproduce it. You'll get a reply within a week.

## What counts

Nib runs entirely on your machine and makes no network requests of its own — the one exception is
Update and restart, and only when you pick it — so the interesting parts are local:

- **The screen guard** — any way past the fog, the blackout or the PIN without the PIN, Windows
  Hello or a Windows sign-in. (Ctrl+Alt+Del and Win+L are deliberately not blocked; see
  [Using him](docs/USING-HIM.md#the-screen-guard).)
- **Anything he reads or stores that the [privacy page](docs/PRIVACY.md) says he doesn't.**
- **The launcher** — a crafted prompt or dropped file that makes him open something other than your
  chosen assistant.
- **Update and restart** — anything that gets it to install something other than this repository's
  own release or branch: a zip accepted with the wrong digest, a path that escapes the install
  folder, a git command other than a fetch of origin and a fast-forward, or a pip install of
  anything but the updated `requirements.txt`.

## Supported versions

Only the latest release gets fixes.
