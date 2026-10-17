"""Advance shipping notices (ASNs) from suppliers.

An ASN line is 'SKU, QTY[, LOT]'. A line that cannot be read is an error that
names the line: receiving zero of something by accident hides a short
shipment until the shelf is empty.
"""


def parse_line(text: str) -> dict:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2 or not parts[0]:
        raise ValueError(f"ASN line needs 'SKU, QTY': {text!r}")
    try:
        qty = int(parts[1])
    except ValueError as e:
        raise ValueError(f"ASN quantity is not a whole number: {text!r}") from e
    if qty <= 0:
        raise ValueError(f"ASN quantity must be positive: {text!r}")
    return {"sku": parts[0], "qty": qty, "lot": parts[2] if len(parts) > 2 else None}


def parse_asn(text: str) -> list[dict]:
    """Every non-blank, non-comment line. Raises on the first bad one, with
    its line number; the broad except is there to add the number, and it
    re-raises."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            out.append(parse_line(line))
        except Exception as e:
            raise ValueError(f"line {n}: {e}") from e
    return out


def totals(lines: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for l in lines:
        out[l["sku"]] = out.get(l["sku"], 0) + l["qty"]
    return out
