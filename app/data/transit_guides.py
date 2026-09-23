from typing import Dict, Any

CITY_TRANSIT_GUIDES: Dict[str, Dict[str, Any]] = {
    "IST": {
        "city_name": "Istanbul",
        "visa_free_days_ru": 60,
        "entry_requirements": {
            "passport_validity_days": 120,
            "transit_visa_required": False,
            "schengen_needed": False,
            "notes": (
                "Граждане РФ могут находиться в Турции без визы до 60 дней подряд "
                "(суммарно не более 90 дней в течение 180-дневного периода). "
                "Загранпаспорт должен быть действителен не менее 120 дней со дня въезда. "
                "Транзитная виза для пересадки не требуется."
            ),
        },
        "airport_transfer": {
            "ist_to_saw_distance_km": 85,
            "minimum_recommended_transfer_hours": 4.5,
            "transport_options": [
                {
                    "type": "Havaist bus",
                    "route": "HVIST-13 (Istanbul Airport ↔ Sabiha Gökçen)",
                    "duration_hours": "1.5 - 2.5",
                    "cost_tl": 260,
                    "payment_methods": "Credit card (UnionPay/Foreign), Istanbulkart",
                },
                {
                    "type": "Taxi / Transfer",
                    "duration_hours": "1.0 - 2.0",
                    "cost_tl": "1500 - 2200",
                    "payment_methods": "Cash (TL, USD, EUR), credit card",
                },
                {
                    "type": "Metro (M11 + Marmaray + M4)",
                    "duration_hours": "2.0 - 2.5",
                    "cost_tl": 140,
                    "payment_methods": "Istanbulkart",
                },
            ],
            "recommendations": (
                "Если прилет в IST (Новый аэропорт), а вылет из SAW (Сабиха Гёкчен), "
                "закладывайте на пересадку минимум 4.5–5 часов. "
                "При пересадке в одном и том же аэропорту IST достаточно 2–2.5 часов."
            ),
        },
        "tourist_transit_highlights": [
            {
                "name": "Touristanbul (Turkish Airlines)",
                "description": (
                    "Бесплатная экскурсия по Стамбулу от авиакомпании Turkish Airlines "
                    "для пассажиров с международной пересадкой от 6 до 24 часов."
                ),
                "eligibility": "Turkish Airlines international-to-international transfer, transit 6-24 hours",
                "counter_location": "Hotel Desk next to arrivals hall at IST",
            },
            {
                "name": "Transit Hotel / YOTELAIR",
                "description": "Отель в транзитной зоне аэропорта IST (airside) и landside.",
                "notes": "Удобно при ночных стыковках от 6 часов без выхода в город.",
            },
        ],
        "payment_tips": {
            "mir_status": "Карты МИР в Турции не принимаются банками с осени 2022 года.",
            "unionpay_status": (
                "Карты UnionPay неподсанкционных банков РФ (например, Газпромбанк, Россельхозбанк) "
                "работают на снятие наличных в банкоматах Ziraat Bank, DenizBank, VakifBank, Isbank, "
                "а также на оплату во многих терминалах аэропорта."
            ),
            "cash_recommendations": "Рекомендуется иметь наличные доллары (USD) или евро (EUR) для обмена на турецкие лиры.",
        },
    },
    "HKT": {
        "city_name": "Phuket",
        "visa_free_days_ru": 60,
        "entry_requirements": {
            "passport_validity_days": 180,
            "transit_visa_required": False,
            "schengen_needed": False,
            "notes": (
                "Граждане РФ могут въезжать в Таиланд без визы на срок до 60 дней. "
                "Загранпаспорт должен быть действителен не менее 6 месяцев (180 дней). "
                "Необходимо иметь обратный билет или билет в третью страну."
            ),
        },
        "payment_tips": {
            "mir_status": "Карты МИР не работают в Таиланде.",
            "unionpay_status": "UnionPay неподсанкционных банков РФ работает в банкоматах (Bangkok Bank, SCB, Kasikorn) и крупных ТЦ.",
            "cash_recommendations": "Наличные доллары (нового образца) или наличные евро с обменом в местных обменных пунктах.",
        },
    },
}
