"""The shape gate for message_send: recipients and content are required, and no moniker.

The member is identified by the token, not by a moniker field. The other ops keep their
moniker rule.
"""

import pytest

from bed.api import message as message_mod


@pytest.mark.unit
def test_send_without_moniker_passes_the_shape_gate():
    msg = {"recipients": ["bob"], "content": "hello"}
    assert message_mod._validate_shape("send", msg) is None


@pytest.mark.unit
def test_send_without_recipients_is_refused():
    err = message_mod._validate_shape("send", {"content": "hello"})
    assert err is not None
    assert err["code"] == "missing_recipients"


@pytest.mark.unit
def test_send_without_content_is_refused():
    err = message_mod._validate_shape("send", {"recipients": ["bob"], "content": "  "})
    assert err is not None
    assert err["code"] == "missing_content"


@pytest.mark.unit
@pytest.mark.parametrize("op", ["subscribe", "unsubscribe", "list_pending"])
def test_moniker_ops_still_require_a_moniker(op):
    err = message_mod._validate_shape(op, {})
    assert err is not None
    assert err["code"] == "missing_moniker"


@pytest.mark.unit
@pytest.mark.parametrize("op", ["subscribe", "unsubscribe", "list_pending"])
def test_moniker_ops_pass_with_a_moniker(op):
    assert message_mod._validate_shape(op, {"moniker": "alice"}) is None
