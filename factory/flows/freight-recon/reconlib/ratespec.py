"""Declarative rate specs and the one engine that evaluates them.

A rate spec is what a contract-extraction worker produces from contract
prose (schema: definitions/rate-spec.json). It is DATA, never code: a small
closed expression language that this module interprets. Every pricing
decision in the pipeline goes through ``price_shipment`` — the extraction
cross-check, the price stage, and the arithmetic re-check after
adjudication — so there is exactly one implementation of "what the contract
says this costs".

Expressions (all numbers may be JSON numbers or decimal strings):
    {"const": n}
    {"field": name}            shipment field, e.g. distance_km, billed_weight_kg
    {"qty": id}                a derived_quantities entry
    {"charge": id}             amount of an earlier charge (0 if it did not apply)
    {"add": [e, ...]}  {"mul": [e, ...]}  {"max": [e, ...]}  {"min": [e, ...]}
    {"percent": n, "of": e}    n% of e
    {"tiered": {"on": e, "tiers": [tier, ...]}}
        tier: {"gt"|"gte": n (optional), "lt"|"lte": n (optional),
               "value": e} | {..., "out_of_card": true}
        No matching tier -> undetermined (tier_gap). Several -> spec error.

Conditions:
    {"field": name, "op": "eq"|"ne"|"in"|"contains"|"gt"|"gte"|"lt"|"lte", "value": v}
    {"qty": id, "op": ..., "value": v}
    {"all": [c, ...]}  {"any": [c, ...]}  {"not": c}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .money import q, to_decimal


class SpecError(ValueError):
    """The spec itself is malformed or self-contradictory (not the shipment)."""


class Undetermined(Exception):
    """The contract does not determine an amount for this shipment."""

    def __init__(self, reason: str, detail: str, clause: str | None = None):
        super().__init__(detail)
        self.reason, self.detail, self.clause = reason, detail, clause


@dataclass
class PriceResult:
    status: str                      # "determined" | "undetermined"
    amount: Decimal | None
    charges: list[dict] = field(default_factory=list)   # [{id, label, amount, clause}]
    reasons: list[dict] = field(default_factory=list)   # [{reason, detail, clause}]
    clauses: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"status": self.status, "amount": self.amount, "charges": self.charges,
                "reasons": self.reasons, "clauses": self.clauses}


SHIPMENT_FIELDS = {"distance_km", "billed_weight_kg", "declared_value_inr", "service_level",
                   "special_handling", "ship_date", "delivery_status", "carrier"}


def _num(v) -> Decimal:
    try:
        return to_decimal(v)
    except Exception as e:
        raise SpecError(f"not a number: {v!r}") from e


class _Ctx:
    def __init__(self, spec: dict, shipment: dict):
        self.spec, self.shipment = spec, shipment
        self.qty: dict[str, Decimal] = {}
        self.charge_amounts: dict[str, Decimal] = {}
        self.current_clause: str | None = None

    def field(self, name: str):
        if name not in SHIPMENT_FIELDS:
            raise SpecError(f"unknown shipment field {name!r}")
        if name not in self.shipment:
            raise SpecError(f"shipment has no field {name!r}")
        v = self.shipment[name]
        return v if isinstance(v, (str, list)) else _num(v)


def eval_expr(e: Any, ctx: _Ctx) -> Decimal:
    if not isinstance(e, dict) or len(e) == 0:
        raise SpecError(f"expression must be a non-empty object: {e!r}")
    if "const" in e:
        return _num(e["const"])
    if "field" in e:
        v = ctx.field(e["field"])
        if not isinstance(v, Decimal):
            raise SpecError(f"field {e['field']!r} is not numeric")
        return v
    if "qty" in e:
        if e["qty"] not in ctx.qty:
            raise SpecError(f"derived quantity {e['qty']!r} used before it is defined")
        return ctx.qty[e["qty"]]
    if "charge" in e:
        cid = e["charge"]
        if cid not in {c["id"] for c in ctx.spec["charges"]}:
            raise SpecError(f"reference to unknown charge {cid!r}")
        return ctx.charge_amounts.get(cid, Decimal(0))
    for op, fn in (("add", sum), ("mul", _product), ("max", max), ("min", min)):
        if op in e:
            args = e[op]
            if not isinstance(args, list) or not args:
                raise SpecError(f"{op} needs a non-empty list")
            vals = [eval_expr(a, ctx) for a in args]
            return fn(vals, Decimal(0)) if op == "add" else fn(vals)
    if "percent" in e:
        return _num(e["percent"]) / Decimal(100) * eval_expr(e["of"], ctx)
    if "tiered" in e:
        return _eval_tiered(e["tiered"], ctx)
    raise SpecError(f"unknown expression {sorted(e)}")


def _product(vals):
    out = Decimal(1)
    for v in vals:
        out *= v
    return out


def _tier_matches(t: dict, x: Decimal) -> bool:
    if "gt" in t and not x > _num(t["gt"]):
        return False
    if "gte" in t and not x >= _num(t["gte"]):
        return False
    if "lt" in t and not x < _num(t["lt"]):
        return False
    if "lte" in t and not x <= _num(t["lte"]):
        return False
    return True


def _eval_tiered(spec: dict, ctx: _Ctx) -> Decimal:
    x = eval_expr(spec["on"], ctx)
    hits = [t for t in spec["tiers"] if _tier_matches(t, x)]
    if len(hits) > 1:
        raise SpecError(f"overlapping tiers for value {x}: {hits}")
    if not hits:
        raise Undetermined("tier_gap", f"no rate tier covers {x}", ctx.current_clause)
    t = hits[0]
    if t.get("out_of_card"):
        raise Undetermined("out_of_card", f"value {x} falls in a tier the contract excludes from the rate card",
                           t.get("clause") or ctx.current_clause)
    return eval_expr(t["value"], ctx)


def eval_cond(c: Any, ctx: _Ctx) -> bool:
    if c is None:
        return True
    if "all" in c:
        return all(eval_cond(x, ctx) for x in c["all"])
    if "any" in c:
        return any(eval_cond(x, ctx) for x in c["any"])
    if "not" in c:
        return not eval_cond(c["not"], ctx)
    if "field" in c:
        left = ctx.field(c["field"])
    elif "qty" in c:
        left = ctx.qty[c["qty"]]
    else:
        raise SpecError(f"condition needs field/qty/all/any/not: {c!r}")
    op, right = c.get("op"), c.get("value")
    if op == "eq":
        return left == (right if isinstance(left, str) else _num(right))
    if op == "ne":
        return left != (right if isinstance(left, str) else _num(right))
    if op == "in":
        return left in right
    if op == "contains":
        return right in left
    if op in ("gt", "gte", "lt", "lte"):
        l, r = _num(left), _num(right)
        return {"gt": l > r, "gte": l >= r, "lt": l < r, "lte": l <= r}[op]
    raise SpecError(f"unknown condition op {op!r}")


def price_shipment(spec: dict, shipment: dict) -> PriceResult:
    """Expected line amount for one shipment under ``spec``.

    Returns ``undetermined`` (never raises) when the contract does not settle
    the amount; raises SpecError only when the spec itself is broken."""
    ctx = _Ctx(spec, shipment)
    reasons: list[dict] = []

    term = spec.get("term") or {}
    d = shipment.get("ship_date")
    if d and ((term.get("from") and d < term["from"]) or (term.get("to") and d > term["to"])):
        reasons.append({"reason": "outside_term", "clause": term.get("clause"),
                        "detail": f"ship date {d} outside contract term {term.get('from')}..{term.get('to')}"})
    offered = (spec.get("services_offered") or {}).get("values")
    if offered is not None and shipment.get("service_level") not in offered:
        reasons.append({"reason": "service_not_offered", "clause": spec["services_offered"].get("clause"),
                        "detail": f"service level {shipment.get('service_level')!r} is not offered under this contract"})

    for dq in spec.get("derived_quantities", []):
        ctx.current_clause = dq.get("clause")
        try:
            ctx.qty[dq["id"]] = eval_expr(dq["expr"], ctx)
        except Undetermined as u:
            # Everything downstream may depend on this quantity, so the shipment
            # cannot be priced: report undetermined rather than evaluating on.
            reasons.append({"reason": u.reason, "detail": f"{dq['id']}: {u.detail}", "clause": u.clause})
            return PriceResult("undetermined", None, [], reasons, [])

    for amb in spec.get("ambiguities", []):
        if amb.get("when") is not None:
            try:
                hit = eval_cond(amb["when"], ctx)
            except KeyError:
                hit = False
            if hit:
                reasons.append({"reason": "ambiguity", "clause": amb.get("clause"),
                                "detail": f"{amb['id']}: {amb['description']}"})

    charges, clauses = [], []
    for ch in spec["charges"]:
        ctx.current_clause = ch.get("clause")
        if not eval_cond(ch.get("when"), ctx):
            continue
        try:
            amt = eval_expr(ch["amount"], ctx)
        except Undetermined as u:
            reasons.append({"reason": u.reason, "detail": f"{ch['id']}: {u.detail}", "clause": u.clause})
            continue
        ctx.charge_amounts[ch["id"]] = amt
        charges.append({"id": ch["id"], "label": ch.get("label", ch["id"]), "amount": amt,
                        "clause": ch.get("clause")})
        if ch.get("clause") and ch["clause"] not in clauses:
            clauses.append(ch["clause"])

    if reasons:
        return PriceResult("undetermined", None, charges, reasons, clauses)
    total = q(sum((c["amount"] for c in charges), Decimal(0)))
    return PriceResult("determined", total, [{**c, "amount": q(c["amount"])} for c in charges], [], clauses)


def invoice_adjustments(spec: dict, metrics: dict) -> list[dict]:
    """Invoice-level adjustments that apply given per-invoice metrics.

    metrics: {"consignments_in_calendar_month": int, "invoice_line_total": Decimal, ...}
    Returns [{id, clause, amount (signed, applied to the invoice total)}]."""
    out = []
    for adj in spec.get("invoice_adjustments", []):
        cond = adj.get("when")
        if cond is not None:
            name = cond["metric"]
            if name not in metrics:
                raise SpecError(f"invoice adjustment {adj['id']} needs metric {name!r}")
            left, right = _num(metrics[name]), _num(cond["value"])
            ok = {"gt": left > right, "gte": left >= right, "lt": left < right,
                  "lte": left <= right, "eq": left == right}[cond["op"]]
            if not ok:
                continue
        if adj["kind"] != "percent_of_invoice_total":
            raise SpecError(f"unknown invoice adjustment kind {adj['kind']!r}")
        amount = q(_num(adj["percent"]) / Decimal(100) * _num(metrics["invoice_line_total"]))
        out.append({"id": adj["id"], "label": adj.get("label", adj["id"]), "clause": adj.get("clause"),
                    "amount": amount})
    return out


def check_spec(spec: dict) -> list[str]:
    """Static checks beyond the JSON schema: dangling references, overlapping
    tiers at their boundaries, unknown fields. Returns a list of problems."""
    problems = []
    ids = [c["id"] for c in spec.get("charges", [])]
    if len(ids) != len(set(ids)):
        problems.append(f"duplicate charge ids: {ids}")
    problems += _check_refs(spec)
    probe = {"distance_km": 100, "billed_weight_kg": 100, "declared_value_inr": 1000,
             "service_level": "standard", "special_handling": [], "ship_date": "2000-01-01",
             "delivery_status": "delivered", "carrier": spec.get("carrier")}
    for x in sorted(_boundaries(spec)) or [Decimal(1)]:
        for fld in ("distance_km", "billed_weight_kg"):
            try:
                price_shipment(spec, {**probe, fld: x})
            except SpecError as e:
                problems.append(f"{fld}={x}: {e}")
            except Exception as e:  # anything else is also a broken spec
                problems.append(f"{fld}={x}: {type(e).__name__}: {e}")
    return sorted(set(problems))


def _boundaries(obj) -> set[Decimal]:
    """Every tier/condition boundary number mentioned anywhere in the spec."""
    out: set[Decimal] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("gt", "gte", "lt", "lte") and not isinstance(v, (dict, list)):
                out.add(_num(v))
            elif k == "value" and not isinstance(v, (dict, list, str)) and v is not None:
                out.add(_num(v))
            else:
                out |= _boundaries(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _boundaries(v)
    return out


def _check_refs(spec: dict) -> list[str]:
    """Walk every expression and condition, whether or not it would apply to
    any particular shipment: a charge may only reference charges defined
    BEFORE it, quantities must be defined before use, fields must be known."""
    problems: list[str] = []
    qtys: set[str] = set()
    earlier: set[str] = set()

    def walk(node, where: str):
        if isinstance(node, dict):
            if "qty" in node and isinstance(node["qty"], str) and node["qty"] not in qtys:
                problems.append(f"{where}: quantity {node['qty']!r} is not defined before use")
            if "charge" in node and isinstance(node["charge"], str) and node["charge"] not in earlier:
                problems.append(f"{where}: charge {node['charge']!r} is not defined earlier in the charge list")
            if "field" in node and isinstance(node["field"], str) and node["field"] not in SHIPMENT_FIELDS:
                problems.append(f"{where}: unknown shipment field {node['field']!r}")
            for v in node.values():
                walk(v, where)
        elif isinstance(node, list):
            for v in node:
                walk(v, where)

    for dq in spec.get("derived_quantities", []):
        walk(dq.get("expr"), f"derived_quantities.{dq.get('id')}")
        qtys.add(dq.get("id"))
    for amb in spec.get("ambiguities", []):
        walk(amb.get("when"), f"ambiguities.{amb.get('id')}")
    for ch in spec.get("charges", []):
        walk(ch.get("when"), f"charges.{ch.get('id')}.when")
        walk(ch.get("amount"), f"charges.{ch.get('id')}.amount")
        earlier.add(ch.get("id"))
    return problems
