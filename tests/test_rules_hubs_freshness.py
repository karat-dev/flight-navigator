import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.flight import (
    BaggageChoice,
    PassengerConfig,
    RouteSearchRequest,
    SeatChoice,
)
from app.services.airline_rules import (
    FORBIDDEN_AMOUNTS,
    allowance_kg,
    baggage_coverage,
    get_airline,
    load_airline_rules,
    validate_rules,
)
from app.services.aviasales_client import AviasalesDataClient
from app.services.flight_search import FlightSearchService
from app.services.freshness import classify_freshness
from app.services.pricing import calculate_basket_price


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def test_airline_rules_are_sourced_and_not_invented():
    payload = load_airline_rules()
    validate_rules(payload)
    airlines = {item["iata"]: item for item in payload["airlines"]}
    assert set(airlines) == {"TK", "EY", "CZ", "3U", "2S", "DP", "W5", "U6"}

    for airline in airlines.values():
        for family in airline["fare_families"]:
            for key in ("checked_baggage", "cabin_baggage", "seat"):
                item = family[key]
                assert item["confidence"] in {"confirmed", "approx", "unknown"}
                assert item["source_url"].startswith("https://")
                assert item["checked_at"] == "2026-10-12"
        assert airline["add_bag"]["confidence"] in {"confirmed", "approx", "unknown"}
        assert airline["seat_price"]["source_url"].startswith("https://")

    assert airlines["W5"]["excluded_from_recommendations"] is True
    assert "OFAC" in airlines["W5"]["exclusion_reason"]
    assert allowance_kg(airlines["W5"]["fare_families"][0]["checked_baggage"]) is None
    assert airlines["W5"]["add_bag"]["amount"] is None

    assert airlines["CZ"]["add_bag"]["amount"] is None
    assert airlines["CZ"]["fare_families"][0]["checked_baggage"]["total_weight_kg"] is None
    assert airlines["TK"]["add_bag"]["amount"] is None
    assert airlines["U6"]["add_bag"]["amount"] is None
    assert airlines["2S"]["add_bag"]["amount"] is None

    blob = json.dumps(payload)
    for amount in FORBIDDEN_AMOUNTS:
        assert f'"amount": {amount}' not in blob
        assert f'"amount": {float(amount)}' not in blob

    assert baggage_coverage(airlines["3U"], 23) == "included"
    assert baggage_coverage(airlines["3U"], 30) == "mixed"
    assert baggage_coverage(airlines["TK"], 20) == "unknown"
    assert baggage_coverage(airlines["DP"], 30) == "not_included"
    assert baggage_coverage(airlines["EY"], 20) == "mixed"


def test_validate_rules_rejects_a_value_without_a_source():
    with pytest.raises(Exception):
        validate_rules({
            "collection_checked_at": "2026-10-12",
            "airlines": [{
                "iata": "ZZ",
                "fare_families": [],
                "add_bag": {"confidence": "confirmed", "checked_at": "2026-10-12"},
            }],
        })


def test_full_basket_uses_rules_and_states_unknown():
    included = calculate_basket_price(
        fare_per_pax=29184,
        num_pax=1,
        num_legs=1,
        carrier="3U",
        baggage=BaggageChoice.CHECKED_23KG,
        seats=SeatChoice.NONE,
    )
    assert included.full_basket_rub == 29184
    assert included.rules_confidence == "confirmed"
    assert included.confidence.value == "partial"
    assert any("подтверждено" in flag for flag in included.flags)
    assert included.addons[0].included_in_rub_total is True

    pobeda = calculate_basket_price(
        fare_per_pax=9384,
        num_pax=1,
        num_legs=1,
        carrier="DP",
        baggage=BaggageChoice.CHECKED_30KG,
        seats=SeatChoice.NONE,
        separate_tickets=True,
    )
    assert pobeda.full_basket_rub == 9384
    assert pobeda.confidence.value == "partial"
    assert pobeda.rules_confidence == "approx"
    addon = next(item for item in pobeda.addons if item.kind == "baggage")
    assert addon.amount == 80
    assert addon.currency == "EUR"
    assert addon.included_in_rub_total is False
    assert "не переводим" in pobeda.basket_display

    etihad = calculate_basket_price(
        fare_per_pax=40000,
        num_pax=1,
        num_legs=1,
        carrier="EY",
        baggage=BaggageChoice.CHECKED_20KG,
        seats=SeatChoice.NONE,
    )
    assert etihad.full_basket_rub == 40000
    assert etihad.confidence.value == "unknown"
    reference = next(item for item in etihad.addons if item.amount == 260)
    assert reference.currency == "USD"
    assert reference.included_in_rub_total is False
    assert "неизвест" in etihad.basket_display.lower()

    china_southern = calculate_basket_price(
        fare_per_pax=26508,
        num_pax=1,
        num_legs=1,
        carrier="CZ",
        baggage=BaggageChoice.CHECKED_23KG,
        seats=SeatChoice.NONE,
    )
    assert china_southern.full_basket_rub == 26508
    assert china_southern.confidence.value == "unknown"
    assert all(item.amount is None for item in china_southern.addons)
    assert "не угадываем" in china_southern.basket_display


