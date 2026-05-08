"""FNB (First National Bank) CSV statement parser."""

from __future__ import annotations
from pathlib import Path

from .base import BaseParser, ParseResult
from ..normalizer import parse_date, parse_amount, sniff_delimiter
from ..schema import make_transaction, make_error, make_statement_meta


# Required meta columns in the first header row
_REQUIRED_META_COLS = {
    "recreated statement", "date", "account nickname",
    "account number", "opening balance", "closing balance"
}

# Column name aliases (lowercased raw → internal)
_COL_ALIASES: dict[str, str] = {
    "recreated statement": "recreated_statement",
    "date": "statement_date",
    "account nickname": "account_nickname",
    "account number": "account_number",
    "opening balance": "opening_balance",
    "closing balance": "closing_balance",
    "debits": "total_debits",
    "credits": "total_credits",
    "number of debits": "num_debits",
    "number of credits": "num_credits",
    "effective date": "effective_date",
    "description": "description",
    "amount": "amount",
    "debit / credit": "debit_credit",
    "service fee": "service_fee",
    "balance": "balance",
    "reference": "reference",
}


class FNBParser(BaseParser):
    SOURCE = "fnb"

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        """Detect FNB flat format: first row contains 'recreated statement' + 'effective date' somewhere."""
        try:
            with path.open(encoding="utf-8-sig", errors="replace") as fh:
                lines = [line.strip().lower() for line in fh if line.strip()]
            if not lines:
                return False
            first_line = lines[0]
            has_meta = any(col in first_line for col in _REQUIRED_META_COLS)
            has_txn = any("effective date" in line for line in lines)
            return has_meta and has_txn
        except OSError:
            return False

    def parse(self, path: Path) -> ParseResult:
        try:
            raw_lines, _ = self._load(path)
        except (FileNotFoundError, ValueError) as exc:
            return self._file_error(path, str(exc))

        delimiter = self._sniff(raw_lines)
        rows = self._csv_rows(raw_lines, delimiter)

        if not rows:
            return ParseResult([], [make_error(0, "Empty file", self.SOURCE)], {}, str(path))

        # First row = meta + header
        meta_row = rows[0]
        meta = self._build_meta(meta_row)

        # Remaining rows = transactions
        txn_rows = rows[1:]
        transactions = []
        errors = []

        for i, row in enumerate(txn_rows, start=2):
            norm = self._normalise_cols(row)
            result = self._parse_row(norm, i)
            if "reason" in result:
                errors.append(result)
            else:
                transactions.append(result)

        return ParseResult(transactions, errors, meta, str(path))

    def _normalise_cols(self, row: dict[str, str]) -> dict[str, str]:
        """Remap raw column names using aliases."""
        return {
            _COL_ALIASES.get(k.strip().lower(), k.strip().lower()): v.strip()
            for k, v in row.items()
        }

    def _build_meta(self, header_row: dict[str, str]) -> dict:
        norm = self._normalise_cols(header_row)

        return make_statement_meta(
            account_number=norm.get("account_number", ""),
            account_nickname=norm.get("account_nickname", ""),
            statement_date=parse_date(norm.get("statement_date", "")) or norm.get("statement_date", ""),
            opening_balance=parse_amount(norm.get("opening_balance", "")),
            closing_balance=parse_amount(norm.get("closing_balance", "")),
            total_debits=parse_amount(norm.get("total_debits", "")),
            total_credits=parse_amount(norm.get("total_credits", "")),
        )

    def _parse_row(self, row: dict[str, str], row_num: int) -> dict:
        """Parse a single transaction row."""
        raw_date = row.get("effective_date", "")
        if not raw_date:
            return make_error(
                row=row_num,
                reason="Missing 'Effective Date'",
                raw=row,
                source=self.SOURCE,
            )

        date = parse_date(raw_date)
        if date is None:
            return make_error(
                row=row_num,
                reason=f"Unparseable date: '{raw_date}'",
                raw=row,
                source=self.SOURCE,
            )

        raw_amount = row.get("amount", "")
        amount = parse_amount(raw_amount)
        if amount is None:
            return make_error(
                row=row_num,
                reason=f"Unparseable amount: '{raw_amount}'",
                raw=row,
                source=self.SOURCE,
            )

        # Handle Debit/Credit indicator
        dc = row.get("debit_credit", "").strip().upper()
        if dc == "CR" and amount < 0:
            amount = abs(amount)

        service_fee = parse_amount(row.get("service_fee", "")) or 0.0
        balance = parse_amount(row.get("balance", ""))

        description = row.get("description", "").strip() or "UNKNOWN"
        reference = row.get("reference", "").strip()

        return make_transaction(
            date=date,
            description=description,
            reference=reference,
            amount=amount,
            service_fee=service_fee,
            balance=balance,
            source=self.SOURCE,
        )