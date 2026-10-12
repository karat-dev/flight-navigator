#!/usr/bin/env python3
"""Search Duffel test-mode offers for a fixed set of one-way routes.

Reads DUFFEL_TOKEN from the environment. The token must be a Duffel test-mode
token (prefix duffel_test_). The token is never printed.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

API_BASE = "https://api.duffel.com"
DUFFEL_VERSION = "v2"
CHEAPEST_N = 5
SUPPLIER_TIMEOUT_MS = 55000
CREATE_TIMEOUT_S = 90
READ_TIMEOUT_S = 45
MAX_ATTEMPTS = 4

ROUTES = [
    ("SVO", "HKT", "2026-11-10"),
    ("SVO", "IST", "2026-11-10"),
    ("IST", "HKT", "2026-11-10"),
    ("SVO", "HKT", "2026-11-24"),
    ("SVO", "HKG", "2026-12-05"),
    ("HKG", "SVO", "2026-12-19"),
    ("HKG", "PVG", "2026-12-19"),
    ("PVG", "SVO", "2026-12-19"),
]

_TOKEN = ""
_SECRET_RE = re.compile(r"duffel_(?:test|live)_[A-Za-z0-9._\-]+")
_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


def redact(text: str) -> str:
    if not text:
        return ""
    out = text
    if _TOKEN:
        out = out.replace(_TOKEN, "[REDACTED]")
    out = _SECRET_RE.sub("[REDACTED]", out)
    out = _BEARER_RE.sub("Bearer [REDACTED]", out)
    return out


class DuffelError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        self.status = status
        self.code = code
        super().__init__(redact(message))


def require_test_token() -> str:
    global _TOKEN
    token = os.environ.get("DUFFEL_TOKEN", "")
    if not token.startswith("duffel_test_"):
        print(
            "DUFFEL_TOKEN is missing or is not a Duffel test-mode token "
            "(expected prefix duffel_test_).",
            file=sys.stderr,
        )
        sys.exit(2)
    _TOKEN = token
    return token


def _decode_body(raw: bytes, content_encoding: str | None) -> str:
    if content_encoding and "gzip" in content_encoding.lower():
        raw = gzip.decompress(raw)
    elif raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _error_message(status: int, payload: str) -> tuple[str, str | None]:
    code = None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("errors"), list):
        parts = []
        for item in parsed["errors"]:
            if not isinstance(item, dict):
                continue
            code = code or item.get("code")
            title = item.get("title") or ""
            message = item.get("message") or ""
            bits = [bit for bit in (title, message, item.get("code")) if bit]
            if bits:
                parts.append(": ".join(bits))
        if parts:
            return redact(f"HTTP {status}: {'; '.join(parts)}"), code
    snippet = redact(payload.strip().replace("\n", " "))[:400]
    return redact(f"HTTP {status}: {snippet}"), code


class DuffelClient:
    def __init__(self, token: str):
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict | None = None,
        body: dict | None = None,
        timeout: int,
    ) -> dict:
        url = API_BASE + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Duffel-Version": DUFFEL_VERSION,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "flight-navigator-duffel-refcheck/1.0",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                    text = _decode_body(raw, response.headers.get("Content-Encoding"))
                    return json.loads(text)
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                text = _decode_body(raw, exc.headers.get("Content-Encoding"))
                message, code = _error_message(exc.code, text)
                if exc.code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
                    delay = _retry_delay(exc.headers.get("Retry-After"), attempt)
                    print(
                        f"Duffel HTTP {exc.code} on {method} {path}; "
                        f"retry {attempt}/{MAX_ATTEMPTS - 1} in {delay:.0f}s",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    last_error = DuffelError(exc.code, message, code)
                    continue
                raise DuffelError(exc.code, message, code) from None
            except urllib.error.URLError as exc:
                reason = redact(str(exc.reason))
                if attempt < MAX_ATTEMPTS:
                    delay = _retry_delay(None, attempt)
                    print(
                        f"Duffel network error on {method} {path}; "
                        f"retry {attempt}/{MAX_ATTEMPTS - 1} in {delay:.0f}s",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    last_error = DuffelError(0, reason)
                    continue
                raise DuffelError(0, reason) from None
        if last_error:
            raise last_error
        raise DuffelError(0, "request failed")


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(2.0 ** attempt, 30.0)


# This script only runs with a duffel_test_ token. Every amount it reports is
# sandbox data, including offers owned by a real airline.
TEST_MODE_PRICE_LABEL = "test-mode (not a real price)"


def airline_kind(airline: dict | None) -> str:
    """Classify the carrier code only. This does not describe price or baggage."""
    airline = airline or {}
    iata = str(airline.get("iata_code") or "").upper()
    name = str(airline.get("name") or "")
    if iata == "ZZ" or "duffel airways" in name.lower():
        return "duffel_airways"
    if not iata and not name:
        return "unknown"
    return "real_airline"


def carrier_code_label(kind: str) -> str:
    """'real airline' means the IATA code exists. It does not mean a live fare."""
    if kind == "duffel_airways":
        return "Duffel Airways"
    if kind == "real_airline":
        return "real airline"
    return "unknown carrier code"


def _decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _money(amount: str | None, currency: str | None) -> str:
    if amount is None and not currency:
        return "n/a"
    return f"{amount} {currency or ''}".strip()


def _place_iata(place: dict | None) -> str:
    if not isinstance(place, dict):
        return "?"
    return place.get("iata_code") or place.get("iata_city_code") or "?"


def _carrier(carrier: dict | None) -> dict:
    carrier = carrier or {}
    return {
        "iata_code": carrier.get("iata_code"),
        "name": carrier.get("name"),
        "kind": airline_kind(carrier),
    }


def _flight_code(carrier: dict | None, number: str | None) -> str:
    iata = (carrier or {}).get("iata_code") or "?"
    if not number:
        return iata
    return f"{iata}{number}"


def _iso_between(start: str | None, end: str | None) -> str | None:
    if not start or not end:
        return None
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except ValueError:
        return None
    seconds = int(delta.total_seconds())
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    body = f"PT{hours}H{minutes}M"
    if secs:
        body += f"{secs}S"
    return sign + body


def _human_duration(iso: str | None) -> str:
    if not iso:
        return "n/a"
    match = re.fullmatch(r"(-)?PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not match:
        return iso
    sign, hours, minutes, seconds = match.groups()
    parts = []
    if hours:
        parts.append(f"{int(hours)}h")
    if minutes:
        parts.append(f"{int(minutes)}m")
    if seconds:
        parts.append(f"{int(seconds)}s")
    if not parts:
        parts.append("0m")
    return ("-" if sign else "") + " ".join(parts)


def _condition(node: dict | None) -> dict:
    if not isinstance(node, dict):
        return {"present": False, "allowed": None, "penalty_amount": None, "penalty_currency": None}
    return {
        "present": True,
        "allowed": node.get("allowed"),
        "penalty_amount": node.get("penalty_amount"),
        "penalty_currency": node.get("penalty_currency"),
    }


def _condition_text(cond: dict) -> str:
    if not cond.get("present"):
        return "not provided"
    allowed = cond.get("allowed")
    if allowed is True:
        penalty = _money(cond.get("penalty_amount"), cond.get("penalty_currency"))
        if cond.get("penalty_amount") in (None, "", "0.00", "0"):
            return "allowed"
        return f"allowed, penalty {penalty}"
    if allowed is False:
        return "not allowed"
    return "unknown"


def _sum_bags(baggages: list | None) -> dict:
    checked = 0
    carry_on = 0
    for bag in baggages or []:
        if not isinstance(bag, dict):
            continue
        quantity = bag.get("quantity") or 0
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 0
        bag_type = bag.get("type")
        if bag_type == "checked":
            checked += quantity
        elif bag_type == "carry_on":
            carry_on += quantity
    return {"checked": checked, "carry_on": carry_on}


def _segment_view(segment: dict, passengers_by_id: dict) -> dict:
    passengers = []
    for pax in segment.get("passengers") or []:
        if not isinstance(pax, dict):
            continue
        passenger_id = pax.get("passenger_id")
        known = passengers_by_id.get(passenger_id) or {}
        passengers.append(
            {
                "passenger_id": passenger_id,
                "type": known.get("type"),
                "cabin_class": pax.get("cabin_class"),
                "cabin_class_marketing_name": pax.get("cabin_class_marketing_name"),
                "fare_basis_code": pax.get("fare_basis_code"),
                "included_baggage": _sum_bags(pax.get("baggages")),
            }
        )
    marketing = _carrier(segment.get("marketing_carrier"))
    operating = _carrier(segment.get("operating_carrier"))
    stops = []
    for stop in segment.get("stops") or []:
        if not isinstance(stop, dict):
            continue
        airport = stop.get("airport") if isinstance(stop.get("airport"), dict) else {}
        stops.append(
            {
                "airport": airport.get("iata_code"),
                "arriving_at": stop.get("arriving_at"),
                "departing_at": stop.get("departing_at"),
                "duration": stop.get("duration"),
            }
        )
    return {
        "id": segment.get("id"),
        "origin": _place_iata(segment.get("origin")),
        "destination": _place_iata(segment.get("destination")),
        "departing_at": segment.get("departing_at"),
        "arriving_at": segment.get("arriving_at"),
        "duration": segment.get("duration"),
        "origin_terminal": segment.get("origin_terminal"),
        "destination_terminal": segment.get("destination_terminal"),
        "marketing_carrier": marketing,
        "marketing_carrier_flight_number": segment.get("marketing_carrier_flight_number"),
        "marketing_flight": _flight_code(
            segment.get("marketing_carrier"),
            segment.get("marketing_carrier_flight_number"),
        ),
        "operating_carrier": operating,
        "operating_carrier_flight_number": segment.get("operating_carrier_flight_number"),
        "operating_flight": _flight_code(
            segment.get("operating_carrier"),
            segment.get("operating_carrier_flight_number"),
        ),
        "stops": stops,
        "passengers": passengers,
    }


def _layovers(segments: list[dict]) -> list[dict]:
    layovers = []
    for prev, nxt in zip(segments, segments[1:]):
        duration = _iso_between(prev.get("arriving_at"), nxt.get("departing_at"))
        layovers.append(
            {
                "airport": prev.get("destination"),
                "arriving_at": prev.get("arriving_at"),
                "departing_at": nxt.get("departing_at"),
                "duration": duration,
            }
        )
    return layovers


def _service_view(service: dict) -> dict:
    metadata = service.get("metadata") if isinstance(service.get("metadata"), dict) else {}
    return {
        "id": service.get("id"),
        "type": service.get("type"),
        "total_amount": service.get("total_amount"),
        "total_currency": service.get("total_currency"),
        "maximum_quantity": service.get("maximum_quantity"),
        "passenger_ids": service.get("passenger_ids") or [],
        "segment_ids": service.get("segment_ids") or [],
        "bag_type": metadata.get("type"),
        "maximum_weight_kg": metadata.get("maximum_weight_kg"),
        "maximum_length_cm": metadata.get("maximum_length_cm"),
        "maximum_height_cm": metadata.get("maximum_height_cm"),
        "maximum_depth_cm": metadata.get("maximum_depth_cm"),
    }


def summarize_services(services: list | None) -> dict:
    extra_bags = []
    seat_services = []
    other = []
    for service in services or []:
        if not isinstance(service, dict):
            continue
        view = _service_view(service)
        if service.get("type") == "baggage":
            extra_bags.append(view)
        elif service.get("type") == "seat":
            seat_services.append(view)
        else:
            other.append(view)
    return {
        "extra_bags": {
            "available": bool(extra_bags),
            "services": extra_bags,
        },
        "seat_services_on_offer": seat_services,
        "other_services": other,
    }


def summarize_seat_maps(maps: list | None, error: str | None = None) -> dict:
    if error:
        return {
            "available": False,
            "error": error,
            "segments": [],
            "note": "Seat maps are a separate Duffel endpoint. This offer did not return them.",
        }
    segments = []
    any_selectable = False
    for seat_map in maps or []:
        if not isinstance(seat_map, dict):
            continue
        prices: dict[tuple[str | None, str | None], int] = {}
        selectable = 0
        shown = 0
        for cabin in seat_map.get("cabins") or []:
            if not isinstance(cabin, dict):
                continue
            for row in cabin.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                for section in row.get("sections") or []:
                    if not isinstance(section, dict):
                        continue
                    for element in section.get("elements") or []:
                        if not isinstance(element, dict) or element.get("type") != "seat":
                            continue
                        shown += 1
                        available_services = element.get("available_services") or []
                        if not available_services:
                            continue
                        selectable += 1
                        any_selectable = True
                        for svc in available_services:
                            if not isinstance(svc, dict):
                                continue
                            key = (svc.get("total_currency"), svc.get("total_amount"))
                            prices[key] = prices.get(key, 0) + 1
        distinct = []
        for (currency, amount), count in prices.items():
            distinct.append(
                {
                    "total_currency": currency,
                    "total_amount": amount,
                    "seats": count,
                    "_sort": _decimal(amount) if amount is not None else Decimal("999999"),
                }
            )
        distinct.sort(key=lambda item: (item["total_currency"] or "", item["_sort"]))
        priced = [(item["total_amount"], item["_sort"]) for item in distinct if item["total_amount"] is not None]
        min_amount = min(priced, key=lambda item: item[1])[0] if priced else None
        max_amount = max(priced, key=lambda item: item[1])[0] if priced else None
        for item in distinct:
            item.pop("_sort", None)
        segments.append(
            {
                "segment_id": seat_map.get("segment_id"),
                "slice_id": seat_map.get("slice_id"),
                "seat_map_id": seat_map.get("id"),
                "seats_shown": shown,
                "selectable_seats": selectable,
                "min_amount": min_amount,
                "max_amount": max_amount,
                "currencies": sorted({item["total_currency"] for item in distinct if item["total_currency"]}),
                "prices": distinct[:30],
                "omitted_price_levels": max(0, len(distinct) - 30),
            }
        )
    if not segments:
        note = "No seat map was returned for this offer."
    elif any_selectable:
        note = "Selectable seats and prices come from the seat map. Duffel does not list seats on offer available_services."
    else:
        note = "A seat map was returned, but no seat had a purchasable service."
    return {
        "available": any_selectable,
        "segments": segments,
        "note": note,
    }


def project_offer(offer: dict, services: dict, seat_selection: dict, search_total: dict | None = None) -> dict:
    owner = _carrier(offer.get("owner"))
    passengers_by_id = {}
    for passenger in offer.get("passengers") or []:
        if isinstance(passenger, dict) and passenger.get("id"):
            passengers_by_id[passenger["id"]] = passenger
    slices = []
    marketing_flights = []
    for slc in offer.get("slices") or []:
        if not isinstance(slc, dict):
            continue
        segments = [_segment_view(seg, passengers_by_id) for seg in (slc.get("segments") or []) if isinstance(seg, dict)]
        for segment in segments:
            marketing_flights.append(segment["marketing_flight"])
        conditions = slc.get("conditions") if isinstance(slc.get("conditions"), dict) else {}
        slices.append(
            {
                "id": slc.get("id"),
                "origin": _place_iata(slc.get("origin")),
                "destination": _place_iata(slc.get("destination")),
                "duration": slc.get("duration"),
                "fare_brand_name": slc.get("fare_brand_name"),
                "segments": segments,
                "layovers": _layovers(segments),
                "conditions": {
                    "change_before_departure": _condition(conditions.get("change_before_departure")),
                    "advance_seat_selection": conditions.get("advance_seat_selection"),
                },
            }
        )
    conditions = offer.get("conditions") if isinstance(offer.get("conditions"), dict) else {}
    carrier_kinds = {owner["kind"]}
    for slc in slices:
        for segment in slc["segments"]:
            carrier_kinds.add(segment["marketing_carrier"]["kind"])
    refreshed = {
        "total_amount": offer.get("total_amount"),
        "total_currency": offer.get("total_currency"),
    }
    price_changed = False
    if search_total and (
        search_total.get("total_amount") != refreshed["total_amount"]
        or search_total.get("total_currency") != refreshed["total_currency"]
    ):
        price_changed = True
    return {
        "id": offer.get("id"),
        "live_mode": offer.get("live_mode"),
        "expires_at": offer.get("expires_at"),
        "total_amount": refreshed["total_amount"],
        "total_currency": refreshed["total_currency"],
        "search_total_amount": None if not price_changed else (search_total or {}).get("total_amount"),
        "search_total_currency": None if not price_changed else (search_total or {}).get("total_currency"),
        "price_changed_on_refresh": price_changed,
        "owner": owner,
        "price_label": TEST_MODE_PRICE_LABEL,
        "carrier_code_kind": owner["kind"],
        "carrier_code_label": carrier_code_label(owner["kind"]),
        "marketing_flights": marketing_flights,
        "contains_duffel_airways_marketing_carrier": "duffel_airways" in carrier_kinds,
        "slices": slices,
        "conditions": {
            "refund_before_departure": _condition(conditions.get("refund_before_departure")),
            "change_before_departure": _condition(conditions.get("change_before_departure")),
        },
        "extra_bags": services["extra_bags"],
        "seat_services_on_offer": services["seat_services_on_offer"],
        "other_services": services["other_services"],
        "seat_selection": seat_selection,
    }


def create_offer_request(client: DuffelClient, origin: str, destination: str, departure_date: str) -> dict:
    payload = client.request(
        "POST",
        "/air/offer_requests",
        query={"return_offers": "false", "supplier_timeout": str(SUPPLIER_TIMEOUT_MS)},
        body={
            "data": {
                "slices": [
                    {
                        "origin": origin,
                        "destination": destination,
                        "departure_date": departure_date,
                    }
                ],
                "passengers": [{"type": "adult"}],
                "cabin_class": "economy",
            }
        },
        timeout=CREATE_TIMEOUT_S,
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("id"):
        raise DuffelError(0, "offer request response did not include an id")
    return data


def list_cheapest(client: DuffelClient, offer_request_id: str) -> list[dict]:
    payload = client.request(
        "GET",
        "/air/offers",
        query={
            "offer_request_id": offer_request_id,
            "sort": "total_amount",
            "limit": str(CHEAPEST_N),
        },
        timeout=READ_TIMEOUT_S,
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise DuffelError(0, "offer list response did not include data")
    return [offer for offer in data if isinstance(offer, dict)][:CHEAPEST_N]


def load_priced_offer(client: DuffelClient, offer_id: str) -> dict:
    payload = client.request(
        "GET",
        f"/air/offers/{urllib.parse.quote(offer_id)}",
        query={"return_available_services": "true"},
        timeout=READ_TIMEOUT_S,
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise DuffelError(0, "offer response did not include data")
    return data


def load_seat_maps(client: DuffelClient, offer_id: str) -> dict:
    try:
        payload = client.request(
            "GET",
            "/air/seat_maps",
            query={"offer_id": offer_id},
            timeout=READ_TIMEOUT_S,
        )
    except DuffelError as exc:
        return summarize_seat_maps([], error=str(exc))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return summarize_seat_maps([], error="seat map response did not include a list")
    return summarize_seat_maps(data)


def check_route(client: DuffelClient, origin: str, destination: str, departure_date: str) -> dict:
    label = f"{origin}-{destination} {departure_date}"
    print(f"search {label}", file=sys.stderr)
    created = create_offer_request(client, origin, destination, departure_date)
    offer_request_id = created["id"]
    live_mode = created.get("live_mode")
    print(f"  offer_request {offer_request_id} live_mode={live_mode}", file=sys.stderr)
    cheapest = list_cheapest(client, offer_request_id)
    projected = []
    for index, summary in enumerate(cheapest, start=1):
        offer_id = summary.get("id")
        search_total = {
            "total_amount": summary.get("total_amount"),
            "total_currency": summary.get("total_currency"),
        }
        offer = summary
        services = summarize_services(summary.get("available_services"))
        seat_selection = summarize_seat_maps([], error="priced offer was not loaded")
        detail_error = None
        if offer_id:
            try:
                offer = load_priced_offer(client, offer_id)
                services = summarize_services(offer.get("available_services"))
            except DuffelError as exc:
                detail_error = str(exc)
                print(f"  offer {index} detail failed: {detail_error}", file=sys.stderr)
            try:
                seat_selection = load_seat_maps(client, offer_id)
            except DuffelError as exc:
                seat_selection = summarize_seat_maps([], error=str(exc))
        view = project_offer(offer, services, seat_selection, search_total)
        view["rank"] = index
        view["detail_error"] = detail_error
        projected.append(view)
        print(
            f"  #{index} {_money(view['total_amount'], view['total_currency'])} "
            f"{view['owner'].get('iata_code')} {view['price_label']} "
            f"carrier={view['carrier_code_label']}",
            file=sys.stderr,
        )
    return {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "offer_request_id": offer_request_id,
        "live_mode": live_mode,
        "offers_reported": len(projected),
        "offers": projected,
        "error": None,
    }


def _md(text: object) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _bag_line(service: dict) -> str:
    weight = service.get("maximum_weight_kg")
    weight_text = f", max {weight} kg" if weight not in (None, "") else ""
    bag_type = service.get("bag_type") or "baggage"
    qty = service.get("maximum_quantity")
    qty_text = f", max quantity {qty}" if qty not in (None, "") else ""
    return (
        f"{bag_type} {_money(service.get('total_amount'), service.get('total_currency'))}"
        f"{weight_text}{qty_text}"
    )


def _seat_line(seat_selection: dict) -> str:
    if seat_selection.get("error") and not seat_selection.get("segments"):
        return f"unavailable ({seat_selection['error']})"
    if not seat_selection.get("available"):
        return seat_selection.get("note") or "not available"
    parts = []
    for segment in seat_selection.get("segments") or []:
        if not segment.get("selectable_seats"):
            continue
        parts.append(
            f"{segment.get('selectable_seats')} seats "
            f"{segment.get('min_amount')}-{segment.get('max_amount')} "
            f"{', '.join(segment.get('currencies') or [])}".strip()
        )
    return "; ".join(parts) if parts else "available"


def render_markdown(report: dict) -> str:
    lines = [
        "# Duffel reference check",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "Search: Duffel API v2 (`Duffel-Version: v2`), one adult, economy, one-way. "
        "Each route keeps the five cheapest offers by `total_amount`.",
        "",
        "**Every offer is test-mode (not a real price).** This run uses a `duffel_test_` token. "
        "Duffel generates every offer in test mode, including offers owned by a real airline. "
        "Prices, included baggage, extra bags, seat selection, and refund or change conditions are sandbox data.",
        "",
        "**Real airline** means only that the owner IATA code is a real airline code, not Duffel Airways (`ZZ`). "
        "It does not mean the price or the baggage are live.",
        "",
        "## Cheapest offer on each route",
        "",
        "| Route | Date | Cheapest | Owner | Price | Carrier code | Flights |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for route in report["routes"]:
        route_name = f"{route['origin']}-{route['destination']}"
        if route.get("error"):
            lines.append(
                f"| {_md(route_name)} | {_md(route['departure_date'])} | failed |  |  |  | {_md(route['error'])} |"
            )
            continue
        if not route["offers"]:
            lines.append(
                f"| {_md(route_name)} | {_md(route['departure_date'])} | no offers |  |  |  |  |"
            )
            continue
        top = route["offers"][0]
        owner = top["owner"]
        owner_text = f"{owner.get('name') or '?'} ({owner.get('iata_code') or '?'})"
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(route_name),
                    _md(route["departure_date"]),
                    _md(_money(top["total_amount"], top["total_currency"])),
                    _md(owner_text),
                    _md(top["price_label"]),
                    _md(top["carrier_code_label"]),
                    _md(", ".join(top["marketing_flights"])),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Routes", ""])
    for route in report["routes"]:
        title = f"{route['origin']} → {route['destination']} on {route['departure_date']}"
        lines.append(f"### {title}")
        lines.append("")
        if route.get("error"):
            lines.append(f"Search failed: {route['error']}")
            lines.append("")
            continue
        lines.append(
            f"Offer request `{route.get('offer_request_id')}` "
            f"(live_mode={route.get('live_mode')}). "
            f"Showing {route['offers_reported']} cheapest offer(s)."
        )
        lines.append("")
        if not route["offers"]:
            lines.append("No offers were returned.")
            lines.append("")
            continue
        for offer in route["offers"]:
            owner = offer["owner"]
            owner_text = f"{owner.get('name') or '?'} ({owner.get('iata_code') or '?'})"
            lines.append(
                f"#### {offer['rank']}. {_money(offer['total_amount'], offer['total_currency'])} "
                f"— {owner_text} — {offer['price_label']}"
            )
            lines.append("")
            lines.append(f"- Offer id: `{offer['id']}`")
            lines.append(f"- Expires: {offer.get('expires_at') or 'n/a'}")
            if offer.get("price_changed_on_refresh"):
                lines.append(
                    "- Price at search was "
                    f"{_money(offer.get('search_total_amount'), offer.get('search_total_currency'))}; "
                    "the amount above is from the priced offer."
                )
            if offer.get("detail_error"):
                lines.append(f"- Priced-offer refresh failed: {offer['detail_error']}")
            lines.append(f"- Price: {offer['price_label']}")
            lines.append(f"- Owner airline: {owner_text}")
            lines.append(f"- Carrier code: {offer['carrier_code_label']}")
            if offer.get("contains_duffel_airways_marketing_carrier") and offer["carrier_code_kind"] != "duffel_airways":
                lines.append(
                    "- A marketing carrier code on this offer is Duffel Airways (`ZZ`). "
                    "The price is still test-mode (not a real price)."
                )
            lines.append(f"- Marketing flights: {', '.join(offer['marketing_flights']) or 'n/a'}")
            lines.append("- Segments:")
            for slc in offer["slices"]:
                brand = f" ({slc['fare_brand_name']})" if slc.get("fare_brand_name") else ""
                lines.append(
                    f"  - Slice {slc['origin']} → {slc['destination']}, "
                    f"duration {_human_duration(slc.get('duration'))}{brand}"
                )
                segments = slc["segments"]
                layovers = slc["layovers"]
                for index, segment in enumerate(segments):
                    operating = ""
                    if segment["operating_flight"] != segment["marketing_flight"]:
                        operating = f", operated by {segment['operating_flight']}"
                        op_name = segment["operating_carrier"].get("name")
                        if op_name:
                            operating += f" {op_name}"
                    lines.append(
                        f"    - {segment['marketing_flight']} "
                        f"{segment['origin']} {segment.get('departing_at')} → "
                        f"{segment['destination']} {segment.get('arriving_at')} "
                        f"({_human_duration(segment.get('duration'))}){operating}"
                    )
                    for stop in segment["stops"]:
                        lines.append(
                            f"      - Stop {stop.get('airport') or '?'} "
                            f"{stop.get('arriving_at')} → {stop.get('departing_at')} "
                            f"(duration {_human_duration(stop.get('duration'))})"
                        )
                    if not segment["passengers"]:
                        lines.append("      - Included baggage: not provided")
                    for passenger in segment["passengers"]:
                        bags = passenger["included_baggage"]
                        pax = passenger.get("type") or "passenger"
                        lines.append(
                            f"      - Included baggage for {pax} `{passenger.get('passenger_id')}`: "
                            f"checked {bags['checked']}, carry-on {bags['carry_on']}"
                        )
                    if index < len(layovers):
                        layover = layovers[index]
                        lines.append(
                            f"    - Layover at {layover.get('airport')}: "
                            f"{_human_duration(layover.get('duration'))} "
                            f"({layover.get('duration') or 'n/a'})"
                        )
            extra = offer["extra_bags"]
            if extra["available"]:
                lines.append("- Extra bags as services:")
                for service in extra["services"]:
                    lines.append(f"    - {_bag_line(service)}")
            else:
                lines.append("- Extra bags as services: not available on this offer")
            if offer["seat_services_on_offer"]:
                lines.append("- Seat services listed on the offer:")
                for service in offer["seat_services_on_offer"]:
                    lines.append(
                        f"    - {_money(service.get('total_amount'), service.get('total_currency'))}"
                    )
            lines.append(f"- Seat selection: {_seat_line(offer['seat_selection'])}")
            for segment in offer["seat_selection"].get("segments") or []:
                if not segment.get("prices"):
                    continue
                price_bits = [
                    f"{item['total_amount']} {item['total_currency']} × {item['seats']}"
                    for item in segment["prices"]
                ]
                extra_levels = segment.get("omitted_price_levels") or 0
                suffix = f" (+{extra_levels} more price levels)" if extra_levels else ""
                lines.append(
                    f"    - Segment `{segment.get('segment_id')}`: "
                    + ", ".join(price_bits)
                    + suffix
                )
            if offer["other_services"]:
                lines.append("- Other services:")
                for service in offer["other_services"]:
                    lines.append(
                        f"    - {service.get('type')}: "
                        f"{_money(service.get('total_amount'), service.get('total_currency'))}"
                    )
            refund = offer["conditions"]["refund_before_departure"]
            change = offer["conditions"]["change_before_departure"]
            lines.append(f"- Refund before departure: {_condition_text(refund)}")
            lines.append(f"- Change before departure: {_condition_text(change)}")
            for slc in offer["slices"]:
                slice_change = slc["conditions"]["change_before_departure"]
                advance = slc["conditions"].get("advance_seat_selection")
                if slice_change.get("present") or advance is not None:
                    lines.append(
                        f"- Slice {slc['origin']}→{slc['destination']} conditions: "
                        f"change {_condition_text(slice_change)}; "
                        f"advance seat selection {advance if advance is not None else 'not provided'}"
                    )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_report(routes: list[dict]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": {
            "version_header": DUFFEL_VERSION,
            "cabin_class": "economy",
            "passengers": [{"type": "adult"}],
            "supplier_timeout_ms": SUPPLIER_TIMEOUT_MS,
            "cheapest_n": CHEAPEST_N,
        },
        "notice": (
            "Every offer is test-mode (not a real price). A duffel_test_ token returns "
            "sandbox offers only, including offers owned by a real airline. Prices, baggage, "
            "and conditions are not live. 'real airline' means only that the owner IATA code "
            "exists and is not Duffel Airways (ZZ)."
        ),
        "routes": routes,
    }


def write_outputs(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "offers.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return md_path, json_path


def main() -> int:
    token = require_test_token()
    out_dir = Path(__file__).resolve().parent / "out"
    if len(sys.argv) > 1:
        if sys.argv[1] in ("-h", "--help"):
            print("Usage: refcheck.py [out_dir]", file=sys.stderr)
            return 0
        out_dir = Path(sys.argv[1])
    client = DuffelClient(token)
    routes = []
    successes = 0
    for origin, destination, departure_date in ROUTES:
        try:
            route = check_route(client, origin, destination, departure_date)
            successes += 1
        except DuffelError as exc:
            message = str(exc)
            print(f"FAILED {origin}-{destination} {departure_date}: {message}", file=sys.stderr)
            route = {
                "origin": origin,
                "destination": destination,
                "departure_date": departure_date,
                "offer_request_id": None,
                "live_mode": None,
                "offers_reported": 0,
                "offers": [],
                "error": message,
            }
            if exc.status in (401, 403):
                routes.append(route)
                report = build_report(routes)
                write_outputs(report, out_dir)
                print("Stopped because Duffel rejected the token.", file=sys.stderr)
                return 2
        routes.append(route)
    report = build_report(routes)
    md_path, json_path = write_outputs(report, out_dir)
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
    if successes == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
