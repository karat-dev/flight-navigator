"""Load airline fare rules. Every sourced value keeps url, date and confidence."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "airline_rules.json"

CONFIDENCE_LEVELS = frozenset({"confirmed", "approx", "unknown"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Amounts that showed up in research as unofficial, contradictory, or invented basket fillers.
# They must not be stored as chargeable amounts.
FORBIDDEN_AMOUNTS = frozenset({35, 45, 90, 490, 800, 900, 1000, 2500, 3900, 4200, 5800})


class AirlineRulesError(ValueError):
    pass


def _iter_dicts(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dicts(item)


def validate_rules(payload: dict) -> None:
    if not isinstance(payload, dict) or "airlines" not in payload:
        raise AirlineRulesError("airline rules payload has no airlines list")
    checked = payload.get("collection_checked_at")
    if not isinstance(checked, str) or not _DATE_RE.match(checked):
        raise AirlineRulesError("collection_checked_at must be YYYY-MM-DD")

    seen_iata = set()
    for airline in payload["airlines"]:
        iata = airline.get("iata")
        if not iata or iata in seen_iata:
            raise AirlineRulesError(f"bad or duplicate iata: {iata}")
        seen_iata.add(iata)
        if airline.get("excluded_from_recommendations") and not airline.get("exclusion_reason"):
            raise AirlineRulesError(f"{iata} is excluded without a reason")

    for obj in _iter_dicts(payload):
        if "confidence" not in obj:
            continue
        confidence = obj.get("confidence")
        if confidence not in CONFIDENCE_LEVELS:
            raise AirlineRulesError(f"confidence must be confirmed, approx or unknown: {obj}")
        source_url = obj.get("source_url")
        checked_at = obj.get("checked_at")
        if not isinstance(source_url, str) or not source_url.startswith("https://"):
            raise AirlineRulesError(f"sourced value missing https source_url: {obj}")
        if not isinstance(checked_at, str) or not _DATE_RE.match(checked_at):
            raise AirlineRulesError(f"sourced value missing checked_at: {obj}")
        amount = obj.get("amount")
        if amount is not None:
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise AirlineRulesError(f"amount is not a number: {obj}")
            if float(amount) in FORBIDDEN_AMOUNTS:
                raise AirlineRulesError(f"forbidden invented or rejected amount {amount}")
            if confidence == "unknown":
                raise AirlineRulesError(f"unknown confidence cannot carry an amount: {obj}")


@lru_cache(maxsize=1)
def load_airline_rules() -> dict:
    with DATA_PATH.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    validate_rules(payload)
    return payload


def get_airline(iata: Optional[str]) -> Optional[dict]:
    if not iata:
        return None
    code = iata.strip().upper()
    for airline in load_airline_rules()["airlines"]:
        if airline["iata"] == code:
            return airline
    return None


def exclusion_reason(iata: Optional[str]) -> Optional[str]:
    airline = get_airline(iata)
    if not airline or not airline.get("excluded_from_recommendations"):
        return None
    return airline.get("exclusion_reason")


def allowance_kg(baggage: dict) -> Optional[float]:
    """Known included kilograms, 0 when the fare states there is no checked bag, else None."""
    if not baggage or baggage.get("confidence") == "unknown":
        return None
    if baggage.get("pieces") == 0:
        return 0.0
    total = baggage.get("total_weight_kg")
    if total is not None:
        return float(total)
    pieces = baggage.get("pieces")
    each = baggage.get("weight_kg_each")
    if pieces is not None and each is not None:
        return float(pieces) * float(each)
    if each is not None and pieces is None:
        return float(each)
    return None


def baggage_coverage(airline: dict, requested_kg: float) -> str:
    """
    included — every known economy family covers the request and none are unknown.
    not_included — every known family falls short and none are unknown.
    mixed — some families cover the request and some do not.
    unknown — at least one family has no usable allowance, or there is nothing to read.
    """
    families = airline.get("fare_families") or []
    if not families:
        return "unknown"
    allowances = [allowance_kg(family.get("checked_baggage") or {}) for family in families]
    known = [kg for kg in allowances if kg is not None]
    unknown = [kg for kg in allowances if kg is None]
    if not known:
        return "unknown"
    covers = [kg >= requested_kg for kg in known]
    if unknown:
        if any(covers) and not all(covers):
            return "mixed"
        return "unknown"
    if all(covers):
        return "included"
    if not any(covers):
        return "not_included"
    return "mixed"


def worst_confidence(*levels: Optional[str]) -> str:
    rank = {"unknown": 2, "approx": 1, "confirmed": 0}
    known = [level for level in levels if level in rank]
    if not known:
        return "unknown"
    return max(known, key=lambda level: rank[level])
