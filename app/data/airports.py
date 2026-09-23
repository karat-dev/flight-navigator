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

# Flight schedules templates (daily frequencies)
MOW_TO_IST_TEMPLATES = [
    {
        "flight_number": "TK 414",
        "airline": "Turkish Airlines",
        "airline_code": "TK",
        "dep_airport": "VKO",
        "arr_airport": "IST",
        "dep_time_str": "05:40",
        "duration_minutes": 255,  # 4h 15m
        "airplane": "Boeing 737-800",
        "base_price_rub": 31500,
        "baggage_kg": 20,
    },
    {
        "flight_number": "TK 416",
        "airline": "Turkish Airlines",
        "airline_code": "TK",
        "dep_airport": "VKO",
        "arr_airport": "IST",
        "dep_time_str": "13:30",
        "duration_minutes": 255,
        "airplane": "Airbus A330-300",
        "base_price_rub": 34000,
        "baggage_kg": 20,
    },
    {
        "flight_number": "TK 420",
        "airline": "Turkish Airlines",
        "airline_code": "TK",
        "dep_airport": "VKO",
        "arr_airport": "IST",
        "dep_time_str": "20:00",
        "duration_minutes": 260,
        "airplane": "Boeing 777-300ER",
        "base_price_rub": 33500,
        "baggage_kg": 20,
    },
    {
        "flight_number": "SU 2136",
        "airline": "Aeroflot",
        "airline_code": "SU",
        "dep_airport": "SVO",
        "arr_airport": "IST",
        "dep_time_str": "09:15",
        "duration_minutes": 260,
        "airplane": "Boeing 777-300ER",
        "base_price_rub": 28500,
        "baggage_kg": 23,
    },
    {
        "flight_number": "SU 2134",
        "airline": "Aeroflot",
        "airline_code": "SU",
        "dep_airport": "SVO",
        "arr_airport": "IST",
        "dep_time_str": "18:45",
        "duration_minutes": 265,
        "airplane": "Airbus A330-300",
        "base_price_rub": 29800,
        "baggage_kg": 23,
    },
    {
        "flight_number": "PC 389",
        "airline": "Pegasus Airlines",
        "airline_code": "PC",
        "dep_airport": "DME",
        "arr_airport": "SAW",
        "dep_time_str": "15:20",
        "duration_minutes": 270,
        "airplane": "Airbus A321neo",
        "base_price_rub": 22400,
        "baggage_kg": 20,
    },
    {
        "flight_number": "PC 387",
        "airline": "Pegasus Airlines",
        "airline_code": "PC",
        "dep_airport": "VKO",
        "arr_airport": "SAW",
        "dep_time_str": "03:10",
        "duration_minutes": 265,
        "airplane": "Airbus A320neo",
        "base_price_rub": 19900,
        "baggage_kg": 20,
    },
]

IST_TO_HKT_TEMPLATES = [
    {
        "flight_number": "TK 172",
        "airline": "Turkish Airlines",
        "airline_code": "TK",
        "dep_airport": "IST",
        "arr_airport": "HKT",
        "dep_time_str": "01:55",
        "duration_minutes": 575,  # 9h 35m
        "airplane": "Airbus A350-900",
        "base_price_rub": 52000,
        "baggage_kg": 20,
    },
    {
        "flight_number": "TK 174",
        "airline": "Turkish Airlines",
        "airline_code": "TK",
        "dep_airport": "IST",
        "arr_airport": "HKT",
        "dep_time_str": "16:25",
        "duration_minutes": 570,  # 9h 30m
        "airplane": "Boeing 787-9",
        "base_price_rub": 56000,
        "baggage_kg": 20,
    },
    {
        "flight_number": "PC 792",
        "airline": "Pegasus Airlines",
        "airline_code": "PC",
        "dep_airport": "SAW",
        "arr_airport": "HKT",
        "dep_time_str": "21:40",
        "duration_minutes": 590,
        "airplane": "Airbus A321neo",
        "base_price_rub": 43500,
        "baggage_kg": 20,
    },
    {
        "flight_number": "TK 176",
        "airline": "Turkish Airlines",
        "airline_code": "TK",
        "dep_airport": "IST",
        "arr_airport": "HKT",
        "dep_time_str": "23:55",
        "duration_minutes": 580,
        "airplane": "Boeing 777-300ER",
        "base_price_rub": 54200,
        "baggage_kg": 20,
    },
]
