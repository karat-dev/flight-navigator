from datetime import datetime, date
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class FlightClass(str, Enum):
    ECONOMY = "economy"
    COMFORT = "comfort"
    BUSINESS = "business"


class BaggageChoice(str, Enum):
    NONE = "none"
    CABIN_ONLY = "cabin_only"
    CHECKED_10KG = "checked_10kg"
    CHECKED_20KG = "checked_20kg"
    CHECKED_23KG = "checked_23kg"
    CHECKED_30KG = "checked_30kg"


class SeatChoice(str, Enum):
    NONE = "none"
    YES = "yes"


class LayoverPreset(str, Enum):
    FASTER = "faster"
    CALM = "calm"
    BUFFER_12H = "buffer_12h"


class PackageClass(str, Enum):
    UNIFIED = "unified"
    ASSEMBLY = "assembly"


class PriceConfidence(str, Enum):
    EXACT = "exact"
    PARTIAL = "partial"


class AirportInfo(BaseModel):
    code: str = Field(..., description="IATA code: SVO, VKO, DME, IST, SAW, HKT")
    name: str = Field(..., description="Airport name")
    city: str = Field(..., description="City name")
    country: str = Field(..., description="Country name")


class CityInfo(BaseModel):
    code: str
    name: str
    country: str
    airports: List[AirportInfo]


class FlightSegment(BaseModel):
    from_airport: str = Field(..., description="Departure airport or city IATA code")
    to_airport: str = Field(..., description="Arrival airport or city IATA code")
    date: str = Field(..., description="Departure date (YYYY-MM-DD)")
    dep_time: Optional[str] = Field(None, description="Exact departure time if known (null if not returned by cache)")
    arr_time: Optional[str] = Field(None, description="Exact arrival time if known (null if not returned by cache)")
    carrier: Optional[str] = Field(None, description="Carrier / airline code (e.g. TK, SU, PC, WZ)")
    airline_name: Optional[str] = Field(None, description="Airline name")
    flight_number: Optional[str] = Field(None, description="Flight number if known, otherwise null")
    changes: int = Field(0, description="Number of transfers within this leg")
    price_rub: float = Field(..., description="Bare price for this leg from Data API")
    source: str = Field("aviasales_data_api", description="Data source")
    found_at: Optional[str] = Field(None, description="When the price was recorded in cache")


class ConnectionInfo(BaseModel):
    hub: str = Field("IST", description="Hub city/airport")
    airport_change: bool = Field(False, description="Whether passenger transfers between IST and SAW")
    from_airport: Optional[str] = Field(None, description="Arrival airport at hub")
    to_airport: Optional[str] = Field(None, description="Departure airport at hub")
    duration_min: Optional[int] = Field(None, description="Layover in minutes if times known, else null")
    preset_ok: bool = Field(True, description="Whether connection complies with chosen preset floors")
    transfer_notes: Optional[str] = Field(None, description="Notes on airport change, minimum connection time")


class CTAInfo(BaseModel):
    title: str = Field(..., description="Button title")
    url: str = Field(..., description="Target deep link")
    provider_name: str = Field(..., description="Target name (Авиасейлс, Turkish Airlines, etc.)")
    price_rub: Optional[float] = Field(None, description="Price indicator if known")


class DualCTA(BaseModel):
    primary: CTAInfo
    secondary: Optional[CTAInfo] = None


class Package(BaseModel):
    id: str = Field(..., description="Unique package ID")
    package_class: PackageClass = Field(..., alias="class", description="'unified' (через Стамбул единым билетом) или 'assembly' (два раздельных билета)")
    segments: List[FlightSegment] = Field(..., description="Legs of the journey")
    connection: ConnectionInfo = Field(..., description="Connection information at hub")
    bare_fare_rub: float = Field(..., description="Base fare without added baggage/seat options")
    full_basket_rub: float = Field(..., description="Total price with baggage and seat preferences included")
    currency: str = Field("RUB", description="Currency")
    price_confidence: PriceConfidence = Field(PriceConfidence.EXACT, description="exact or partial (if baggage costs estimated)")
    flags: List[str] = Field(default_factory=list, description="User warnings and guidance in Russian")
    cluster_alternatives: List[Dict[str, Any]] = Field(default_factory=list, description="Alternative flights clustered within ±1000 RUB or ~3%")
    cta: DualCTA = Field(..., description="Dual call-to-action deep links")
    disclaimer_required: bool = Field(True, description="Client must show disclaimer before external click")

    model_config = {
        "populate_by_name": True
    }


class PassengerConfig(BaseModel):
    adults: int = Field(1, ge=1, le=9)
    children: int = Field(0, ge=0, le=9)
    infants: int = Field(0, ge=0, le=4)

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants


class RouteSearchRequest(BaseModel):
    departure_date: date = Field(..., description="Date of departure from Moscow (YYYY-MM-DD)")
    origin: str = Field("MOW", description="Origin city or airport code (MOW, SVO, VKO, DME)")
    hub: str = Field("IST", description="Hub city or airport code (IST, SAW)")
    destination: str = Field("HKT", description="Destination airport code (HKT)")
    flight_class: FlightClass = FlightClass.ECONOMY
    
    # Assembly preset
    layover_preset: LayoverPreset = Field(LayoverPreset.CALM, description="faster | calm | buffer_12h")
    allow_airport_change: bool = Field(True, description="Allow changing between IST and SAW")
    
    # Full basket params
    passengers: PassengerConfig = Field(default_factory=PassengerConfig)
    baggage: BaggageChoice = Field(BaggageChoice.CHECKED_20KG, description="none | cabin_only | checked_10kg | checked_20kg | checked_23kg | checked_30kg")
    seats: SeatChoice = Field(SeatChoice.NONE, description="none | yes")

    # Filters and controls
    max_changes_per_leg: int = Field(1, ge=0, le=2, description="Max transfers within each leg")
    include_neighbor_dates: bool = Field(False, description="Search ±1 day as well")


class TabsResult(BaseModel):
    recommend: List[Package] = Field(default_factory=list, description="Рекомендуемые варианты (проходят по нормам времени пересадки)")
    risk: List[Package] = Field(default_factory=list, description="Дешевле, но рискованнее (смена аэропорта, короткая стыковка, раздельные билеты)")


class NeighborDateResult(BaseModel):
    date: str
    day_diff: int
    cheapest_price_rub: Optional[float] = None
    packages_count: int = 0


class RouteSearchResponse(BaseModel):
    query: Dict[str, Any]
    note: str = Field(
        "Цены — ориентир, могут измениться к моменту оплаты. Цены с учётом багажа и мест из вашего запроса.",
        description="User guidance message"
    )
    tabs: TabsResult
    neighbor_dates: Optional[List[NeighborDateResult]] = None
    empty_reasons: List[str] = Field(default_factory=list)
    manual_search_url: Optional[str] = None
