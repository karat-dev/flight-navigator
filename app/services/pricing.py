from typing import Dict, Any, Tuple
from app.models.flight import BaggageChoice, SeatChoice, PriceConfidence

# Baggage estimated additions in RUB per passenger per leg if not included in fare
# LCCs (e.g. Pegasus) charge extra, full-service carriers (e.g. Turkish, Aeroflot) typically include baggage in standard fares
BAGGAGE_EXTRA_RUB_PER_LEG: Dict[str, float] = {
    BaggageChoice.NONE.value: 0.0,
    BaggageChoice.CABIN_ONLY.value: 0.0,
    BaggageChoice.CHECKED_10KG.value: 2500.0,
    BaggageChoice.CHECKED_20KG.value: 3900.0,
    BaggageChoice.CHECKED_23KG.value: 4200.0,
    BaggageChoice.CHECKED_30KG.value: 5800.0,
}

SEAT_EXTRA_RUB_PER_LEG: float = 900.0


def calculate_basket_price(
    fare_per_pax: float,
    num_pax: int,
    num_legs: int,
    carrier: str | None,
    baggage: BaggageChoice,
    seats: SeatChoice,
) -> Tuple[float, PriceConfidence, list[str]]:
    """
    Calculates full basket price in RUB and price confidence.
    """
    bare_total = fare_per_pax * num_pax
    flags: list[str] = []
    confidence = PriceConfidence.EXACT

    extra_per_pax_leg = 0.0

    # Baggage calculation
    if baggage not in (BaggageChoice.NONE, BaggageChoice.CABIN_ONLY):
        if not carrier:
            confidence = PriceConfidence.UNKNOWN
            flags.append("Багаж не учтён в цене — проверьте при покупке")
        else:
            is_lcc = carrier.upper() in ("PC", "WZ", "DP", "A4", "D7")
            if is_lcc:
                extra_per_pax_leg += BAGGAGE_EXTRA_RUB_PER_LEG.get(baggage.value, 3900.0)
                confidence = PriceConfidence.PARTIAL
                flags.append(f"Багаж ({baggage.value}) оценен ориентировочно для лоукостера {carrier}")
            elif baggage in (BaggageChoice.CHECKED_23KG, BaggageChoice.CHECKED_30KG):
                pass
            else:
                flags.append("Норма багажа: проверьте включённый вес при покупке билета")

    # Seat selection
    if seats == SeatChoice.YES:
        extra_per_pax_leg += SEAT_EXTRA_RUB_PER_LEG
        flags.append("Выбор места: добавлена ориентировочная стоимость выбора кресла")

    total_basket = bare_total + (extra_per_pax_leg * num_legs * num_pax)
    return round(total_basket, 2), confidence, flags
