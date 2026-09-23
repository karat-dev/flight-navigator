from datetime import datetime, date
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class FlightClass(str, Enum):
    ECONOMY = "economy"
    COMFORT = "comfort"
    BUSINESS = "business"


class AirportInfo(BaseModel):
    code: str = Field(..., description="IATA code: SVO, VKO, DME, IST, SAW, HKT")
    name: str = Field(..., description="Airport name")
    city: str = Field(..., description="City name")
    country: str = Field(..., description="Country name")


class FlightSegment(BaseModel):
    id: str = Field(..., description="Segment ID")
    flight_number: str = Field(..., description="e.g. TK 414, SU 2136")
    airline: str = Field(..., description="Airline name")
    airline_code: str = Field(..., description="Airline IATA code: TK, SU, WZ, PC")
    departure_airport: str = Field(..., description="IATA airport code")
    arrival_airport: str = Field(..., description="IATA airport code")
    departure_time: datetime = Field(..., description="Departure datetime (ISO)")
    arrival_time: datetime = Field(..., description="Arrival datetime (ISO)")
    duration_minutes: int = Field(..., description="Flight duration in minutes")
    airplane: Optional[str] = Field(None, description="Aircraft model, e.g. Airbus A330-300, Boeing 777-300ER")
    cabin_class: FlightClass = FlightClass.ECONOMY
    baggage_included: bool = Field(True, description="Whether 1 check-in baggage is included")
    baggage_weight_kg: Optional[int] = Field(20, description="Check-in baggage allowance in kg")


class StopoverInfo(BaseModel):
    airport: str = Field(..., description="Transit airport code (e.g. IST or SAW)")
    city: str = Field(..., description="Transit city (e.g. Istanbul)")
    duration_minutes: int = Field(..., description="Layover duration in minutes")
    is_airport_change: bool = Field(False, description="True if arrival airport is IST and departure is SAW")
    requires_transit_visa: bool = Field(False, description="Russian passport transit visa requirement note")
    visa_notes: Optional[str] = Field(None, description="Visa/entry requirements notes for Russian citizens")


class PaymentCardSuitability(BaseModel):
    mir_accepted: bool = Field(False, description="Whether Russian Mir cards are accepted directly")
    unionpay_accepted: bool = Field(True, description="Whether Russian UnionPay cards are accepted")
    foreign_cards_accepted: bool = Field(True, description="Whether foreign Visa/Mastercard cards are accepted")
    russian_rub_card_accepted: bool = Field(False, description="Whether Russian Visa/Mastercard/Mir accepted (e.g. via RU OTA)")
    notes: str = Field(..., description="Payment tips and details for Russian travelers")


class RouteOption(BaseModel):
    id: str = Field(..., description="Route unique ID")
    origin: str = Field("MOW", description="Origin city code")
    hub: str = Field("IST", description="Hub transit city code")
    destination: str = Field("HKT", description="Destination city code")
    segments: List[FlightSegment] = Field(..., description="Ordered list of flight legs")
    stopover: StopoverInfo = Field(..., description="Stopover information in Istanbul")
    
    total_duration_minutes: int = Field(..., description="Total travel time including layover")
    total_price: float = Field(..., description="Total price")
    currency: str = Field("RUB", description="Price currency")
    
    booking_provider: str = Field(..., description="Provider / OTA name (e.g. Turkish Airlines, Aeroflot + Turkish, Aviasales)")
    booking_url: Optional[str] = Field(None, description="Direct booking or search link")
    payment_info: PaymentCardSuitability = Field(..., description="Payment card guidance for Russian citizens")
    
    tags: List[str] = Field(default_factory=list, description="Tags: ['cheapest', 'fastest', 'same_airport', 'single_ticket']")


class RouteSearchRequest(BaseModel):
    departure_date: date = Field(..., description="Date of departure from Moscow (YYYY-MM-DD)")
    origin: str = Field("MOW", description="Origin city or airport code (MOW, SVO, VKO, DME)")
    hub: str = Field("IST", description="Hub city or airport code (IST, SAW)")
    destination: str = Field("HKT", description="Destination airport code (HKT)")
    flight_class: FlightClass = FlightClass.ECONOMY
    min_layover_hours: Optional[float] = Field(2.0, ge=1.0, description="Minimum layover in Istanbul (hours)")
    max_layover_hours: Optional[float] = Field(48.0, le=168.0, description="Maximum layover in Istanbul (hours)")
    allow_airport_change: bool = Field(True, description="Allow changing between IST and SAW")
    passengers: int = Field(1, ge=1, le=9, description="Number of passengers")
    sort_by: Optional[str] = Field("price", description="Sort by: 'price', 'duration', 'layover'")


class RouteSearchResponse(BaseModel):
    origin: str
    hub: str
    destination: str
    departure_date: date
    currency: str
    total_found: int
    routes: List[RouteOption]
    summary: Dict[str, Any] = Field(default_factory=dict, description="Summary statistics (cheapest price, fastest duration, etc.)")


class CityInfo(BaseModel):
    code: str
    name: str
    country: str
    airports: List[AirportInfo]
