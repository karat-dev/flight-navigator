from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple

from app.models.flight import (
    RouteSearchRequest,
    RouteSearchResponse,
    TabsResult,
    Package,
    PackageClass,
    FlightSegment,
    ConnectionInfo,
    LayoverPreset,
    PriceConfidence,
    NeighborDateResult,
    BaggageChoice,
)
from app.services.aviasales_client import aviasales_client, AviasalesDataClient, AviasalesAPIError
from app.services.pricing import calculate_basket_price
from app.services.cta import build_dual_cta, build_aviasales_deep_link
from app.data.transit_guides import CITY_TRANSIT_GUIDES


class FlightSearchService:
    def __init__(self, client: Optional[AviasalesDataClient] = None):
        self.client = client or aviasales_client

    def _parse_date(self, d_str: str) -> Optional[date]:
        try:
            return datetime.strptime(d_str[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    def _filter_and_pick_leg_prices(
        self,
        prices: List[Dict[str, Any]],
        target_date: date,
        max_changes: int = 1,
        day_window: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Per-leg picker:
        - number_of_changes <= max_changes (default 1)
        - depart_date within ±5 days of target_date
        - Sort: min price, tie-break closer date to target_date
        """
        valid = []
        for p in prices:
            dep_str = p.get("depart_date")
            if not dep_str:
                continue
            d = self._parse_date(dep_str)
            if not d:
                continue

            day_diff = abs((d - target_date).days)
            if day_diff > day_window:
                continue

            changes = p.get("number_of_changes", 0)
            if changes > max_changes:
                continue

            price = float(p.get("value", 0))
            if price <= 0:
                continue

            valid.append({
                "item": p,
                "date": d,
                "price": price,
                "day_diff": day_diff,
            })

        # Sort: min price, tie-break closer date
        valid.sort(key=lambda x: (x["price"], x["day_diff"]))
        return [v["item"] for v in valid]

    def _evaluate_connection(
        self,
        leg1: Dict[str, Any],
        leg2: Dict[str, Any],
        preset: LayoverPreset,
        allow_airport_change: bool,
    ) -> Tuple[ConnectionInfo, List[str]]:
        """
        Evaluates connection between Leg 1 and Leg 2.
        Airport-change floor is 6h.
        Presets (same airport vs IST<->SAW):
        - faster: 2h00-3h30 (same) | >= 6h (change)
        - calm: 3h30-8h (same) | 6h-12h (change)
        - buffer_12h: >= 12h (same) | >= 12h (change)
        """
        arr_airport = leg1.get("destination_airport") or leg1.get("destination") or "IST"
        dep_airport = leg2.get("origin_airport") or leg2.get("origin") or "IST"

        is_airport_change = (
            (arr_airport == "IST" and dep_airport == "SAW") or
            (arr_airport == "SAW" and dep_airport == "IST")
        )

        flags: List[str] = []
        preset_ok = True

        if is_airport_change:
            flags.append("Смена аэропорта в Стамбуле: IST ↔ SAW (закладывайте от 6 часов)")
            if not allow_airport_change:
                preset_ok = False

        # Check dates between legs
        d1 = self._parse_date(leg1.get("depart_date", ""))
        d2 = self._parse_date(leg2.get("depart_date", ""))

        duration_min = None
        transfer_notes = None

        if d1 and d2:
            day_gap = (d2 - d1).days
            if day_gap < 0:
                preset_ok = False
                flags.append("Дата вылета из Стамбула раньше даты вылета из Москвы")
            elif day_gap == 0:
                # Same day transfer
                transfer_notes = "Вылет из Стамбула в тот же день (точное время стыковки уточняйте при бронировании)"
                if is_airport_change:
                    flags.append("Внимание: смена аэропорта в один день — проверьте расписание рейсов перед оплатой")
            elif day_gap == 1:
                transfer_notes = "Стыковка с ночевкой в Стамбуле (следующий день)"
                if preset == LayoverPreset.FASTER:
                    preset_ok = False
            else:
                transfer_notes = f"Длинная пересадка в Стамбуле ({day_gap} дн.)"
                if preset != LayoverPreset.BUFFER_12H:
                    preset_ok = False

        connection = ConnectionInfo(
            hub="IST",
            airport_change=is_airport_change,
            from_airport=arr_airport,
            to_airport=dep_airport,
            duration_min=duration_min,
            preset_ok=preset_ok,
            transfer_notes=transfer_notes,
        )
        return connection, flags

    def _cluster_packages(self, packages: List[Package]) -> List[Package]:
        """
        Near-price clusters: within ±1000 RUB or ~3% -> group under a leader (alternatives list).
        """
        if not packages:
            return []

        # Sort packages by full_basket_rub ASC
        sorted_pkgs = sorted(packages, key=lambda p: p.full_basket_rub)
        clustered: List[Package] = []

        for p in sorted_pkgs:
            placed = False
            for leader in clustered:
                price_diff = abs(p.full_basket_rub - leader.full_basket_rub)
                percent_diff = price_diff / leader.full_basket_rub if leader.full_basket_rub > 0 else 0
                if price_diff <= 1000.0 or percent_diff <= 0.03:
                    # Place as alternative to leader
                    alt_data = {
                        "id": p.id,
                        "class": p.package_class.value,
                        "full_basket_rub": p.full_basket_rub,
                        "carrier": p.segments[0].carrier,
                        "flags": p.flags,
                    }
                    leader.cluster_alternatives.append(alt_data)
                    placed = True
                    break
            if not placed:
                clustered.append(p)

        return clustered

    def _sort_recommend_tab(self, packages: List[Package]) -> List[Package]:
        """
        recommend tab sort:
        full_basket_rub ASC.
        Tie-breaks: unified > no airport change > shorter layover / fewer flags
        """
        def sort_key(p: Package):
            is_unified = 0 if p.package_class == PackageClass.UNIFIED else 1
            has_airport_change = 1 if p.connection.airport_change else 0
            flags_count = len(p.flags)
            return (p.full_basket_rub, is_unified, has_airport_change, flags_count)

        return sorted(packages, key=sort_key)

    async def search_routes(self, request: RouteSearchRequest) -> RouteSearchResponse:
        empty_reasons: List[str] = []
        origin = request.origin.upper()
        hub = request.hub.upper()
        destination = request.destination.upper()
        dep_date = request.departure_date
        dep_date_str = dep_date.strftime("%Y-%m-%d")

        manual_url = build_aviasales_deep_link(
            origin=origin,
            destination=destination,
            depart_date_str=dep_date_str,
            passengers=request.passengers.total,
        )

        if not self.client.is_configured:
            empty_reasons.append("Не задан AVIASALES_TOKEN — скопируйте .env.example в .env и впишите токен")
            return RouteSearchResponse(
                query=request.model_dump(),
                tabs=TabsResult(recommend=[], risk=[]),
                empty_reasons=empty_reasons,
                manual_search_url=manual_url,
            )

        # 1. Fetch unified candidate prices: MOW -> HKT directly (through Istanbul or direct)
        unified_raw: List[Dict[str, Any]] = []
        try:
            unified_raw = await self.client.get_latest_prices(
                origin=origin,
                destination=destination,
                depart_date=dep_date_str,
                currency="rub",
                limit=30,
            )
        except AviasalesAPIError as e:
            empty_reasons.append(f"unified_leg_error: {str(e)}")

        # 2. Fetch leg 1: MOW -> IST (or SAW)
        leg1_raw: List[Dict[str, Any]] = []
        try:
            leg1_raw = await self.client.get_latest_prices(
                origin=origin,
                destination=hub,
                depart_date=dep_date_str,
                currency="rub",
                limit=30,
            )
        except AviasalesAPIError as e:
            empty_reasons.append(f"leg1_error_{origin}_{hub}: {str(e)}")

        # 3. Fetch leg 2: IST (or SAW) -> HKT
        leg2_raw: List[Dict[str, Any]] = []
        try:
            leg2_raw = await self.client.get_latest_prices(
                origin=hub,
                destination=destination,
                depart_date=dep_date_str,
                currency="rub",
                limit=30,
            )
        except AviasalesAPIError as e:
            empty_reasons.append(f"leg2_error_{hub}_{destination}: {str(e)}")

        # Pick best candidates
        picked_unified = self._filter_and_pick_leg_prices(
            unified_raw,
            target_date=dep_date,
            max_changes=request.max_changes_per_leg + 1,  # through tickets may have 1 change at IST
            day_window=5,
        )
        picked_leg1 = self._filter_and_pick_leg_prices(
            leg1_raw,
            target_date=dep_date,
            max_changes=request.max_changes_per_leg,
            day_window=5,
        )
        picked_leg2 = self._filter_and_pick_leg_prices(
            leg2_raw,
            target_date=dep_date,
            max_changes=request.max_changes_per_leg,
            day_window=5,
        )

        all_packages: List[Package] = []
        pax_count = request.passengers.total

        # Build unified packages
        for idx, u in enumerate(picked_unified[:5]):
            carrier = u.get("airline") or u.get("gate")
            bare_price = float(u.get("value", 0))
            full_basket, confidence, basket_flags = calculate_basket_price(
                fare_per_pax=bare_price,
                num_pax=pax_count,
                num_legs=1,  # 1 through ticket
                carrier=carrier,
                baggage=request.baggage,
                seats=request.seats,
            )

            flags = [
                "Единый сквозной билет (one booking)",
                "Багаж: обычно сквозной до конечной — уточните при регистрации",
            ]
            flags.extend(basket_flags)

            seg = FlightSegment(
                from_airport=u.get("origin_airport") or u.get("origin") or origin,
                to_airport=u.get("destination_airport") or u.get("destination") or destination,
                date=u.get("depart_date") or dep_date_str,
                dep_time=None,
                arr_time=None,
                carrier=carrier,
                airline_name=carrier,
                flight_number=u.get("flight_number"),
                changes=u.get("number_of_changes", 1),
                price_rub=bare_price,
                source="aviasales_data_api",
                found_at=u.get("created_at"),
            )

            conn = ConnectionInfo(
                hub=hub,
                airport_change=False,
                from_airport=hub,
                to_airport=hub,
                duration_min=None,
                preset_ok=True,  # Unified short connections not filtered by assembly floors
                transfer_notes="Короткая стыковка авиакомпании (гарантирована перевозчиком)",
            )

            cta = build_dual_cta(
                package_class=PackageClass.UNIFIED,
                origin=origin,
                destination=destination,
                hub=hub,
                leg1_date=seg.date,
                leg2_date=seg.date,
                carrier=carrier,
                passengers=pax_count,
                full_basket_rub=full_basket,
                airline_price_rub=None,
            )

            pkg = Package(
                id=f"UNIFIED-{idx+1}-{seg.from_airport}-{seg.to_airport}-{seg.date}",
                package_class=PackageClass.UNIFIED,
                segments=[seg],
                connection=conn,
                bare_fare_rub=bare_price * pax_count,
                full_basket_rub=full_basket,
                currency="RUB",
                price_confidence=confidence,
                flags=flags,
                cluster_alternatives=[],
                cta=cta,
                disclaimer_required=True,
            )
            all_packages.append(pkg)

        # Build assembly packages (MOW->IST + IST->HKT)
        assembly_created = 0
        for l1 in picked_leg1[:6]:
            for l2 in picked_leg2[:6]:
                carrier1 = l1.get("airline") or l1.get("gate")
                carrier2 = l2.get("airline") or l2.get("gate")
                bare1 = float(l1.get("value", 0))
                bare2 = float(l2.get("value", 0))
                bare_fare_total = (bare1 + bare2) * pax_count

                # Calculate basket for 2 separate legs
                basket1, conf1, flags_b1 = calculate_basket_price(
                    fare_per_pax=bare1,
                    num_pax=pax_count,
                    num_legs=1,
                    carrier=carrier1,
                    baggage=request.baggage,
                    seats=request.seats,
                )
                basket2, conf2, flags_b2 = calculate_basket_price(
                    fare_per_pax=bare2,
                    num_pax=pax_count,
                    num_legs=1,
                    carrier=carrier2,
                    baggage=request.baggage,
                    seats=request.seats,
                )
                full_basket = basket1 + basket2
                confidence = PriceConfidence.PARTIAL if (conf1 == PriceConfidence.PARTIAL or conf2 == PriceConfidence.PARTIAL) else PriceConfidence.EXACT

                conn, conn_flags = self._evaluate_connection(
                    leg1=l1,
                    leg2=l2,
                    preset=request.layover_preset,
                    allow_airport_change=request.allow_airport_change,
                )

                flags = [
                    "Два раздельных билета (self-transfer)",
                    "При опоздании на первый рейс второй билет сгорает без компенсации",
                ]
                if request.baggage not in (BaggageChoice.NONE, BaggageChoice.CABIN_ONLY):
                    flags.append("Багаж: в Стамбуле нужно получить и заново сдать багаж на следующий рейс")
                flags.extend(conn_flags)
                flags.extend(flags_b1)
                flags.extend(flags_b2)

                seg1 = FlightSegment(
                    from_airport=l1.get("origin_airport") or l1.get("origin") or origin,
                    to_airport=l1.get("destination_airport") or l1.get("destination") or hub,
                    date=l1.get("depart_date") or dep_date_str,
                    dep_time=None,
                    arr_time=None,
                    carrier=carrier1,
                    airline_name=carrier1,
                    flight_number=l1.get("flight_number"),
                    changes=l1.get("number_of_changes", 0),
                    price_rub=bare1,
                    source="aviasales_data_api",
                    found_at=l1.get("created_at"),
                )
                seg2 = FlightSegment(
                    from_airport=l2.get("origin_airport") or l2.get("origin") or hub,
                    to_airport=l2.get("destination_airport") or l2.get("destination") or destination,
                    date=l2.get("depart_date") or dep_date_str,
                    dep_time=None,
                    arr_time=None,
                    carrier=carrier2,
                    airline_name=carrier2,
                    flight_number=l2.get("flight_number"),
                    changes=l2.get("number_of_changes", 0),
                    price_rub=bare2,
                    source="aviasales_data_api",
                    found_at=l2.get("created_at"),
                )

                cta = build_dual_cta(
                    package_class=PackageClass.ASSEMBLY,
                    origin=origin,
                    destination=destination,
                    hub=hub,
                    leg1_date=seg1.date,
                    leg2_date=seg2.date,
                    carrier=carrier1,
                    passengers=pax_count,
                    full_basket_rub=full_basket,
                )

                assembly_created += 1
                pkg = Package(
                    id=f"ASSEMBLY-{assembly_created}-{seg1.from_airport}-{seg2.to_airport}-{seg1.date}",
                    package_class=PackageClass.ASSEMBLY,
                    segments=[seg1, seg2],
                    connection=conn,
                    bare_fare_rub=bare_fare_total,
                    full_basket_rub=full_basket,
                    currency="RUB",
                    price_confidence=confidence,
                    flags=list(dict.fromkeys(flags)),  # remove duplicates preserving order
                    cluster_alternatives=[],
                    cta=cta,
                    disclaimer_required=True,
                )
                all_packages.append(pkg)

        if not picked_leg1 and not picked_unified:
            empty_reasons.append("no_cache_for_leg_MOW_IST")
        if not picked_leg2 and not picked_unified:
            empty_reasons.append("no_cache_for_leg_IST_HKT")

        # Separate into recommend and risk tabs
        recommend_list: List[Package] = []
        risk_list: List[Package] = []

        for p in all_packages:
            if p.package_class == PackageClass.UNIFIED:
                recommend_list.append(p)
            else:
                # Assembly
                if p.connection.preset_ok and not p.connection.airport_change:
                    recommend_list.append(p)
                else:
                    risk_list.append(p)

        # Cluster near prices
        recommend_clustered = self._cluster_packages(recommend_list)
        risk_clustered = self._cluster_packages(risk_list)

        # Sort recommend tab with tie-breaks
        recommend_sorted = self._sort_recommend_tab(recommend_clustered)
        risk_sorted = sorted(risk_clustered, key=lambda p: p.full_basket_rub)

        tabs = TabsResult(recommend=recommend_sorted, risk=risk_sorted)

        # Neighbor dates (only if requested)
        neighbor_dates_result: Optional[List[NeighborDateResult]] = None
        if request.include_neighbor_dates:
            neighbor_dates_result = []
            for diff in (-1, 1):
                n_date = dep_date + timedelta(days=diff)
                n_date_str = n_date.strftime("%Y-%m-%d")
                
                # Check cache for MOW->IST and IST->HKT for neighbor date
                m_prices = [p for p in leg1_raw if p.get("depart_date") == n_date_str]
                h_prices = [p for p in leg2_raw if p.get("depart_date") == n_date_str]
                
                cheapest_n = None
                if m_prices and h_prices:
                    cheapest_n = (min(float(p.get("value", 999999)) for p in m_prices) +
                                  min(float(p.get("value", 999999)) for p in h_prices)) * pax_count

                neighbor_dates_result.append(NeighborDateResult(
                    date=n_date_str,
                    day_diff=diff,
                    cheapest_price_rub=cheapest_n,
                    packages_count=len(m_prices) * len(h_prices),
                ))

        return RouteSearchResponse(
            query=request.model_dump(),
            tabs=tabs,
            neighbor_dates=neighbor_dates_result,
            empty_reasons=empty_reasons,
            manual_search_url=manual_url,
        )


flight_service = FlightSearchService()
