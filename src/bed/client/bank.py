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

    async def get_balance(self, moniker: str) -> int:
        reply = await self._request(
            {"type": "bank_balance", "moniker": moniker}
        )
        return int((reply or {}).get("balance", 0))

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
