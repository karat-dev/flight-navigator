import pytest
from datetime import date, timedelta
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.flight_search import flight_service
from app.models.flight import RouteSearchRequest


@pytest.mark.asyncio
async def test_root():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "Flight Navigator" in data["message"]


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


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

    cities = cities_res.json()
    city_codes = [c["code"] for c in cities]
    assert "MOW" in city_codes
    assert "IST" in city_codes
    assert "HKT" in city_codes


@pytest.mark.asyncio
async def test_transit_guide_ist():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/guide/IST")
    assert res.status_code == 200
    data = res.json()
    assert data["city_name"] == "Istanbul"
    assert "passport_validity_days" in data["entry_requirements"]
    assert "airport_transfer" in data
    assert "payment_tips" in data


@pytest.mark.asyncio
async def test_transit_guide_not_found():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/guide/UNKNOWN")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_route_search_service():
    search_date = date.today() + timedelta(days=10)
    req = RouteSearchRequest(
        departure_date=search_date,
        origin="MOW",
        hub="IST",
        destination="HKT",
        allow_airport_change=True,
        min_layover_hours=2.0,
        max_layover_hours=24.0,
    )
    result = flight_service.search_routes(req)
    assert result.total_found > 0
    assert len(result.routes) == result.total_found

    first = result.routes[0]
    assert first.origin == "MOW"
    assert first.hub == "IST"
    assert first.destination == "HKT"
    assert len(first.segments) == 2
    assert first.total_price > 0
    assert first.payment_info is not None
    assert first.stopover.duration_minutes >= 120


@pytest.mark.asyncio
async def test_quick_search_api():
    search_date = (date.today() + timedelta(days=14)).isoformat()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(
            f"/api/v1/routes/quick-search?departure_date={search_date}&allow_airport_change=false&sort_by=price"
        )
    assert res.status_code == 200
    data = res.json()
    assert data["total_found"] > 0
    for r in data["routes"]:
        assert r["stopover"]["is_airport_change"] is False


@pytest.mark.asyncio
async def test_airport_change_constraint():
    search_date = date.today() + timedelta(days=5)
    # When allow_airport_change is False, no route should change between IST and SAW
    req = RouteSearchRequest(
        departure_date=search_date,
        origin="MOW",
        hub="IST",
        destination="HKT",
        allow_airport_change=False,
    )
    res = flight_service.search_routes(req)
    for r in res.routes:
        assert not r.stopover.is_airport_change
        assert r.segments[0].arrival_airport == r.segments[1].departure_airport
