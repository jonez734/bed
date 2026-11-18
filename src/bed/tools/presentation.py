"""The shared credentials prompt: one look for every front end (casino, round-view, letteredolive, bed).

``prompt_credentials`` asks for a moniker and a password. The moniker is read as text and the password is masked and
never echoed. A blank field is asked again, so a blank is never returned to a caller. Ctrl-D cancels. On a terminal
without ANSI the box is printed as plain text.

Customization is by parameters, not forks: ``title`` names the box, ``header`` is an optional line shown first, and
``width`` sets the field width. The look comes from bbsengine6's ``draw_box`` (the frame in demo_box_inputstring.py).
"""

from __future__ import annotations

from typing import Optional

from bbsengine6 import io
from bbsengine6.util import draw_box, inputpassword


class CredentialsCancelled(Exception):
    """The player pressed Ctrl-D at the prompt."""


def _has_ansi() -> bool:
    # Wait for the terminal's answer. The non-blocking form returns False without probing, which put every terminal on
    # the plain-text line.
    return io.terminal.has_ansi()


def _box(title: str, width: int) -> None:
    draw_box(title, width, 1)


def _enter_box(height: int = 1) -> None:
    """Move the cursor from the box's bottom edge to the first interior column, where the field starts.

    draw_box leaves the cursor at the end of the bottom edge and does not move it; the caller does, as the demo does.
    """
    io.echo(f"{{cursorup:{max(2, height)}}}{{cha:1}}{{cursorright:2}}", end="")


def _plain(title: str) -> None:
    if title:
        io.echo(f"{title}")


def _read_field(prompt: str, width: int) -> str:
    return io.inputstring(prompt, "", max_width=width - 1, bottombar=False)


def _read_password(prompt: str, width: int) -> str:
    return inputpassword(prompt, args=None, bottombar=False)


def prompt_credentials(*, title: str = "login", header: Optional[str] = None, width: int = 25) -> tuple[str, str]:
    """Ask for a moniker and password; return them. Blank fields are asked again. Raises CredentialsCancelled on Ctrl-D."""
    if header:
        io.echo(header)
    ansi = _has_ansi()
    if ansi:
        _box(title, width)
    else:
        _plain(title)
    if ansi:
        _enter_box(1)
    try:
        moniker = ""
        while not moniker.strip():
            moniker = _read_field("{promptcolor}moniker: {inputcolor}", width)
        password = ""
        while not password:
            password = _read_password("{promptcolor}password: {inputcolor}", width)
    except EOFError as exc:
        raise CredentialsCancelled() from exc
    return moniker.strip(), password
