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
from bbsengine6.form import Form, FormItemTextbox
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


#: The sign-in labels, padded to one width so the values after them start in the same column.
LABEL_MONIKER = "moniker:  "
LABEL_PASSWORD = "password: "


def _default_form(title: str) -> Form:
    """The sign-in form when a caller does not pass one: a moniker, then a password shown as nothing."""
    return Form(title, [
        FormItemTextbox(label=LABEL_MONIKER, name="moniker"),
        FormItemTextbox(label=LABEL_PASSWORD, name="password", mask=""),
    ])


def prompt_credentials(*, title: str = "login", header: Optional[str] = None, width: int = 25,
                       form: Optional[Form] = None, **kwargs) -> tuple[str, str]:
    """Ask for a moniker and a password in one frame, with a row for each. Blank fields are asked again. Ctrl-D cancels.

    The frame is drawn once with two interior rows. The moniker is read on the first row; its Enter leaves the cursor one
    row below the field (after_field), and the password is read on the second row. No box is grown and no absolute
    position is sent. On a terminal without ANSI the prompts are printed as plain text.

    ``form`` describes the two textboxes (moniker, then password); its labels and the password's mask come from it. When it
    is not given, the sign-in form is built here, so the old keywords still work. ``**kwargs`` is accepted so the configured
    presentation keeps matching the contract; extra options are ignored.
    """
    if form is None:
        form = _default_form(title)
    moniker_box, password_box = form[0], form[1]
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
            frame.paint_label(0, moniker_box.label)
            frame.paint_label(1, password_box.label)
            frame.place_row(0)
        moniker = ""
        while not moniker.strip():
            moniker = _read_field("{promptcolor}" + moniker_box.label + "{inputcolor}", width)
        if ansi:
            frame.after_field()
            frame.place_row(1)
        password = ""
        while not password:
            password = _read_password("{promptcolor}" + password_box.label + "{inputcolor}", width)
    except EOFError as exc:
        raise CredentialsCancelled() from exc
    return moniker.strip(), password
