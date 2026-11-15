"""A token file can be named for the terminal it belongs to, so two clients on one account do not share it.

``token_path_for_tty`` returns ``<dir>/<stem>.<tty>.token``; a session with no tty keeps ``<stem>.token``.
The default path is unchanged for every existing reader.
"""

import os

import pytest

from bed.tools import _token


@pytest.mark.unit
def test_a_tty_is_part_of_the_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(_token.os, "ttyname", lambda fd: "/dev/pts/4")
    path = _token.token_path_for_tty(str(tmp_path), fd=0)
    assert os.path.basename(path) == "bed.pts_4.token"


@pytest.mark.unit
def test_no_tty_keeps_the_plain_name(tmp_path, monkeypatch):
    def no_tty(fd):
        raise OSError("not a tty")

    monkeypatch.setattr(_token.os, "ttyname", no_tty)
    path = _token.token_path_for_tty(str(tmp_path), fd=0)
    assert os.path.basename(path) == "bed.token"


@pytest.mark.unit
def test_the_default_path_is_unchanged_for_existing_readers():
    # default_token_path() still returns bed.token; only the per-tty helper adds the tty.
    assert os.path.basename(_token.default_token_path()) == "bed.token"
