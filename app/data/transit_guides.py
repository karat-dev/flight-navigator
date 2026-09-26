from typing import Dict, Any

CITY_TRANSIT_GUIDES: Dict[str, Dict[str, Any]] = {
    "IST": {
        "city_name": "Стамбул",
        "visa_disclaimer": "Ориентир, не проверка паспорта. Правила могут меняться — проверяйте официальные источники перед поездкой.",
        "visa_info": {
            "source_url": "https://mid.ru/ru/maps/tr/",
            "last_checked": "2026-09-01",
            "notes": (
                "Для граждан РФ въезд в Турцию безвизовый на срок до 60 дней "
                "(суммарно не более 90 дней в течение 180 дней). "
                "Загранпаспорт должен быть действителен не менее 120 дней со дня въезда. "
                "Транзитная виза при пересадке не требуется."
            ),
        },
        "airport_transfer": {
            "ist_to_saw_distance_km": 85,
            "minimum_recommended_transfer_hours": 6.0,
            "transport_options": [
                {
                    "type": "Havaist bus",
                    "route": "HVIST-13 (Istanbul Airport ↔ Sabiha Gökçen)",
                    "duration_hours": "1.5 - 2.5",
                    "cost_tl": 260,
                },
                {
                    "type": "Taxi / Transfer",
                    "duration_hours": "1.0 - 2.0",
                    "cost_tl": "1500 - 2200",
                },
                {
                    "type": "Metro (M11 + Marmaray + M4)",
                    "duration_hours": "2.0 - 2.5",
                    "cost_tl": 140,
                },
            ],
            "recommendations": (
                "При пересадке со сменой аэропорта IST ↔ SAW минимальный запас времени — 6 часов. "
                "При пересадке в одном и том же аэропорту для раздельных билетов закладывайте от 3.5 часов."
            ),
        },
        "tourist_transit_highlights": [
            {
                "name": "Touristanbul (Turkish Airlines)",
                "description": (
                    "Бесплатная экскурсия по Стамбулу от авиакомпании Turkish Airlines "
                    "для пассажиров с единым билетом и международной пересадкой от 6 до 24 часов."
                ),
                "eligibility": "Turkish Airlines international transfer, 6-24 hours layover",
                "counter_location": "Hotel Desk next to arrivals hall at IST",
            },
        ],
    },
    "HKT": {
        "city_name": "Пхукет",
        "visa_disclaimer": "Ориентир, не проверка паспорта. Правила могут меняться — проверяйте официальные источники перед поездкой.",
        "visa_info": {
            "source_url": "https://mid.ru/ru/maps/th/",
            "last_checked": "2026-09-01",
            "notes": (
                "Для граждан РФ действует безвизовый въезд в Таиланд на срок до 60 дней. "
                "Загранпаспорт должен быть действителен не менее 6 месяцев (180 дней) на момент въезда. "
                "Может потребоваться подтверждение обратного билета или билета в третью страну."
            ),
        },
    },
}
