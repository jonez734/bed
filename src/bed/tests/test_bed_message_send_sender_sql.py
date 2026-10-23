"""engine.bed_message_send takes an optional chosen sender, honoured only for a sysop token.

A non-sysop token must send as its own session member. A different p_sender from a non-sysop
raises before anything is written. A sysop may choose the sender. Refusals raise an exception,
so no row is stored.
"""

import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_message_send.sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def _text() -> str:
    return _code(SQL.read_text())


def _signature() -> str:
    m = re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.bed_message_send\s*\((.*?)\)\s*RETURNS",
        _text(),
        re.IGNORECASE | re.DOTALL,
    )
    assert m, "bed_message_send not defined"
    return m.group(1)


def test_signature_accepts_an_optional_chosen_sender():
    assert re.search(r"p_sender\s+citext", _signature(), re.IGNORECASE)


def test_chosen_sender_defaults_to_null():
    assert re.search(r"p_sender\s+citext\s+DEFAULT\s+NULL", _signature(), re.IGNORECASE)


def test_non_sysop_with_a_different_sender_raises():
    text = _text()
    assert re.search(
        r"IF\s+p_sender\s+IS\s+NOT\s+NULL\s+AND\s+p_sender\s+IS\s+DISTINCT\s+FROM\s+v_sender",
        text,
        re.IGNORECASE,
    ), "must compare p_sender with the session member"
    assert re.search(r"NOT\s*\(\s*engine\.bed_is_sysop\(v_sender\)\s+IS\s+TRUE\s*\)", text)


def test_sysop_check_guards_the_chosen_sender():
    text = _text()
    m = re.search(
        r"IF\s+p_sender\s+IS\s+NOT\s+NULL.*?END\s+IF;",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    assert m, "no p_sender guard found"
    assert "bed_is_sysop" in m.group(0)
    assert "RAISE EXCEPTION" in m.group(0)


def test_chosen_sender_is_used_as_the_sender_once_authorised():
    text = _text()
    assert re.search(r"v_sender\s*:=\s*p_sender", text), "the authorised p_sender must become v_sender"


def test_refusal_happens_before_any_insert():
    text = _text()
    guard = text.find("p_sender IS NOT NULL")
    insert = text.find("INSERT INTO engine.__message ")
    assert guard != -1 and insert != -1
    assert guard < insert, "the sender check must run before the message row is written"


def test_sender_is_still_read_from_the_token_row_first():
    text = _text()
    lookup = text.find("FROM engine.__bed_token")
    guard = text.find("p_sender IS NOT NULL")
    assert lookup != -1 and guard != -1 and lookup < guard


def test_grant_names_the_six_argument_signature():
    # The EXECUTE grant to bed must match the function as it now exists.
    text = _text()
    assert re.search(
        r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_message_send\(bytea,\s*text,\s*text,\s*text,\s*citext\[\],\s*citext\)\s+TO\s+bed",
        text,
        re.IGNORECASE,
    )


def test_old_five_argument_signature_is_only_named_in_the_drop():
    # The old signature may appear only in the DROP that removes it, never in a GRANT.
    text = _text()
    old = r"engine\.bed_message_send\(bytea,\s*text,\s*text,\s*text,\s*citext\[\]\)"
    assert not re.search(r"GRANT[^;]*" + old, text, re.IGNORECASE)
    assert re.search(r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+" + old, text, re.IGNORECASE)
