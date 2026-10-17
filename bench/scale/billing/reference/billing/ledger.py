"""A double-entry-ish ledger: entries per account, balances, reversals.

Balances are cached per account because reports ask for them constantly;
anything that changes an account's entries must drop that account's cache.
"""
from dataclasses import dataclass
from decimal import Decimal

from .money import to_money


@dataclass(frozen=True)
class Entry:
    id: int
    account: str
    amount: Decimal          # positive is a charge, negative a payment
    memo: str = ""
    reverses: int | None = None


class Ledger:
    def __init__(self):
        self._entries: list[Entry] = []
        self._balances: dict[str, Decimal] = {}

    def post(self, account: str, amount, memo: str = "") -> Entry:
        entry = Entry(len(self._entries) + 1, account, to_money(amount), memo)
        self._entries.append(entry)
        self._balances.pop(account, None)
        return entry

    def reverse(self, entry_id: int, memo: str = "reversal") -> Entry:
        """Post the opposite of an entry. An entry can be reversed once."""
        original = self.get(entry_id)
        if any(e.reverses == entry_id for e in self._entries):
            raise ValueError(f"entry {entry_id} is already reversed")
        entry = Entry(len(self._entries) + 1, original.account,
                      -original.amount, memo, reverses=entry_id)
        self._entries.append(entry)
        self._balances.pop(original.account, None)
        return entry

    def get(self, entry_id: int) -> Entry:
        for e in self._entries:
            if e.id == entry_id:
                return e
        raise KeyError(entry_id)

    def balance(self, account: str) -> Decimal:
        if account not in self._balances:
            self._balances[account] = sum(
                (e.amount for e in self._entries if e.account == account),
                Decimal("0.00"))
        return self._balances[account]

    def entries(self, account: str | None = None) -> list[Entry]:
        return [e for e in self._entries if account is None or e.account == account]

    def accounts(self) -> list[str]:
        return sorted({e.account for e in self._entries})
