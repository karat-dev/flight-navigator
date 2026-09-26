from typing import List, Dict
from app.models.flight import AirportInfo, CityInfo

AIRPORTS_DB: Dict[str, AirportInfo] = {
    # Moscow airports
    "SVO": AirportInfo(code="SVO", name="Sheremetyevo International Airport", city="Moscow", country="Russia"),
    "VKO": AirportInfo(code="VKO", name="Vnukovo International Airport", city="Moscow", country="Russia"),
    "DME": AirportInfo(code="DME", name="Domodedovo International Airport", city="Moscow", country="Russia"),
    # Istanbul airports
    "IST": AirportInfo(code="IST", name="Istanbul Airport (New)", city="Istanbul", country="Turkey"),
    "SAW": AirportInfo(code="SAW", name="Sabiha Gokcen International Airport", city="Istanbul", country="Turkey"),
    # Phuket airport
    "HKT": AirportInfo(code="HKT", name="Phuket International Airport", city="Phuket", country="Thailand"),
}

CITIES_DB: Dict[str, CityInfo] = {
    "MOW": CityInfo(
        code="MOW",
        name="Moscow",
        country="Russia",
        airports=[AIRPORTS_DB["SVO"], AIRPORTS_DB["VKO"], AIRPORTS_DB["DME"]],
    ),
    "IST": CityInfo(
        code="IST",
        name="Istanbul",
        country="Turkey",
        airports=[AIRPORTS_DB["IST"], AIRPORTS_DB["SAW"]],
    ),
    "HKT": CityInfo(
        code="HKT",
        name="Phuket",
        country="Thailand",
        airports=[AIRPORTS_DB["HKT"]],
    ),
}

