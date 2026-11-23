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
from bbsengine6.util import draw_frame, inputpassword


class CredentialsCancelled(Exception):
    """The player pressed Ctrl-D at the prompt."""


def _has_ansi() -> bool:
    # Wait for the terminal's answer. The non-blocking form returns False without probing, which put every terminal on
    # the plain-text line.
    return io.terminal.has_ansi()


def _plain(title: str) -> None:
    if title:
        io.echo(f"{title}")


def _read_field(prompt: str, width: int) -> str:
    return io.inputstring(prompt, "", max_width=width - 1, bottombar=False)


def _read_password(prompt: str, width: int) -> str:
    return inputpassword(prompt, args=None, bottombar=False)


def prompt_credentials(*, title: str = "login", header: Optional[str] = None, width: int = 25,
                       **kwargs) -> tuple[str, str]:
    """Ask for a moniker and a password in one frame, with a row for each. Blank fields are asked again. Ctrl-D cancels.

    The frame is drawn once with two interior rows. The moniker is read on the first row; its Enter leaves the cursor one
    row below the field (after_field), and the password is read on the second row. No box is grown and no absolute
    position is sent. On a terminal without ANSI the prompts are printed as plain text. ``**kwargs`` is accepted so the
    configured presentation keeps matching the contract; extra options are ignored.
    """
    if header:
        io.echo(header)
    ansi = _has_ansi()
    if not ansi:
        _plain(title)
    frame = None
    try:
        if ansi:
            frame = draw_frame(title, width, height=2)
            # Paint every child's label before the first read, then move back to the first child.
            frame.paint_label(0, "moniker:")
            frame.paint_label(1, "password:")
            frame.place_row(0)
        moniker = ""
        while not moniker.strip():
            moniker = _read_field("{promptcolor}moniker: {inputcolor}", width)
        if ansi:
            frame.after_field()
            frame.place_row(1)
        password = ""
        while not password:
            password = _read_password("{promptcolor}password: {inputcolor}", width)
    except EOFError as exc:
        raise CredentialsCancelled() from exc
    return moniker.strip(), password
