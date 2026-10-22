"""Empyre-shaped bank client (operates on per-account ``moniker``).

This is the empyre wire shape (``bank_balance`` returns ``balance``,
``bank_history`` returns ``transactions``, etc.). Casino has its own
``BedBankClient`` in ``casino/services/bank_client.py`` that operates
on ``table_moniker`` — same base class, different message shape.
"""

from __future__ import annotations

from typing import Any, Dict, List

from bed.client.messages import BedMessageClient


class BedBankClient(BedMessageClient):
    """Bank client for the empyre wire shape."""

    async def get_balance_result(self, moniker: str) -> Dict[str, Any]:
        """Return ``{"balance": int, "account": bool}`` for ``moniker``.

        ``account`` is False when bed has no bank account for the moniker. A zero
        balance with ``account`` True is a real zero, not a missing account.
        """
        reply = await self._request(
            {"type": "bank_balance", "moniker": moniker}
        )
        reply = reply or {}
        return {
            "balance": int(reply.get("balance", 0)),
            "account": bool(reply.get("account", False)),
        }

    async def get_balance(self, moniker: str) -> int:
        return (await self.get_balance_result(moniker))["balance"]

    async def add_funds(
        self, moniker: str, amount: int, description: str = "credit"
    ) -> Dict[str, Any]:
        return await self._request(
            {
                "type": "bank_add",
                "moniker": moniker,
                "amount": int(amount),
                "description": description,
            }
        )

    async def remove_funds(
        self, moniker: str, amount: int, description: str = "debit"
    ) -> Dict[str, Any]:
        return await self._request(
            {
                "type": "bank_remove",
                "moniker": moniker,
                "amount": int(amount),
                "description": description,
            }
        )

    async def get_history(
        self, moniker: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        reply = await self._request(
            {
                "type": "bank_history",
                "moniker": moniker,
                "limit": int(limit),
            }
        )
        return list((reply or {}).get("transactions", []))
