"""The bed CLI's `message` takes list and show as subcommands, alongside pending and send.

`list` is the same operation as `pending` (bed's list_pending). `show <id>` returns one pending
message for the signed-in member. Neither sends a message.
"""

import argparse

import pytest

from bed.tools import message as msg_tool


def _parser():
    p = argparse.ArgumentParser()
    msg_tool.buildargs(p)
    return p


@pytest.mark.unit
def test_list_is_a_subcommand():
    ns = _parser().parse_args(["list"])
    assert ns.subcommand == "list"


@pytest.mark.unit
def test_list_maps_to_the_pending_operation():
    assert msg_tool._WS_OP["list"] == "list_pending"
    assert msg_tool._WS_OP["pending"] == "list_pending"


@pytest.mark.unit
def test_show_takes_an_id():
    ns = _parser().parse_args(["show", "281"])
    assert ns.subcommand == "show"
    assert ns.id == 281


@pytest.mark.unit
def test_show_requires_an_id():
    with pytest.raises(SystemExit):
        _parser().parse_args(["show"])


@pytest.mark.unit
def test_send_is_still_a_subcommand():
    ns = _parser().parse_args(["send", "--to", "bob", "--content", "hi"])
    assert ns.subcommand == "send"
