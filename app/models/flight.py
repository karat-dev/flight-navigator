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
    THROUGH_UNVERIFIED = "through_unverified"
    ASSEMBLY = "assembly"


class PriceConfidence(str, Enum):
    EXACT = "exact"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class RulesConfidence(str, Enum):
    CONFIRMED = "confirmed"
    APPROX = "approx"
    UNKNOWN = "unknown"


class Freshness(str, Enum):
    FRESH = "fresh"
    POSSIBLY_STALE = "possibly_stale"


class DateMatch(str, Enum):
    EXACT = "exact"
    NEIGHBOUR = "neighbour"


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
    price_date: Optional[str] = Field(
        None, description="Calendar date the fare was priced for (may differ from requested date)"
    )
    dep_time: Optional[str] = Field(None, description="Exact departure time if known (null if not returned by cache)")
    arr_time: Optional[str] = Field(None, description="Exact arrival time if known (null if not returned by cache)")
    carrier: Optional[str] = Field(None, description="Carrier / airline IATA code from API (often absent in cache)")
    airline_name: Optional[str] = Field(None, description="Airline name from API when available")
    gate: Optional[str] = Field(None, description="OTA/agency from cache (gate), not the operating carrier")
    flight_number: Optional[str] = Field(None, description="Flight number if known, otherwise null")
    changes: int = Field(0, description="Number of transfers within this leg")
    duration_minutes: Optional[int] = Field(
        None, description="Total leg duration from API cache (minutes), if provided"
    )
    price_rub: float = Field(..., description="Bare price for this leg from Data API")
    source: str = Field("aviasales_data_api", description="Data source")
    found_at: Optional[str] = Field(None, description="When the price was recorded in cache (found_at)")
    requested_date: Optional[str] = Field(None, description="Date the user asked for, YYYY-MM-DD")
    date_status: DateMatch = Field(
        DateMatch.EXACT,
        description="exact when this fare is on the requested date, neighbour otherwise",
    )
    freshness: Freshness = Field(Freshness.POSSIBLY_STALE, description="fresh or possibly_stale from found_at")
    freshness_note: Optional[str] = Field(None, description="Russian note on cache age")


class ConnectionInfo(BaseModel):
    hub: Optional[str] = Field(None, description="Stopover hub if known from API (null if unknown)")
    airport_change: bool = Field(False, description="Whether passenger transfers between IST and SAW")
    from_airport: Optional[str] = Field(None, description="Arrival airport at hub")
    to_airport: Optional[str] = Field(None, description="Departure airport at hub")
    duration_min: Optional[int] = Field(None, description="Layover in minutes if times known, else null")
    preset_ok: Optional[bool] = Field(
        None,
        description="Whether connection complies with preset; null if layover time unknown",
    )
    transfer_notes: Optional[str] = Field(None, description="Notes on airport change, minimum connection time")
    hub_in_api: Optional[str] = Field(
        None,
        description="Stopover airport/city if returned by Data API (often null; IST/SAW not in cache)",
    )
    changes_in_api: Optional[int] = Field(None, description="number_of_changes from unified MOW–HKT cache row")


class AddonQuote(BaseModel):
    kind: str = Field(..., description="baggage or seat")
    display: str = Field(..., description="Russian explanation, including confidence")
    amount: Optional[float] = Field(None, description="Sourced amount. Null when unknown")
    currency: Optional[str] = Field(None, description="ISO currency of amount. Not converted to RUB")
    confidence: RulesConfidence = Field(RulesConfidence.UNKNOWN)
    source_url: Optional[str] = None
    checked_at: Optional[str] = Field(None, description="YYYY-MM-DD when this rule was checked")
    included_in_rub_total: bool = Field(
        False,
        description="True only when this addon is already reflected in full_basket_rub",
    )


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
    full_basket_rub: float = Field(
        ...,
        description="Ruble part of the basket. Foreign bag/seat fees are not converted into this number",
    )
    basket_display: str = Field("", description="Ticket plus sourced bag/seat text, with confidence")
    currency: str = Field("RUB", description="Currency of full_basket_rub")
    price_confidence: PriceConfidence = Field(
        PriceConfidence.UNKNOWN,
        description="exact: ticket only, no extras asked; partial: rules applied but brand or currency is incomplete; unknown: a required fee was not guessed",
    )
    rules_confidence: RulesConfidence = Field(
        RulesConfidence.UNKNOWN,
        description="confirmed / approx / unknown for the fare rules used in the basket",
    )
    addons: List[AddonQuote] = Field(default_factory=list, description="Sourced baggage and seat lines")
    freshness: Freshness = Field(Freshness.POSSIBLY_STALE)
    freshness_note: str = Field("")
    date_status: DateMatch = Field(DateMatch.EXACT)
    requested_date: Optional[str] = None
    verify_price: Optional[CTAInfo] = Field(
        None,
        description="Deep link titled проверить актуальную цену, on the requested date, with marker",
    )
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
    hub: str = Field("IST", description="Single hub when hubs is empty. IST, BJS, SHA, HKG and their airports")
    hubs: Optional[List[str]] = Field(
        None,
        description="Hubs to build self-transfer through. Overrides hub. IST, BJS/PEK/PKX, SHA/PVG, HKG",
    )
    destination: str = Field("HKT", description="Destination city or airport code")
    return_date: Optional[date] = Field(None, description="When set, also search the return direction")
    flight_class: FlightClass = FlightClass.ECONOMY
    
    # Assembly preset
    layover_preset: LayoverPreset = Field(LayoverPreset.CALM, description="faster | calm | buffer_12h")
    allow_airport_change: bool = Field(
        True,
        description="Allow a change between airports of the same hub. IST↔SAW keeps the 6 hour floor",
    )
    
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
    comparison_note: Optional[str] = Field(
        None,
        description="Through ticket versus self-transfer on the requested date",
    )
    return_tabs: Optional[TabsResult] = None
    return_empty_reasons: List[str] = Field(default_factory=list)
    return_comparison_note: Optional[str] = None
    return_neighbor_dates: Optional[List[NeighborDateResult]] = None
    return_manual_search_url: Optional[str] = None
    exclusions: List[str] = Field(
        default_factory=list,
        description="Carriers dropped from recommend and risk, with the reason",
    )
