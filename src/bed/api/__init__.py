# bed/api/__init__.py
# Public re-exports for bed's API package.

from .auth import AuthService, TokenError, _decode_token, _encode_token
from .bank import BankService
from .credential_provider import (
    CredentialProvider,
    MonikerOnlyCredentialProvider,
    PasswordCredentialProvider,
    get_provider,
)
from .errors import (
    CODE_BAD_CREDENTIALS,
    CODE_BED_SECRET_INSECURE,
    CODE_DATABASE_ERROR,
    CODE_INSTANCE_MISMATCH,
    CODE_MISSING_CREDENTIALS,
    CODE_NOT_AUTHENTICATED,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_INVALID,
    CODE_TOKEN_REVOKED,
    error_envelope,
    not_authenticated,
    scrub_token,
)
from .handler import BaseService, SessionManager
from .message import NOTIFY_CHANNEL, MessageService
from .secret import (
    InsecureSecretError,
    SecretFormatError,
    load_or_create_secret,
)
from .session import SessionRegistry, SessionState
from .token_store import (
    DBTokenStore,
    InMemoryTokenStore,
    MemberInfo,
    TokenRecord,
    TokenStore,
)


__all__ = [
    "AuthService",
    "BankService",
    "BaseService",
    "CredentialProvider",
    "DBTokenStore",
    "InMemoryTokenStore",
    "InsecureSecretError",
    "MemberInfo",
    "MessageService",
    "MonikerOnlyCredentialProvider",
    "NOTIFY_CHANNEL",
    "PasswordCredentialProvider",
    "SecretFormatError",
    "SessionManager",
    "SessionRegistry",
    "SessionState",
    "TokenError",
    "TokenRecord",
    "TokenStore",
    "error_envelope",
    "get_provider",
    "load_or_create_secret",
    "not_authenticated",
    "scrub_token",
    "_decode_token",
    "_encode_token",
    "CODE_BAD_CREDENTIALS",
    "CODE_BED_SECRET_INSECURE",
    "CODE_DATABASE_ERROR",
    "CODE_INSTANCE_MISMATCH",
    "CODE_MISSING_CREDENTIALS",
    "CODE_NOT_AUTHENTICATED",
    "CODE_TOKEN_EXPIRED",
    "CODE_TOKEN_INVALID",
    "CODE_TOKEN_REVOKED",
]
