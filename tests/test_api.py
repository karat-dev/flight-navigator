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
from app.services.aviasales_client import AviasalesDataClient
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
    
    # Mock get_latest_prices
    async def mock_get_latest_prices(origin, destination, depart_date, currency="rub", limit=30, page=1):
        if origin == "MOW" and destination == "IST":
            return [
                {
                    "origin": "VKO",
                    "destination": "IST",
                    "depart_date": "2026-11-10",
                    "value": 25000,
                    "airline": "TK",
                    "number_of_changes": 0,
                    "created_at": "2026-09-23T10:00:00Z",
                },
                {
                    "origin": "SVO",
                    "destination": "SAW",
                    "depart_date": "2026-11-10",
                    "value": 18000,
                    "airline": "PC",
                    "number_of_changes": 0,
                    "created_at": "2026-09-23T10:00:00Z",
                },
            ]
        elif origin == "IST" and destination == "HKT":
            return [
                {
                    "origin": "IST",
                    "destination": "HKT",
                    "depart_date": "2026-11-10",
                    "value": 45000,
                    "airline": "TK",
                    "number_of_changes": 0,
                    "created_at": "2026-09-23T10:00:00Z",
                },
            ]
        elif origin == "MOW" and destination == "HKT":
            return [
                {
                    "origin": "VKO",
                    "destination": "HKT",
                    "depart_date": "2026-11-10",
                    "value": 68000,
                    "airline": "TK",
                    "number_of_changes": 1,
                    "created_at": "2026-09-23T10:00:00Z",
                }
            ]
        return []

    mock_client.get_latest_prices = AsyncMock(side_effect=mock_get_latest_prices)

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

    # Check tabs populated
    assert len(result.tabs.recommend) > 0 or len(result.tabs.risk) > 0
    all_pkgs = result.tabs.recommend + result.tabs.risk

    # Unified package check
    unified = [p for p in all_pkgs if p.package_class == PackageClass.UNIFIED]
    assert len(unified) >= 1
    assert unified[0].connection.preset_ok is True
    assert "обычно сквозной" in unified[0].flags[1]

    # Assembly package check (SAW -> IST airport change goes to risk)
    risk_pkgs = result.tabs.risk
    assert any(p.connection.airport_change for p in risk_pkgs)
    saw_ist_pkg = next(p for p in risk_pkgs if p.connection.airport_change)
    assert any("IST ↔ SAW" in f for f in saw_ist_pkg.flags)

    # Check neighbor dates block is returned
    assert result.neighbor_dates is not None
    assert len(result.neighbor_dates) == 2


@pytest.mark.asyncio
async def test_basket_price_calculation():
    # Full service
    basket_tk, conf_tk, flags_tk = calculate_basket_price(
        fare_per_pax=30000,
        num_pax=2,
        num_legs=2,
        carrier="TK",
        baggage=BaggageChoice.CHECKED_20KG,
        seats=SeatChoice.YES,
    )
    # Fare: 30000 * 2 = 60000. Seats: 900 * 2 legs * 2 pax = 3600. Total = 63600.
    assert basket_tk == 63600.0
    assert conf_tk.value == "exact"

    # LCC (PC - Pegasus) with checked bag
    basket_pc, conf_pc, flags_pc = calculate_basket_price(
        fare_per_pax=20000,
        num_pax=1,
        num_legs=1,
        carrier="PC",
        baggage=BaggageChoice.CHECKED_20KG,
        seats=SeatChoice.NONE,
    )
    # Fare: 20000. Bag: 3900. Total: 23900.
    assert basket_pc == 23900.0
    assert conf_pc.value == "partial"


def test_dual_cta_rules():
    # Assembly CTA: Aviasales is always primary
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

    # Unified CTA: airline cheaper by >=5% or >=1500 RUB
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
        airline_price_rub=75000,  # 5000 diff > 1500 & > 5%
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