def test_freshness_uses_found_at_only():
    fresh, fresh_note = classify_freshness("2026-10-12T10:00:00+00:00", NOW)
    stale, stale_note = classify_freshness("2026-09-01T00:00:00+00:00", NOW)
    missing, missing_note = classify_freshness(None, NOW)
    assert fresh == "fresh"
    assert "found_at" in fresh_note
    assert stale == "possibly_stale"
    assert missing == "possibly_stale"
    assert "нет found_at" in missing_note


def _service(handler, token="super-secret-token-xyz"):
    client = AviasalesDataClient(token=token)
    client.fetch_route_prices = AsyncMock(side_effect=handler)
    return FlightSearchService(client=client, now_fn=lambda: NOW), client


def _row(**kwargs):
    base = {
        "depart_date": "2026-12-05",
        "value": 26508,
        "airline": "CZ",
        "flight_number": "342",
        "number_of_changes": 1,
        "gate": "Авиасейлс",
        "duration": 1380,
        "origin_airport": "SVO",
        "destination_airport": "HKT",
        "found_at": "2026-10-12T10:00:00+00:00",
        "api_version": "v3",
        "itinerary_airports": ["SVO", "PKX", "HKT"],
    }
    base.update(kwargs)
    return base


@pytest.mark.asyncio
async def test_neighbour_date_is_flagged_and_verify_link_uses_requested_date():
    calls = []

    async def handler(origin, destination, target_date, currency="rub"):
        calls.append((origin, destination))
        if origin == "MOW" and destination == "HKT":
            return [
                _row(
                    depart_date="2026-12-06",
                    value=22774,
                    found_at="2026-10-12T11:00:00+00:00",
                    gate="Trip.com",
                    airline=None,
                    flight_number=None,
                ),
                _row(
                    depart_date="2026-12-05",
                    value=26508,
                    found_at="2026-09-01T00:00:00+00:00",
                ),
            ], "v3"
        return [], "v3"

    service, client = _service(handler)
    result = await service.search_routes(RouteSearchRequest(
        departure_date=date(2026, 12, 5),
        origin="MOW",
        hub="HKG",
        destination="HKT",
        baggage=BaggageChoice.CHECKED_23KG,
        seats=SeatChoice.NONE,
        passengers=PassengerConfig(adults=1),
    ))
    assert ("MOW", "IST") not in calls
    assert ("MOW", "HKG") in calls
    packages = result.tabs.recommend + result.tabs.risk
    neighbour = next(package for package in packages if package.segments[0].date == "2026-12-06")
    exact = next(package for package in packages if package.segments[0].date == "2026-12-05" and package.segments[0].carrier == "CZ")
    assert neighbour.date_status.value == "neighbour"
    assert exact.date_status.value == "exact"
    assert any("соседняя дата" in flag for flag in neighbour.flags)
    assert neighbour.requested_date == "2026-12-05"
    assert "0512" in neighbour.verify_price.url
    assert "0612" not in neighbour.verify_price.url
    assert "marker=765617" in neighbour.verify_price.url
    assert neighbour.verify_price.title == "проверить актуальную цену"
    assert neighbour.freshness.value == "fresh"
    assert exact.freshness.value == "possibly_stale"
    assert result.tabs.recommend[0].date_status.value == "exact"
    blob = result.model_dump_json()
    assert "super-secret-token-xyz" not in blob
    assert client.token not in blob


