from fastapi import APIRouter, HTTPException, Query
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List

from app.models.flight import (
    RouteSearchRequest,
    RouteSearchResponse,
    AirportInfo,
    CityInfo,
    LayoverPreset,
    BaggageChoice,
    SeatChoice,
    PassengerConfig,
)
from app.services.flight_search import flight_service
from app.services.aviasales_client import aviasales_client
from app.data.airports import AIRPORTS_DB, CITIES_DB
from app.data.transit_guides import CITY_TRANSIT_GUIDES

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Service healthcheck reporting Aviasales Data API connection and token status
    WITHOUT exposing token value.
    """
    api_status = await aviasales_client.check_health()
    return {
        "status": "ok",
        "service": "Flight Navigator MOW-IST-HKT Backend",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "aviasales_data_api": api_status,
    }


@router.post("/routes/search", response_model=RouteSearchResponse)
async def search_routes(request: RouteSearchRequest):
    """
    Search for connecting flights from Moscow (MOW) to Phuket (HKT) via Istanbul (IST/SAW)
    using real Travelpayouts Aviasales Data API prices.
    Evaluates unified tickets vs assembly (separate tickets), baggage allowances,
    layover presets, and dual CTAs.
    """
    try:
        results = await flight_service.search_routes(request)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/routes/quick-search", response_model=RouteSearchResponse)
async def quick_search(
    departure_date: Optional[date] = Query(None, description="Departure date (default: +14 days)"),
    origin: str = Query("MOW", description="Origin city or airport code (MOW, SVO, VKO, DME)"),
    hub: str = Query("IST", description="Transit hub city or airport code (IST, SAW)"),
    destination: str = Query("HKT", description="Destination airport code (HKT)"),
    allow_airport_change: bool = Query(True, description="Allow changing between IST and SAW"),
    layover_preset: LayoverPreset = Query(LayoverPreset.CALM, description="faster | calm | buffer_12h"),
    baggage: BaggageChoice = Query(BaggageChoice.CHECKED_20KG, description="Baggage choice"),
    seats: SeatChoice = Query(SeatChoice.NONE, description="Seat selection choice"),
    adults: int = Query(1, ge=1, le=9, description="Number of adult passengers"),
    include_neighbor_dates: bool = Query(False, description="Search ±1 day as well"),
):
    """
    GET helper endpoint for quick browser testing of MOW-IST-HKT routes with real data.
    """
    if departure_date is None:
        departure_date = date.today() + timedelta(days=14)

    req = RouteSearchRequest(
        departure_date=departure_date,
        origin=origin,
        hub=hub,
        destination=destination,
        allow_airport_change=allow_airport_change,
        layover_preset=layover_preset,
        baggage=baggage,
        seats=seats,
        passengers=PassengerConfig(adults=adults),
        include_neighbor_dates=include_neighbor_dates,
    )
    return await flight_service.search_routes(req)


@router.get("/guide/{city_code}")
def get_transit_guide(city_code: str):
    """
    Get transit, visa, transfer and notes for a specific transit city (e.g. IST, HKT).
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
