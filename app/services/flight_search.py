from datetime import datetime, date, time, timedelta
from typing import List, Optional
import uuid

from app.models.flight import (
    FlightSegment,
    StopoverInfo,
    PaymentCardSuitability,
    RouteOption,
    RouteSearchRequest,
    RouteSearchResponse,
    FlightClass,
)
from app.data.airports import (
    AIRPORTS_DB,
    MOW_TO_IST_TEMPLATES,
    IST_TO_HKT_TEMPLATES,
)
from app.data.transit_guides import CITY_TRANSIT_GUIDES


class FlightSearchService:
    def __init__(self):
        self.mow_templates = MOW_TO_IST_TEMPLATES
        self.ist_templates = IST_TO_HKT_TEMPLATES
        self.airports = AIRPORTS_DB

    def _parse_time_str(self, time_str: str) -> time:
        parts = time_str.split(":")
        return time(hour=int(parts[0]), minute=int(parts[1]))

    def _generate_segments_for_date(
        self,
        target_date: date,
        origin_code: str,
        dest_code: str,
    ) -> List[FlightSegment]:
        segments = []
        templates = self.mow_templates if dest_code in ("IST", "SAW") else self.ist_templates

        for tmpl in templates:
            # Check origin filter
            if origin_code != "MOW" and tmpl["dep_airport"] != origin_code:
                continue
            # Check destination filter
            if dest_code not in ("IST", "MOW", "HKT") and tmpl["arr_airport"] != dest_code:
                continue

            t = self._parse_time_str(tmpl["dep_time_str"])
            dep_dt = datetime.combine(target_date, t)
            arr_dt = dep_dt + timedelta(minutes=tmpl["duration_minutes"])

            seg_id = f"{tmpl['airline_code']}-{tmpl['flight_number'].replace(' ', '')}-{target_date.strftime('%Y%m%d')}"
            
            segments.append(
                FlightSegment(
                    id=seg_id,
                    flight_number=tmpl["flight_number"],
                    airline=tmpl["airline"],
                    airline_code=tmpl["airline_code"],
                    departure_airport=tmpl["dep_airport"],
                    arrival_airport=tmpl["arr_airport"],
                    departure_time=dep_dt,
                    arrival_time=arr_dt,
                    duration_minutes=tmpl["duration_minutes"],
                    airplane=tmpl["airplane"],
                    cabin_class=FlightClass.ECONOMY,
                    baggage_included=True,
                    baggage_weight_kg=tmpl.get("baggage_kg", 20),
                )
            )
        return segments

    def _determine_payment_info(self, leg1: FlightSegment, leg2: FlightSegment) -> PaymentCardSuitability:
        # If Turkish Airlines whole journey
        if leg1.airline_code == "TK" and leg2.airline_code == "TK":
            return PaymentCardSuitability(
                mir_accepted=False,
                unionpay_accepted=True,
                foreign_cards_accepted=True,
                russian_rub_card_accepted=True,
                notes=(
                    "Единый билет Turkish Airlines. Можно купить на сайте авиакомпании "
                    "картой зарубежного банка или UnionPay (РСХБ, Газпромбанк), "
                    "либо российскими картами МИР/Visa/Mastercard через российские агрегаторы (Авиасейлс, OneTwoTrip, Купибилет) в рублях."
                ),
            )
        # Pegasus airlines
        elif leg1.airline_code == "PC" and leg2.airline_code == "PC":
            return PaymentCardSuitability(
                mir_accepted=False,
                unionpay_accepted=True,
                foreign_cards_accepted=True,
                russian_rub_card_accepted=True,
                notes=(
                    "Билет Pegasus Airlines. Оплата на сайте flypgs.com картами зарубежных банков или UnionPay, "
                    "а также в рублях российскими картами через Aviasales/Яндекс.Путешествия."
                ),
            )
        else:
            return PaymentCardSuitability(
                mir_accepted=False,
                unionpay_accepted=True,
                foreign_cards_accepted=True,
                russian_rub_card_accepted=True,
                notes=(
                    "Раздельные билеты / интерлайн (Аэрофлот + Turkish/Pegasus). "
                    "Сегмент по РФ можно оплатить любой российской картой МИР. "
                    "Всю связку можно оплатить в рублях через российские агентства."
                ),
            )

    def search_routes(self, request: RouteSearchRequest) -> RouteSearchResponse:
        dep_date = request.departure_date
        next_day = dep_date + timedelta(days=1)
        day_after = dep_date + timedelta(days=2)

        # Leg 1: MOW -> IST/SAW
        leg1_candidates = self._generate_segments_for_date(
            dep_date, origin_code=request.origin, dest_code=request.hub
        )

        # Leg 2: IST/SAW -> HKT (consider dep_date, next_day, and day_after for layovers)
        leg2_candidates = []
        for d in (dep_date, next_day, day_after):
            leg2_candidates.extend(
                self._generate_segments_for_date(d, origin_code=request.hub, dest_code=request.destination)
            )

        min_layover_sec = (request.min_layover_hours or 2.0) * 3600
        max_layover_sec = (request.max_layover_hours or 48.0) * 3600

        valid_routes: List[RouteOption] = []

        for l1 in leg1_candidates:
            for l2 in leg2_candidates:
                # Ensure time sequence
                layover_sec = (l2.departure_time - l1.arrival_time).total_seconds()
                if layover_sec < min_layover_sec or layover_sec > max_layover_sec:
                    continue

                is_airport_change = l1.arrival_airport != l2.departure_airport
                if is_airport_change and not request.allow_airport_change:
                    continue

                # Extra minimum transfer time check if airport changes (IST <-> SAW requires min 4.5h)
                if is_airport_change and layover_sec < 4.5 * 3600:
                    continue

                layover_minutes = int(layover_sec // 60)
                total_duration = l1.duration_minutes + layover_minutes + l2.duration_minutes

                # Base price calculation
                # Find template base prices
                l1_price = 30000
                for tmpl in self.mow_templates:
                    if tmpl["flight_number"] == l1.flight_number:
                        l1_price = tmpl["base_price_rub"]
                        break

                l2_price = 52000
                for tmpl in self.ist_templates:
                    if tmpl["flight_number"] == l2.flight_number:
                        l2_price = tmpl["base_price_rub"]
                        break

                # Apply discount for single-airline connections
                total_price = (l1_price + l2_price) * request.passengers
                is_single_airline = l1.airline_code == l2.airline_code
                if is_single_airline:
                    total_price = round(total_price * 0.92, -2)  # 8% through-fare discount

                tags = []
                if is_single_airline and not is_airport_change:
                    tags.append("single_ticket")
                    tags.append("through_baggage")
                if not is_airport_change:
                    tags.append("same_airport")
                else:
                    tags.append("airport_change_ist_saw")

                if layover_minutes >= 360 and is_single_airline and l1.airline_code == "TK":
                    tags.append("touristanbul_eligible")

                provider = (
                    f"{l1.airline}" if is_single_airline else f"{l1.airline} + {l2.airline} via Aviasales"
                )

                stopover_info = StopoverInfo(
                    airport=f"{l1.arrival_airport}" if not is_airport_change else f"{l1.arrival_airport} → {l2.departure_airport}",
                    city="Istanbul",
                    duration_minutes=layover_minutes,
                    is_airport_change=is_airport_change,
                    requires_transit_visa=False,
                    visa_notes=CITY_TRANSIT_GUIDES["IST"]["entry_requirements"]["notes"],
                )

                route_id = f"ROUTE-{l1.id}-{l2.id}"
                payment_info = self._determine_payment_info(l1, l2)

                route = RouteOption(
                    id=route_id,
                    origin="MOW",
                    hub="IST",
                    destination="HKT",
                    segments=[l1, l2],
                    stopover=stopover_info,
                    total_duration_minutes=total_duration,
                    total_price=float(total_price),
                    currency="RUB",
                    booking_provider=provider,
                    booking_url=f"https://www.aviasales.ru/search/{l1.departure_airport}{dep_date.strftime('%d%m')}{request.destination}1?utm_source=flight_navigator",
                    payment_info=payment_info,
                    tags=tags,
                )
                valid_routes.append(route)

        # Tag fastest and cheapest
        if valid_routes:
            cheapest = min(valid_routes, key=lambda r: r.total_price)
            fastest = min(valid_routes, key=lambda r: r.total_duration_minutes)
            if "cheapest" not in cheapest.tags:
                cheapest.tags.append("cheapest")
            if "fastest" not in fastest.tags:
                fastest.tags.append("fastest")

            # Sorting
            if request.sort_by == "duration":
                valid_routes.sort(key=lambda r: r.total_duration_minutes)
            elif request.sort_by == "layover":
                valid_routes.sort(key=lambda r: r.stopover.duration_minutes)
            else:  # price default
                valid_routes.sort(key=lambda r: r.total_price)

        summary = {}
        if valid_routes:
            summary = {
                "min_price_rub": min(r.total_price for r in valid_routes),
                "max_price_rub": max(r.total_price for r in valid_routes),
                "min_duration_minutes": min(r.total_duration_minutes for r in valid_routes),
                "fastest_hours": round(min(r.total_duration_minutes for r in valid_routes) / 60, 1),
                "same_airport_count": sum(1 for r in valid_routes if not r.stopover.is_airport_change),
                "airport_change_count": sum(1 for r in valid_routes if r.stopover.is_airport_change),
            }

        return RouteSearchResponse(
            origin=request.origin,
            hub=request.hub,
            destination=request.destination,
            departure_date=dep_date,
            currency="RUB",
            total_found=len(valid_routes),
            routes=valid_routes,
            summary=summary,
        )


flight_service = FlightSearchService()