@pytest.mark.asyncio
async def test_mahan_is_excluded_and_beijing_hub_is_used():
    calls = []

    async def handler(origin, destination, target_date, currency="rub"):
        calls.append((origin, destination))
        if (origin, destination) == ("MOW", "HKT"):
            return [
                _row(airline="W5", flight_number="99", value=10000, gate="Авиасейлс", depart_date="2026-12-05"),
                _row(airline="CZ", value=26508, depart_date="2026-12-05"),
            ], "v3"
        if (origin, destination) == ("MOW", "PEK"):
            return [_row(
                airline="U6",
                flight_number="273",
                origin_airport="DME",
                destination_airport="PEK",
                value=20550,
                number_of_changes=1,
                depart_date="2026-12-05",
                itinerary_airports=["DME", "SVX", "PEK"],
            )], "v3"
        if (origin, destination) == ("PKX", "HKT"):
            return [_row(
                airline="3U",
                flight_number="3888",
                origin_airport="PKX",
                destination_airport="HKT",
                value=9000,
                number_of_changes=0,
                depart_date="2026-12-05",
                itinerary_airports=["PKX", "HKT"],
                duration=400,
            )], "v3"
        return [], "v3"

    service, _client = _service(handler)
    result = await service.search_routes(RouteSearchRequest(
        departure_date=date(2026, 12, 5),
        origin="MOW",
        hubs=["BJS"],
        destination="HKT",
        baggage=BaggageChoice.CHECKED_23KG,
        seats=SeatChoice.NONE,
        passengers=PassengerConfig(adults=1),
    ))
    assert not any(code in {"IST", "SAW"} for _origin, code in calls)
    assert not any(code in {"IST", "SAW"} for code, _dest in calls)
    assert ("MOW", "PEK") in calls
    assert ("MOW", "PKX") in calls or ("MOW", "BJS") in calls
    packages = result.tabs.recommend + result.tabs.risk
    carriers = [seg.carrier for package in packages for seg in package.segments]
    assert "W5" not in carriers
    assert any("OFAC" in item for item in result.exclusions)
    changed = [
        package for package in packages
        if package.connection.airport_change and package.connection.hub == "BJS"
    ]
    assert changed
    assert any("PEK" in flag and "PKX" in flag for flag in changed[0].flags)
    assert any("минимального времени нет" in flag for flag in changed[0].flags)
    assert not any("закладывайте от 6 часов" in flag for package in changed for flag in package.flags)
    assert result.comparison_note
    assert "сквозной" in result.comparison_note


@pytest.mark.asyncio
async def test_ist_airport_change_keeps_six_hour_floor_and_risk_tab():
    async def handler(origin, destination, target_date, currency="rub"):
        if (origin, destination) == ("MOW", "HKT"):
            return [_row(
                depart_date="2026-11-10",
                value=68000,
                airline="TK",
                origin_airport="VKO",
                destination_airport="HKT",
                itinerary_airports=["VKO", "IST", "HKT"],
            )], "v3"
        if (origin, destination) == ("MOW", "SAW"):
            return [_row(
                depart_date="2026-11-10",
                value=18000,
                airline="PC",
                origin_airport="SVO",
                destination_airport="SAW",
                number_of_changes=0,
                duration=200,
                itinerary_airports=["SVO", "SAW"],
            )], "v3"
        if (origin, destination) == ("IST", "HKT"):
            return [_row(
                depart_date="2026-11-10",
                value=45000,
                airline="TK",
                origin_airport="IST",
                destination_airport="HKT",
                number_of_changes=0,
                duration=500,
                itinerary_airports=["IST", "HKT"],
            )], "v3"
        return [], "v3"

    service, _client = _service(handler)
    result = await service.search_routes(RouteSearchRequest(
        departure_date=date(2026, 11, 10),
        origin="MOW",
        hub="IST",
        destination="HKT",
        baggage=BaggageChoice.NONE,
        seats=SeatChoice.NONE,
        passengers=PassengerConfig(adults=1),
    ))
    changed = [
        package for package in result.tabs.risk
        if package.connection.airport_change
    ]
    assert changed
    assert any("IST ↔ SAW" in flag and "6 часов" in flag for flag in changed[0].flags)
    assert result.empty_reasons == [] or all("TK 414" not in reason for reason in result.empty_reasons)


