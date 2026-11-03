# Security

## Reporting a problem

Please **don't open a public issue** for a security problem. Report it privately through GitHub's
[private vulnerability reporting](https://github.com/IbramDawwaGmbH/the-nib/security/advisories/new)
instead, with what you found and how to reproduce it. You'll get a reply within a week.

## What counts

Nib runs entirely on your machine and makes no network requests of its own, so the interesting
parts are local:

- **The screen guard** — any way past the fog, the blackout or the PIN without the PIN, Windows
  Hello or a Windows sign-in. (Ctrl+Alt+Del and Win+L are deliberately not blocked; see
  [Using him](docs/USING-HIM.md#the-screen-guard).)
- **Anything he reads or stores that the [privacy page](docs/PRIVACY.md) says he doesn't.**
- **The launcher** — a crafted prompt or dropped file that makes him open something other than your
  chosen assistant.

## Supported versions

Only the latest release gets fixes.
