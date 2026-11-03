"""Hands a prefilled prompt to whichever assistant you have pointed him at.

Every provider works the same way: build a URL with the prompt in a query parameter and give it to
the Windows shell. The shell then either routes a custom scheme (``claude://``) to an installed
desktop app, or opens an ``https://`` URL in the default browser. From this module's point of view
those are the same operation, which is why supporting more than one assistant costs a table rather
than a subsystem.

**The prompt is prefilled, not sent.** You review it and press send yourself, inside the assistant.
That is the intended behaviour for every provider here, not a limitation of one of them.

This module deliberately does nothing else. It makes no network requests of its own - handing a URL
to the shell is not one - and it reads no account, usage or subscription data. There is no
supported API for any of that and none is attempted.
"""

from __future__ import annotations

import logging
import os
import subprocess
import urllib.parse
import webbrowser
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: Hard ceiling on a stored or launched prompt, whatever a provider says. Individual providers
#: declare their own, lower, limits below; this is the backstop that keeps a hand-edited config
#: from trying to put a novel in a URL.
MAX_PROMPT_CHARS = 14000


@dataclass(frozen=True)
class Provider:
    """One assistant he can launch, and how to build a link for it."""

    key: str
    label: str
    #: mode name -> base URL. The order here is the order the bubble shows them in, and a provider
    #: with a single mode gets no mode picker at all - see ``AskBubble.show_for``.
    modes: dict[str, str]
    #: Query parameter the prompt travels in.
    query: str = "q"
    #: Where this provider truncates. Per-provider because they genuinely differ, and silently
    #: losing the end of a long prompt is worse than capping it somewhere you can read about.
    max_chars: int = MAX_PROMPT_CHARS
    #: Offered in the failure toast when the link will not open.
    download: str = ""
    #: One line for the settings window, so the choice is explainable without the docs.
    note: str = ""
    #: Human labels for the modes, for the bubble's buttons.
    mode_labels: dict[str, str] = field(default_factory=dict)

    def label_for(self, mode: str) -> str:
        return self.mode_labels.get(mode, mode.replace("_", " ").title())

    @property
    def default_mode(self) -> str:
        return next(iter(self.modes))


#: Everything he can launch. Adding another assistant is an entry here and nothing else.
#:
#: The only real difference between them is the *kind* of link:
#:
#: * ``claude://`` is a **custom URL scheme**. Windows routes it to the installed desktop app and
#:   starts it if it is not already running. Anthropic documents it.
#: * ``https://chatgpt.com/?q=`` is an **ordinary web link** that opens in your default browser
#:   with the composer prefilled. It is the same URL shape browsers use when ChatGPT is added as a
#:   search engine, which is why it is dependable. It needs no desktop app - and it will not use
#:   one even if you have it installed, because Windows sends https to the browser.
PROVIDERS: dict[str, Provider] = {
    "claude": Provider(
        key="claude",
        label="Claude",
        modes={"chat": "claude://claude.ai/new", "cowork": "claude://cowork/new"},
        max_chars=14000,
        download="https://claude.ai/download",
        note="Opens the Claude desktop app. Needs Claude installed.",
        mode_labels={"chat": "Chat", "cowork": "Cowork"},
    ),
    "chatgpt": Provider(
        key="chatgpt",
        label="ChatGPT",
        modes={"chat": "https://chatgpt.com/"},
        # Conservative: this one travels as a browser URL, and browsers start truncating well
        # below what a desktop app accepts.
        max_chars=6000,
        download="https://chatgpt.com/",
        note="Opens chatgpt.com in your browser, prompt prefilled. No app needed.",
        mode_labels={"chat": "Chat"},
    ),
    "gemini": Provider(
        key="gemini",
        label="Gemini",
        modes={"chat": "https://gemini.google.com/app"},
        max_chars=6000,
        download="https://gemini.google.com/",
        note="Opens Gemini in your browser. Prefill is not officially documented.",
        mode_labels={"chat": "Chat"},
    ),
}

DEFAULT_PROVIDER = "claude"

FAILURE_MESSAGE = "Couldn't open {label}. Is it installed?"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def get_provider(key: str | None = None) -> Provider:
    """The provider for ``key``, falling back to the default rather than raising."""
    return PROVIDERS.get(str(key or "").lower().strip(), PROVIDERS[DEFAULT_PROVIDER])


def available_providers() -> list[str]:
    return list(PROVIDERS)


def modes_for(provider_key: str | None = None) -> list[str]:
    return list(get_provider(provider_key).modes)


def max_chars_for(provider_key: str | None = None) -> int:
    return min(MAX_PROMPT_CHARS, get_provider(provider_key).max_chars)


def build_url(mode: str, prompt: str, provider_key: str | None = None) -> str:
    """Build the link for ``mode``, URL-encoding ``prompt``.

    Raises ``ValueError`` for a mode this provider does not have. An empty prompt still yields a
    valid URL; whether that is worth launching is the caller's decision.
    """
    provider = get_provider(provider_key)
    wanted = str(mode).lower().strip()
    base = provider.modes.get(wanted)
    if base is None:
        raise ValueError(
            f"{provider.label} has no {wanted!r} mode; expected one of {sorted(provider.modes)}"
        )
    # Truncated before encoding, so the cap counts prompt characters rather than escape sequences.
    trimmed = (prompt or "")[: max_chars_for(provider.key)]
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{provider.query}={urllib.parse.quote(trimmed, safe='')}"


def launch(mode: str, prompt: str, provider_key: str | None = None) -> tuple[bool, str | None]:
    """Hand the link to the shell. Returns ``(ok, reason_if_failed)``."""
    provider = get_provider(provider_key)
    try:
        url = build_url(mode, prompt, provider.key)
    except ValueError as exc:
        log.error("refusing to launch: %s", exc)
        return False, str(exc)

    failure = FAILURE_MESSAGE.format(label=provider.label)
    # The prompt itself is never logged - only how long it was. See the privacy notes.
    log.info("launching %s %s link (%d prompt chars)", provider.key, mode, len(prompt or ""))

    try:
        os.startfile(url)  # type: ignore[attr-defined]  # Windows-only, and that is the target
        return True, None
    except OSError as exc:
        log.warning("os.startfile failed for %s: %s", mode, exc)
    except AttributeError:  # pragma: no cover - non-Windows safety net
        log.warning("os.startfile unavailable on this platform")

    try:
        completed = subprocess.run(
            ["cmd", "/c", "start", "", url],
            shell=False,
            creationflags=_CREATE_NO_WINDOW,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("start fallback failed: %s", exc)
        return False, failure

    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        log.warning("start fallback exited %d: %s", completed.returncode, detail)
        return False, failure
    return True, None


def open_download_page(provider_key: str | None = None) -> None:
    """Open the provider's download or home page in the browser. Never raises."""
    url = get_provider(provider_key).download
    if not url:
        return
    try:
        webbrowser.open(url)
    except Exception:
        log.exception("could not open %s", url)
