"""Exact money handling. All amounts are Decimal internally and rounded to
paise (2 dp, half-up) exactly once, at line level."""
from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISE = Decimal("0.01")
_AMOUNT_RE = re.compile(r"^-?\d{1,3}(,\d{2,3})*(\.\d+)?$|^-?\d+(\.\d+)?$")


class MoneyParseError(ValueError):
    pass


def parse_amount(raw: str) -> Decimal:
    """Parse a printed amount such as '1,81,637.60', '181,637.60' or '-496.80'.

    Only digits, one optional leading minus, grouping commas and a decimal
    point are accepted; anything else raises rather than guessing."""
    s = raw.strip()
    if not _AMOUNT_RE.match(s):
        raise MoneyParseError(f"not a recognisable amount: {raw!r}")
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation as e:  # pragma: no cover - regex guards this
        raise MoneyParseError(f"not a recognisable amount: {raw!r}") from e


def to_decimal(value) -> Decimal:
    """Convert a JSON number / string to Decimal without float artefacts."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise MoneyParseError(f"not a number: {value!r}")
    return Decimal(str(value))


def q(value: Decimal) -> Decimal:
    """Round to paise, half-up."""
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


def same_money(a: Decimal, b: Decimal) -> bool:
    return q(a) == q(b)


def dumps(obj, **kw) -> str:
    """Serialise with Decimals as JSON numbers. Values are paise-quantized
    first; a 2-dp decimal below 2**53/100 round-trips exactly through a
    binary float's shortest repr, so no precision is lost on reload."""
    return json.dumps(_prepare(obj), **kw)


def _prepare(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _prepare(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_prepare(v) for v in obj]
    return obj


def loads(text: str):
    """Load JSON with every non-integer number as Decimal."""
    return json.loads(text, parse_float=Decimal)
