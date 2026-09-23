from fastapi import APIRouter, HTTPException, Query
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List

from app.models.flight import (
    RouteSearchRequest,
    RouteSearchResponse,
    RouteOption,
    FlightClass,
    AirportInfo,
    CityInfo,
)
from app.services.flight_search import flight_service
from app.data.airports import AIRPORTS_DB, CITIES_DB
from app.data.transit_guides import CITY_TRANSIT_GUIDES

router = APIRouter()


@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "Flight Navigator MOW-IST-HKT Backend",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/routes/search", response_model=RouteSearchResponse)
def search_routes(request: RouteSearchRequest):
    """
    Search for connecting flights from Moscow (MOW) to Phuket (HKT) via Istanbul (IST/SAW).
    Evaluates layovers, airport changes (IST <-> SAW), baggage allowances,
    payment options (UnionPay, Russian cards via OTA, Mir) and Touristanbul eligibility.
    """
    try:
        results = flight_service.search_routes(request)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/routes/quick-search", response_model=RouteSearchResponse)
def quick_search(
    departure_date: Optional[date] = Query(None, description="Departure date (default: tomorrow)"),
    origin: str = Query("MOW", description="Origin city or airport code (MOW, SVO, VKO, DME)"),
    hub: str = Query("IST", description="Transit hub city or airport code (IST, SAW)"),
    destination: str = Query("HKT", description="Destination airport code (HKT)"),
    allow_airport_change: bool = Query(True, description="Allow changing between IST and SAW"),
    min_layover_hours: float = Query(2.0, ge=1.0, description="Minimum layover in Istanbul (hours)"),
    max_layover_hours: float = Query(48.0, le=168.0, description="Maximum layover in Istanbul (hours)"),
    passengers: int = Query(1, ge=1, le=9, description="Number of passengers"),
    sort_by: str = Query("price", pattern="^(price|duration|layover)$", description="Sort criteria"),
):
    """
    GET helper endpoint for easy browser/query testing of MOW-IST-HKT routes.
    """
    if departure_date is None:
        departure_date = date.today() + timedelta(days=7)

    req = RouteSearchRequest(
        departure_date=departure_date,
        origin=origin,
        hub=hub,
        destination=destination,
        allow_airport_change=allow_airport_change,
        min_layover_hours=min_layover_hours,
        max_layover_hours=max_layover_hours,
        passengers=passengers,
        sort_by=sort_by,
    )
    return flight_service.search_routes(req)


@router.get("/guide/{city_code}")
def get_transit_guide(city_code: str):
    """
    Get transit, visa, transfer and payment guide for a specific transit city (e.g. IST, HKT).
    """
    code = city_code.upper()
    if code not in CITY_TRANSIT_GUIDES:
        raise HTTPException(
            status_code=404,
            detail=f"Guide for {city_code} not found. Available cities: {list(CITY_TRANSIT_GUIDES.keys())}",
        )
    return CITY_TRANSIT_GUIDES[code]


@router.get("/airports", response_model=List[AirportInfo])
def list_airports():
    """List all supported airports on the MOW-IST-HKT corridor."""
    return list(AIRPORTS_DB.values())


@router.get("/cities", response_model=List[CityInfo])
def list_cities():
    """List cities involved in the MOW-IST-HKT corridor with their airports."""
    return list(CITIES_DB.values())
