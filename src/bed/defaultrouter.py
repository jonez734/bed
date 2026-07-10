from typing import Any

from bbsengine6.bank.api.handler import BankServiceHandler, SessionManager


class DefaultRouter:
    """Default router for BED that starts bank and auth services."""

    def __init__(self, args: Any):
        self.args = args
        self.sessions = SessionManager()
        self.bank_service = BankServiceHandler(args, self.sessions)

    def register_all(self, server: Any) -> None:
        server.register_service(self.bank_service, [
            "bank_balance", "bank_add", "bank_remove",
            "bank_transfer_request", "bank_transfer_approve", "bank_transfer_reject",
            "bank_pending", "bank_history", "bank_list_all",
        ])

    def unregister_session(self, session_id: int) -> None:
        self.sessions.unregister_session(session_id)
