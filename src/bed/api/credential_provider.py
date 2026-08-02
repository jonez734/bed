# bed/api/credential_provider.py
# Pluggable credential check for bed's AuthService.

from __future__ import annotations

from typing import Any, Optional, Protocol

from .token_store import MemberInfo


class CredentialProvider(Protocol):
    """Validates a (moniker, password) pair against some backend.

    Return a MemberInfo on success, or None on any failure (unknown
    moniker, wrong password, DB unavailable, etc). The AuthService
    translates None into a `bad_credentials` error envelope; the
    provider does not need to distinguish failure modes.
    """

    def authenticate(
        self,
        args: Any,
        moniker: str,
        password: str,
        *,
        pool: Any = None,
    ) -> Optional[MemberInfo]: ...


class MonikerOnlyCredentialProvider:
    """Confirms the moniker exists in the database; ignores password.

    Mirrors the historical `MonikerAuthRouter` behavior: any non-empty
    password is accepted once the moniker resolves. Use for development,
    wscat smoke tests, or when the game will gate sensitive actions
    through per-route password challenges.
    """

    def authenticate(
        self,
        args: Any,
        moniker: str,
        password: str,
        *,
        pool: Any = None,
    ) -> Optional[MemberInfo]:
        from bbsengine6 import io, member

        if not moniker:
            return None
        try:
            if not member.moniker_exists(args, moniker, pool=pool):
                return None
        except ValueError:
            io.echo(
                f"MonikerOnlyCredentialProvider: invalid moniker {moniker!r}",
                level="warning",
            )
            raise
        except Exception as e:
            io.echo(
                f"MonikerOnlyCredentialProvider: DB error for {moniker!r}: {e}",
                level="error",
            )
            return None

        is_sysop = bool(member.issysop(args, moniker=moniker, pool=pool))
        return MemberInfo(moniker=moniker, is_sysop=is_sysop, balance=None)


class PasswordCredentialProvider:
    """Real credential check: moniker must exist AND password must match.

    Delegates to bbsengine6.member.checkpassword (PostgreSQL `crypt(plain,
    salt) = stored` match). The provider deliberately returns None on
    any check failure so the wire response cannot be used to enumerate
    which monikers exist.
    """

    def authenticate(
        self,
        args: Any,
        moniker: str,
        password: str,
        *,
        pool: Any = None,
    ) -> Optional[MemberInfo]:
        from bbsengine6 import io, member

        if not moniker or not password:
            return None
        try:
            ok = member.checkpassword(
                args, password, membermoniker=moniker, pool=pool
            )
        except Exception as e:
            io.echo(
                f"PasswordCredentialProvider: DB error for {moniker!r}: {e}",
                level="error",
            )
            return None
        if not ok:
            return None
        is_sysop = bool(member.issysop(args, moniker=moniker, pool=pool))
        balance: Optional[int] = None
        try:
            balance = member.getcredits(args, membermoniker=moniker, pool=pool)
        except Exception:
            balance = None
        return MemberInfo(moniker=moniker, is_sysop=is_sysop, balance=balance)


def get_provider(name: str) -> CredentialProvider:
    """Resolve a `--credential-provider` string to a provider instance."""
    name = (name or "").strip().lower()
    if name in ("", "password", "default"):
        return PasswordCredentialProvider()
    if name in ("moniker", "moniker-only", "moniker_only"):
        return MonikerOnlyCredentialProvider()
    raise ValueError(
        f"unknown credential provider {name!r}; "
        f"expected 'password' or 'moniker-only'"
    )
