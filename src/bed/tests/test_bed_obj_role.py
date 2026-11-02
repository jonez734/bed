"""9.0: bed --install creates the NOLOGIN bed_obj owner role, idempotently."""

from unittest.mock import MagicMock, patch

from bed import startup


def test_role_name():
    assert startup.OWNER_OBJ_ROLE == "bed_obj"


def test_creates_nologin_nosuperuser_role_when_missing():
    with patch.object(startup.database, "rolexists", return_value=False), \
         patch.object(startup.database, "createrol", return_value=True) as create:
        assert startup._ensure_owner_obj_role(MagicMock(), MagicMock()) is True
    kwargs = create.call_args.kwargs
    assert kwargs["login"] is False
    assert kwargs["superuser"] is False
    assert kwargs["createdb"] is False
    assert kwargs["createrole"] is False
    assert kwargs["inherit"] is True


def test_noop_when_role_exists():
    with patch.object(startup.database, "rolexists", return_value=True), \
         patch.object(startup.database, "createrol") as create:
        assert startup._ensure_owner_obj_role(MagicMock(), MagicMock()) is True
    create.assert_not_called()


def test_returns_false_when_create_fails():
    with patch.object(startup.database, "rolexists", return_value=False), \
         patch.object(startup.database, "createrol", return_value=False):
        assert startup._ensure_owner_obj_role(MagicMock(), MagicMock()) is False
