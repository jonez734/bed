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
from bbsengine6.util import Child, draw_frame, extend_frame, inputpassword


class CredentialsCancelled(Exception):
    """The player pressed Ctrl-D at the prompt."""


def _has_ansi() -> bool:
    # Wait for the terminal's answer. The non-blocking form returns False without probing, which put every terminal on
    # the plain-text line.
    return io.terminal.has_ansi()


def _plain(title: str) -> None:
    if title:
        io.echo(f"{title}")


def _field_at(frame, index: int):
    """The column and row where the field of child ``index`` starts, from the frame's geometry."""
    place = frame.children[index]
    return place.column, place.top


def _extend(frame, child: Child, *, width: int):
    """Grow the frame with ``child`` below it, at the frame's width (see bbsengine6 extend_frame)."""
    return extend_frame(frame, child, width=width)


def _read_field(prompt: str, width: int) -> str:
    return io.inputstring(prompt, "", max_width=width - 1, bottombar=False)


def _read_password(prompt: str, width: int) -> str:
    return inputpassword(prompt, args=None, bottombar=False)


def prompt_credentials(*, title: str = "login", header: Optional[str] = None, width: int = 25) -> tuple[str, str]:
    """Ask for a moniker, then a password in a box grown below it. Blank fields are asked again. Ctrl-D cancels.

    The moniker box is drawn first. Enter accepts the moniker and adds the password box under it at the same width, then
    the password is read there. On a terminal without ANSI there are no boxes; the prompts are printed as plain text.
    """
    if header:
        io.echo(header)
    ansi = _has_ansi()
    if not ansi:
        _plain(title)
    try:
        if ansi:
            frame = draw_frame(title, width, children=[Child(title="moniker")])
            moniker_col, moniker_row = _field_at(frame, 0)
            io.echo(f"{{curpos:{moniker_row},{moniker_col}}}", end="")
        moniker = ""
        while not moniker.strip():
            moniker = _read_field("{promptcolor}moniker: {inputcolor}", width)
        if ansi:
            frame = _extend(frame, Child(title="password"), width=width)
            password_col, password_row = _field_at(frame, len(frame.children) - 1)
            io.echo(f"{{curpos:{password_row},{password_col}}}", end="")
        password = ""
        while not password:
            password = _read_password("{promptcolor}password: {inputcolor}", width)
    except EOFError as exc:
        raise CredentialsCancelled() from exc
    return moniker.strip(), password
