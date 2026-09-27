import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.config import settings
from app.models.flight import (
    RouteSearchRequest,
    LayoverPreset,
    BaggageChoice,
    SeatChoice,
    PassengerConfig,
    PackageClass,
)
from app.services.flight_search import FlightSearchService
from app.services.aviasales_client import AviasalesDataClient, normalize_v2_item
from app.services.pricing import calculate_basket_price
from app.services.cta import build_dual_cta, build_aviasales_deep_link


@pytest.mark.asyncio
async def test_root():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "Flight Navigator" in data["message"]


@pytest.mark.asyncio
async def test_health_without_token():
    with patch.object(settings, "AVIASALES_TOKEN", None):
        from app.services import aviasales_client as ac_mod

        client = ac_mod.AviasalesDataClient(token=None)
        with patch("app.api.endpoints.aviasales_client", client):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.get("/api/v1/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["aviasales_data_api"]["configured"] is False
            assert "Не задан AVIASALES_TOKEN" in data["aviasales_data_api"]["message"]


@pytest.mark.asyncio
async def test_no_template_flights_in_search():
    """Ensure hardcoded flight templates (TK 414, SU 2136, etc.) do NOT appear in search."""
    with patch.object(settings, "AVIASALES_TOKEN", None):
        from app.services import aviasales_client as ac_mod

        client = ac_mod.AviasalesDataClient(token=None)
        svc = FlightSearchService(client=client)
        with patch("app.api.endpoints.flight_service", svc):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                res = await ac.get("/api/v1/routes/quick-search?departure_date=2026-11-10")
                assert res.status_code == 200
                data = res.json()
                assert len(data["tabs"]["recommend"]) == 0
                assert "Не задан AVIASALES_TOKEN" in data["empty_reasons"][0]
    serialized = str(data)
    assert "TK 414" not in serialized
    assert "SU 2136" not in serialized
    assert "PC 389" not in serialized


@pytest.mark.asyncio
async def test_search_with_mocked_data_api():
    """Test full search flow with mock Travelpayouts Data API responses."""
    mock_client = AviasalesDataClient(token="test_token_123")

    def row(**kwargs):
        base = {
            "depart_date": "2026-11-10",
            "value": 25000,
            "airline": "TK",
            "number_of_changes": 0,
            "gate": "Авиасейлс",
            "duration": 300,
            "origin_airport": "VKO",
            "destination_airport": "IST",
            "api_version": "v3",
            "itinerary_airports": ["VKO", "IST"],
        }
        base.update(kwargs)
        return base

    async def mock_fetch_route_prices(origin, destination, target_date, currency="rub"):
        if origin == "MOW" and destination == "IST":
            return [
                row(destination_airport="IST", value=25000),
                row(
                    origin_airport="SVO",
                    destination_airport="SAW",
                    value=18000,
                    airline="PC",
                    itinerary_airports=["SVO", "SAW"],
                ),
            ], "v3"
        if origin == "MOW" and destination == "SAW":
            return [row(destination_airport="SAW", airline="PC", value=18000)], "v3"
        if origin == "IST" and destination == "HKT":
            return [
                row(
                    origin_airport="IST",
                    destination_airport="HKT",
                    value=45000,
                    itinerary_airports=["IST", "HKT"],
                )
            ], "v3"
        if origin == "SAW" and destination == "HKT":
            return [
                row(
                    origin_airport="SAW",
                    destination_airport="HKT",
                    value=44000,
                    itinerary_airports=["SAW", "HKT"],
                )
            ], "v3"
        if origin == "MOW" and destination == "HKT":
            return [
                row(
                    origin_airport="VKO",
                    destination_airport="HKT",
                    value=68000,
                    number_of_changes=1,
                    itinerary_airports=["VKO", "IST", "HKT"],
                )
            ], "v3"
        return [], "v3"

    mock_client.fetch_route_prices = AsyncMock(side_effect=mock_fetch_route_prices)

    svc = FlightSearchService(client=mock_client)
    req = RouteSearchRequest(
        departure_date=date(2026, 11, 10),
        origin="MOW",
        hub="IST",
        destination="HKT",
        layover_preset=LayoverPreset.CALM,
        baggage=BaggageChoice.CHECKED_20KG,
        seats=SeatChoice.NONE,
        passengers=PassengerConfig(adults=1),
        include_neighbor_dates=True,
    )
    result = await svc.search_routes(req)

    assert len(result.tabs.recommend) > 0 or len(result.tabs.risk) > 0
    all_pkgs = result.tabs.recommend + result.tabs.risk

    unified = [p for p in all_pkgs if p.package_class == PackageClass.UNIFIED]
    assert len(unified) >= 1
    assert unified[0].connection.hub == "IST"
    assert "обычно сквозной" in " ".join(unified[0].flags)

    assemblies = [p for p in all_pkgs if p.package_class == PackageClass.ASSEMBLY]
    assert len(assemblies) >= 1
    assert any(p.connection.airport_change for p in assemblies)

    assert result.neighbor_dates is not None
    assert len(result.neighbor_dates) == 2


@pytest.mark.asyncio
async def test_basket_price_calculation():
    basket_tk, conf_tk, flags_tk = calculate_basket_price(
        fare_per_pax=30000,
        num_pax=2,
        num_legs=2,
        carrier="TK",
        baggage=BaggageChoice.CHECKED_20KG,
        seats=SeatChoice.YES,
    )
    assert basket_tk == 63600.0
    assert conf_tk.value == "exact"

    basket_pc, conf_pc, flags_pc = calculate_basket_price(
        fare_per_pax=20000,
        num_pax=1,
        num_legs=1,
        carrier="PC",
        baggage=BaggageChoice.CHECKED_20KG,
        seats=SeatChoice.NONE,
    )
    assert basket_pc == 23900.0
    assert conf_pc.value == "partial"

    basket_unk, conf_unk, flags_unk = calculate_basket_price(
        fare_per_pax=20000,
        num_pax=1,
        num_legs=1,
        carrier=None,
        baggage=BaggageChoice.CHECKED_20KG,
        seats=SeatChoice.NONE,
    )
    assert conf_unk.value == "unknown"
    assert any("Багаж не учтён" in f for f in flags_unk)


def test_dual_cta_rules():
    cta_assembly = build_dual_cta(
        package_class=PackageClass.ASSEMBLY,
        origin="MOW",
        destination="HKT",
        hub="IST",
        leg1_date="2026-11-10",
        leg2_date="2026-11-11",
        carrier="TK",
        passengers=1,
        full_basket_rub=70000,
    )
    assert "Авиасейлс" in cta_assembly.primary.provider_name
    assert "marker=765617" in cta_assembly.primary.url
    assert cta_assembly.secondary is not None

    cta_unified_airline_cheaper = build_dual_cta(
        package_class=PackageClass.UNIFIED,
        origin="MOW",
        destination="HKT",
        hub="IST",
        leg1_date="2026-11-10",
        leg2_date="2026-11-10",
        carrier="TK",
        passengers=1,
        full_basket_rub=80000,
        airline_price_rub=75000,
    )
    assert "TK" in cta_unified_airline_cheaper.primary.title or "Turkish" in cta_unified_airline_cheaper.primary.provider_name
    assert "Авиасейлс" in cta_unified_airline_cheaper.secondary.provider_name


@pytest.mark.asyncio
async def test_transit_guide_ist_and_hkt():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res_ist = await ac.get("/api/v1/guide/IST")
        res_hkt = await ac.get("/api/v1/guide/HKT")
    assert res_ist.status_code == 200
    assert res_hkt.status_code == 200

    data_ist = res_ist.json()
    assert "Ориентир, не проверка паспорта" in data_ist["visa_disclaimer"]
    assert data_ist["visa_info"]["source_url"] == "https://mid.ru/ru/maps/tr/"
    assert data_ist["airport_transfer"]["minimum_recommended_transfer_hours"] == 6.0


@pytest.mark.asyncio
async def test_airports_and_cities():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        airports_res = await ac.get("/api/v1/airports")
        cities_res = await ac.get("/api/v1/cities")

    assert airports_res.status_code == 200
    assert cities_res.status_code == 200

    airports = airports_res.json()
    airport_codes = [a["code"] for a in airports]
    assert "SVO" in airport_codes
    assert "IST" in airport_codes
    assert "SAW" in airport_codes
    assert "HKT" in airport_codes