@pytest.mark.asyncio
async def test_return_leg_and_unknown_hub_do_not_invent_flights():
    calls = []

    async def handler(origin, destination, target_date, currency="rub"):
        calls.append((origin, destination, target_date.isoformat()))
        if (origin, destination) == ("HKG", "MOW") and target_date.isoformat() == "2026-12-19":
            return [_row(
                depart_date="2026-12-19",
                value=25984,
                airline="CZ",
                origin_airport="HKG",
                destination_airport="SVO",
                itinerary_airports=["HKG", "URC", "SVO"],
            )], "v3"
        if (origin, destination) == ("HKG", "PVG"):
            return [_row(
                depart_date="2026-12-19",
                value=6381,
                airline="3U",
                origin_airport="HKG",
                destination_airport="PVG",
                number_of_changes=0,
                duration=180,
                itinerary_airports=["HKG", "PVG"],
            )], "v3"
        if (origin, destination) == ("SHA", "MOW"):
            return [_row(
                depart_date="2026-12-19",
                value=18563,
                airline="S7",
                origin_airport="SHA",
                destination_airport="SVO",
                number_of_changes=1,
                duration=700,
                itinerary_airports=["SHA", "SVO"],
            )], "v3"
        return [], "v3"

    service, _client = _service(handler)
    result = await service.search_routes(RouteSearchRequest(
        departure_date=date(2026, 12, 5),
        return_date=date(2026, 12, 19),
        origin="MOW",
        hubs=["SHA", "DXB"],
        destination="HKG",
        baggage=BaggageChoice.CHECKED_20KG,
        seats=SeatChoice.NONE,
        passengers=PassengerConfig(adults=1),
    ))
    assert any(item == ("HKG", "MOW", "2026-12-19") for item in calls)
    assert any(item[0] == "HKG" and item[1] in {"SHA", "PVG"} for item in calls)
    assert any(item[1] == "MOW" and item[0] in {"SHA", "PVG"} for item in calls)
    assert not any("IST" in pair[:2] for pair in calls)
    assert result.return_tabs is not None
    return_packages = result.return_tabs.recommend + result.return_tabs.risk
    assert return_packages
    assert all(package.requested_date == "2026-12-19" for package in return_packages)
    assert "1912" in result.return_manual_search_url
    assert "marker=765617" in result.manual_search_url
    assert "MOW0512HKG19121" in result.manual_search_url
    assert any(reason.startswith("unknown_hub_DXB") for reason in result.empty_reasons)
    assert "DXB" not in json.dumps([
        [seg.carrier, seg.from_airport, seg.to_airport]
        for package in return_packages
        for seg in package.segments
    ])


@pytest.mark.asyncio
async def test_rules_endpoint_and_health_do_not_leak_the_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        rules = await http.get("/api/v1/airline-rules/3U")
        missing = await http.get("/api/v1/airline-rules/ZZ")
        hubs = await http.get("/api/v1/hubs")
    assert rules.status_code == 200
    body = rules.json()
    assert body["fare_families"][0]["checked_baggage"]["confidence"] == "confirmed"
    assert body["fare_families"][0]["checked_baggage"]["source_url"].startswith("https://")
    assert missing.status_code == 404
    codes = {item["code"] for item in hubs.json()}
    assert {"IST", "BJS", "SHA", "HKG"} <= codes

    token = "super-secret-token-xyz"

    class FakeResponse:
        status_code = 401
        text = f"bad token {token}"

        def json(self):
            return {"success": False}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            assert params["token"] == token
            return FakeResponse()

    client = AviasalesDataClient(token=token)
    with patch("app.services.aviasales_client.httpx.AsyncClient", FakeClient):
        status = await client.check_health()
    assert token not in json.dumps(status)
    assert "Неверный или неактивный" in status["message"]


def test_airline_lookup_helpers():
    assert get_airline("w5")["iata"] == "W5"
    assert get_airline("ZZ") is None
