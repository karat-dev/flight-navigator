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
from app.services.aviasales_client import (
    aviasales_client,
    AviasalesDataClient,
    AviasalesAPIError,
    AGENCY_GATES,
)
from app.services.pricing import calculate_basket_price
from app.services.cta import build_dual_cta, build_aviasales_deep_link
from app.data.transit_guides import CITY_TRANSIT_GUIDES

IST_AREA = frozenset({"IST", "SAW"})


class FlightSearchService:
    def __init__(self, client: Optional[AviasalesDataClient] = None):
        self.client = client or aviasales_client

    def _parse_date(self, d_str: str) -> Optional[date]:
        try:
            return datetime.strptime(d_str[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    def _format_ddmm(self, d: date) -> str:
        return d.strftime("%d.%m")

    def _price_date_flags(self, requested: date, priced: date) -> List[str]:
        if priced == requested:
            return []
        return [f"Цена найдена на {self._format_ddmm(priced)}, а не на дату запроса"]

    def _filter_and_pick_leg_prices(
        self,
        prices: List[Dict[str, Any]],
        target_date: date,
        max_changes: int = 1,
        day_window: int = 5,
    ) -> List[Dict[str, Any]]:
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

            changes = int(p.get("number_of_changes", 0))
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

        valid.sort(key=lambda x: (x["price"], x["day_diff"]))
        return [v["item"] for v in valid]

    def _gate_is_agency(self, gate: Optional[str]) -> bool:
        if not gate:
            return False
        return gate.strip().lower() in AGENCY_GATES

    def _infer_stopover_hub(self, row: Dict[str, Any]) -> Optional[str]:
        airports = row.get("itinerary_airports") or []
        if len(airports) >= 2:
            middle = airports[1:-1]
            if len(middle) == 1:
                return middle[0]
            if middle:
                return middle[0]
        return None

    def _is_verified_unified(self, row: Dict[str, Any]) -> bool:
        if not row.get("airline"):
            return False
        if self._gate_is_agency(row.get("gate")):
            return False
        return True

    def _duration_flags(self, duration_minutes: Optional[int]) -> List[str]:
        if duration_minutes is None:
            return []
        if duration_minutes > 24 * 60:
            hours = round(duration_minutes / 60)
            return [f"Длительность маршрута более 24 ч (~{hours} ч по кэшу)"]
        return []

    def _merge_confidence(self, *levels: PriceConfidence) -> PriceConfidence:
        if PriceConfidence.UNKNOWN in levels:
            return PriceConfidence.UNKNOWN
        if PriceConfidence.PARTIAL in levels:
            return PriceConfidence.PARTIAL
        return PriceConfidence.EXACT

    def _evaluate_connection(
        self,
        leg1: Dict[str, Any],
        leg2: Dict[str, Any],
        preset: LayoverPreset,
        allow_airport_change: bool,
        times_unknown: bool,
    ) -> Tuple[ConnectionInfo, List[str]]:
        arr_airport = leg1.get("destination_airport") or leg1.get("destination")
        dep_airport = leg2.get("origin_airport") or leg2.get("origin")

        is_airport_change = (
            arr_airport in IST_AREA
            and dep_airport in IST_AREA
            and arr_airport != dep_airport
        )

        flags: List[str] = []
        preset_ok: Optional[bool] = None if times_unknown else True

        if is_airport_change:
            flags.append("Смена аэропорта в Стамбуле: IST ↔ SAW (закладывайте от 6 часов)")
            if not allow_airport_change and preset_ok is not None:
                preset_ok = False

        if times_unknown:
            flags.append("Время стыковки неизвестно")

        d1 = self._parse_date(leg1.get("depart_date", ""))
        d2 = self._parse_date(leg2.get("depart_date", ""))

        duration_min = None
        transfer_notes = None

        if d1 and d2:
            day_gap = (d2 - d1).days
            if day_gap < 0:
                if preset_ok is not None:
                    preset_ok = False
                flags.append("Дата вылета из Стамбула раньше даты вылета из Москвы")
            elif times_unknown:
                if day_gap == 0:
                    transfer_notes = "Стыковка в один календарный день (время неизвестно)"
                elif day_gap == 1:
                    transfer_notes = "Стыковка с ночевкой в Стамбуле (следующий день)"
                else:
                    transfer_notes = f"Между сегментами {day_gap} календарных дн. (время стыковки неизвестно)"
                if is_airport_change:
                    flags.append(
                        "Смена аэропорта IST ↔ SAW — без точного времени рейсов проверьте возможность успеть"
                    )
            elif day_gap == 0:
                transfer_notes = "Вылет из Стамбула в тот же день"
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

        hub_known = None
        if arr_airport in IST_AREA:
            hub_known = arr_airport
        elif dep_airport in IST_AREA:
            hub_known = dep_airport

        connection = ConnectionInfo(
            hub=hub_known,
            airport_change=is_airport_change,
            from_airport=arr_airport,
            to_airport=dep_airport,
            duration_min=duration_min,
            preset_ok=preset_ok,
            transfer_notes=transfer_notes,
        )
        return connection, flags

    def _cluster_packages(self, packages: List[Package]) -> List[Package]:
        if not packages:
            return []

        sorted_pkgs = sorted(packages, key=lambda p: p.full_basket_rub)
        clustered: List[Package] = []

        for p in sorted_pkgs:
            placed = False
            for leader in clustered:
                price_diff = abs(p.full_basket_rub - leader.full_basket_rub)
                percent_diff = price_diff / leader.full_basket_rub if leader.full_basket_rub > 0 else 0
                if price_diff <= 1000.0 or percent_diff <= 0.03:
                    seg = p.segments[0]
                    alt_data = {
                        "id": p.id,
                        "class": p.package_class.value,
                        "full_basket_rub": p.full_basket_rub,
                        "carrier": seg.carrier,
                        "date": seg.price_date or seg.date,
                        "gate": seg.gate,
                        "flags": p.flags,
                    }
                    leader.cluster_alternatives.append(alt_data)
                    placed = True
                    break
            if not placed:
                clustered.append(p)

        return clustered

    def _has_visa_warning(self, package: Package) -> bool:
        visa_markers = ("виз", "паспорт", "шенген")
        return any(
            any(marker in flag.lower() for marker in visa_markers)
            for flag in package.flags
        )

    def _estimated_layover_minutes(self, package: Package) -> int:
        if package.connection.duration_min is not None:
            return package.connection.duration_min
        if package.package_class in (PackageClass.UNIFIED, PackageClass.THROUGH_UNVERIFIED):
            return 0
        if len(package.segments) >= 2:
            d1 = self._parse_date(package.segments[0].date)
            d2 = self._parse_date(package.segments[1].date)
            if d1 and d2:
                return max(0, (d2 - d1).days * 24 * 60)
        return 999_999

    def _is_through_fare(self, package: Package) -> bool:
        return package.package_class in (PackageClass.UNIFIED, PackageClass.THROUGH_UNVERIFIED)

    def _sort_recommend_tab(self, packages: List[Package]) -> List[Package]:
        def sort_key(p: Package):
            is_assembly = 1 if p.package_class == PackageClass.ASSEMBLY else 0
            is_unverified = 1 if p.package_class == PackageClass.THROUGH_UNVERIFIED else 0
            has_airport_change = 1 if p.connection.airport_change else 0
            has_visa_warning = 1 if self._has_visa_warning(p) else 0
            layover = self._estimated_layover_minutes(p)
            return (
                p.full_basket_rub,
                is_assembly,
                is_unverified,
                has_airport_change,
                has_visa_warning,
                layover,
            )

        return sorted(packages, key=sort_key)

    def _segment_from_row(
        self,
        row: Dict[str, Any],
        origin: str,
        destination: str,
        dep_date_str: str,
        requested_date: date,
    ) -> FlightSegment:
        price_d = self._parse_date(row.get("depart_date") or dep_date_str)
        price_date_str = price_d.strftime("%Y-%m-%d") if price_d else dep_date_str
        return FlightSegment(
            from_airport=row.get("origin_airport") or row.get("origin") or origin,
            to_airport=row.get("destination_airport") or row.get("destination") or destination,
            date=price_date_str,
            price_date=price_date_str,
            dep_time=row.get("dep_time"),
            arr_time=None,
            carrier=row.get("airline"),
            airline_name=row.get("airline_name") or row.get("airline"),
            gate=row.get("gate"),
            flight_number=row.get("flight_number"),
            changes=int(row.get("number_of_changes", 0)),
            duration_minutes=int(row["duration"]) if row.get("duration") is not None else None,
            price_rub=float(row.get("value", 0)),
            source=f"aviasales_{row.get('api_version', 'data_api')}",
            found_at=row.get("created_at"),
        )

    async def search_routes(self, request: RouteSearchRequest) -> RouteSearchResponse:
        empty_reasons: List[str] = []
        origin = request.origin.upper()
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

        async def load_route(o: str, d: str) -> Tuple[List[Dict[str, Any]], str]:
            try:
                return await self.client.fetch_route_prices(o, d, dep_date)
            except AviasalesAPIError as e:
                empty_reasons.append(f"route_error_{o}_{d}: {str(e)}")
                return [], "error"

        unified_raw, _ = await load_route(origin, destination)
        leg_mow_ist, _ = await load_route(origin, "IST")
        leg_mow_saw, _ = await load_route(origin, "SAW")
        leg_ist_hkt, _ = await load_route("IST", destination)
        leg_saw_hkt, _ = await load_route("SAW", destination)

        picked_unified = self._filter_and_pick_leg_prices(
            unified_raw,
            target_date=dep_date,
            max_changes=request.max_changes_per_leg + 1,
            day_window=2,
        )
        picked_mow_ist = self._filter_and_pick_leg_prices(
            leg_mow_ist, dep_date, request.max_changes_per_leg, day_window=5
        )
        picked_mow_saw = self._filter_and_pick_leg_prices(
            leg_mow_saw, dep_date, request.max_changes_per_leg, day_window=5
        )
        picked_ist_hkt = self._filter_and_pick_leg_prices(
            leg_ist_hkt, dep_date, request.max_changes_per_leg, day_window=5
        )
        picked_saw_hkt = self._filter_and_pick_leg_prices(
            leg_saw_hkt, dep_date, request.max_changes_per_leg, day_window=5
        )

        all_packages: List[Package] = []
        pax_count = request.passengers.total

        for idx, u in enumerate(picked_unified[:8]):
            verified = self._is_verified_unified(u)
            pkg_class = PackageClass.UNIFIED if verified else PackageClass.THROUGH_UNVERIFIED
            carrier = u.get("airline")
            gate = u.get("gate")
            api_changes = int(u.get("number_of_changes", 0))
            duration_minutes = int(u["duration"]) if u.get("duration") is not None else None
            bare_price = float(u.get("value", 0))
            price_d = self._parse_date(u.get("depart_date") or dep_date_str) or dep_date
            price_date_str = price_d.strftime("%Y-%m-%d")

            full_basket, confidence, basket_flags = calculate_basket_price(
                fare_per_pax=bare_price,
                num_pax=pax_count,
                num_legs=1,
                carrier=carrier,
                baggage=request.baggage,
                seats=request.seats,
            )

            flags: List[str] = []
            if verified:
                flags.append("Единый сквозной билет (one booking)")
                flags.append("Багаж: обычно сквозной до конечной — уточните при регистрации")
            else:
                flags.append("Сквозной тариф от агентства — может оказаться двумя билетами, проверьте")
                if gate:
                    flags.append(f"В кэше указано агентство (gate): {gate}")

            flags.extend(self._price_date_flags(dep_date, price_d))
            flags.extend(self._duration_flags(duration_minutes))

            stopover = self._infer_stopover_hub(u)
            ist_slice = True
            if stopover and stopover not in IST_AREA:
                flags.append(f"маршрут через {stopover}")
                ist_slice = False
            elif api_changes >= 1 and not stopover:
                flags.append(
                    f"По кэшу Data API: пересадок {api_changes}; аэропорт стыковки в ответе не указан"
                )

            seg = self._segment_from_row(u, origin, destination, dep_date_str, dep_date)

            conn = ConnectionInfo(
                hub=stopover,
                airport_change=False,
                from_airport=seg.from_airport,
                to_airport=seg.to_airport,
                duration_min=None,
                preset_ok=True if verified else None,
                transfer_notes=(
                    "Сквозной тариф MOW–HKT"
                    if ist_slice
                    else f"Сквозной тариф не через IST/SAW — для сравнения, не срез «через Стамбул»"
                ),
                hub_in_api=stopover,
                changes_in_api=api_changes,
            )

            if not verified and request.baggage not in (BaggageChoice.NONE, BaggageChoice.CABIN_ONLY):
                basket_flags = [f for f in basket_flags if "сквозной" not in f.lower()]

            flags.extend(basket_flags)

            cta = build_dual_cta(
                package_class=pkg_class,
                origin=origin,
                destination=destination,
                hub=request.hub.upper(),
                leg1_date=price_date_str,
                leg2_date=price_date_str,
                carrier=carrier,
                passengers=pax_count,
                full_basket_rub=full_basket,
                airline_price_rub=None,
            )

            all_packages.append(
                Package(
                    id=f"THROUGH-{idx+1}-{seg.from_airport}-{seg.to_airport}-{price_date_str}",
                    package_class=pkg_class,
                    segments=[seg],
                    connection=conn,
                    bare_fare_rub=bare_price * pax_count,
                    full_basket_rub=full_basket,
                    currency="RUB",
                    price_confidence=confidence,
                    flags=list(dict.fromkeys(flags)),
                    cluster_alternatives=[],
                    cta=cta,
                    disclaimer_required=True,
                )
            )

        leg1_pool = picked_mow_ist + picked_mow_saw
        leg2_pool = picked_ist_hkt + picked_saw_hkt

        assembly_created = 0
        for l1 in leg1_pool[:8]:
            for l2 in leg2_pool[:8]:
                d1 = self._parse_date(l1.get("depart_date", ""))
                d2 = self._parse_date(l2.get("depart_date", ""))
                if d1 and d2 and d2 < d1:
                    continue

                arr = l1.get("destination_airport") or l1.get("destination")
                dep = l2.get("origin_airport") or l2.get("origin")
                if arr in IST_AREA and dep in IST_AREA and arr != dep:
                    if not request.allow_airport_change:
                        continue

                carrier1 = l1.get("airline")
                carrier2 = l2.get("airline")
                bare1 = float(l1.get("value", 0))
                bare2 = float(l2.get("value", 0))
                bare_fare_total = (bare1 + bare2) * pax_count

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
                confidence = self._merge_confidence(conf1, conf2)

                seg1 = self._segment_from_row(l1, origin, arr or "IST", dep_date_str, dep_date)
                seg2 = self._segment_from_row(l2, dep or "IST", destination, dep_date_str, dep_date)

                flags: List[str] = [
                    "Два раздельных билета (self-transfer)",
                    "При опоздании на первый рейс второй билет сгорает без компенсации",
                ]
                flags.extend(self._price_date_flags(dep_date, self._parse_date(seg1.price_date or seg1.date) or dep_date))
                if seg2.price_date and seg2.price_date != seg1.date:
                    d2p = self._parse_date(seg2.price_date)
                    if d2p:
                        flags.extend(self._price_date_flags(dep_date, d2p))

                times_unknown = seg1.dep_time is None or seg2.dep_time is None

                conn, conn_flags = self._evaluate_connection(
                    leg1=l1,
                    leg2=l2,
                    preset=request.layover_preset,
                    allow_airport_change=request.allow_airport_change,
                    times_unknown=times_unknown,
                )

                if request.baggage not in (BaggageChoice.NONE, BaggageChoice.CABIN_ONLY):
                    flags.append("Багаж: в Стамбуле нужно получить и заново сдать багаж на следующий рейс")
                flags.extend(conn_flags)
                flags.extend(flags_b1)
                flags.extend(flags_b2)
                flags.extend(self._duration_flags(seg1.duration_minutes))
                flags.extend(self._duration_flags(seg2.duration_minutes))

                leg1_hub = seg1.to_airport if seg1.to_airport in IST_AREA else request.hub.upper()
                leg2_hub = seg2.from_airport if seg2.from_airport in IST_AREA else request.hub.upper()
                cta = build_dual_cta(
                    package_class=PackageClass.ASSEMBLY,
                    origin=origin,
                    destination=destination,
                    hub=leg1_hub,
                    leg1_date=seg1.price_date or seg1.date,
                    leg2_date=seg2.price_date or seg2.date,
                    carrier=carrier1,
                    passengers=pax_count,
                    full_basket_rub=full_basket,
                    leg2_origin=leg2_hub,
                )

                assembly_created += 1
                all_packages.append(
                    Package(
                        id=f"ASSEMBLY-{assembly_created}-{seg1.from_airport}-{seg2.to_airport}-{seg1.date}",
                        package_class=PackageClass.ASSEMBLY,
                        segments=[seg1, seg2],
                        connection=conn,
                        bare_fare_rub=bare_fare_total,
                        full_basket_rub=full_basket,
                        currency="RUB",
                        price_confidence=confidence,
                        flags=list(dict.fromkeys(flags)),
                        cluster_alternatives=[],
                        cta=cta,
                        disclaimer_required=True,
                    )
                )

        if not picked_mow_ist and not picked_mow_saw:
            empty_reasons.append("no_leg_MOW_IST_SAW_in_window")
        if not picked_ist_hkt and not picked_saw_hkt:
            empty_reasons.append("no_leg_IST_HKT_in_window")
        if assembly_created == 0:
            if not leg1_pool:
                empty_reasons.append("no_leg1_for_assembly")
            elif not leg2_pool:
                empty_reasons.append("no_leg2_for_assembly")
            else:
                empty_reasons.append("no_assembly_pairs_after_filters")

        recommend_list: List[Package] = []
        risk_list: List[Package] = []

        for p in all_packages:
            if self._is_through_fare(p):
                recommend_list.append(p)
            elif p.connection.preset_ok is True and not p.connection.airport_change:
                recommend_list.append(p)
            else:
                risk_list.append(p)

        recommend_clustered = self._cluster_packages(recommend_list)
        risk_clustered = self._cluster_packages(risk_list)

        recommend_sorted = self._sort_recommend_tab(recommend_clustered)
        risk_sorted = sorted(risk_clustered, key=lambda p: p.full_basket_rub)

        has_assembly_in_recommend = any(p.package_class == PackageClass.ASSEMBLY for p in recommend_sorted)
        if not has_assembly_in_recommend:
            all_assemblies = sorted(
                [p for p in all_packages if p.package_class == PackageClass.ASSEMBLY],
                key=lambda p: p.full_basket_rub,
            )
            if all_assemblies:
                best_asm = all_assemblies[0]
                if best_asm not in recommend_sorted:
                    best_asm.flags = list(dict.fromkeys(best_asm.flags + ["Показан для сравнения со сквозным тарифом"]))
                    recommend_sorted.append(best_asm)
                    recommend_sorted = self._sort_recommend_tab(recommend_sorted)

        tabs = TabsResult(recommend=recommend_sorted, risk=risk_sorted)

        neighbor_dates_result: Optional[List[NeighborDateResult]] = None
        if request.include_neighbor_dates:
            neighbor_dates_result = []
            combined_leg1 = leg_mow_ist + leg_mow_saw
            combined_leg2 = leg_ist_hkt + leg_saw_hkt
            for diff in (-1, 1):
                n_date = dep_date + timedelta(days=diff)
                n_date_str = n_date.strftime("%Y-%m-%d")
                m_prices = [p for p in combined_leg1 if p.get("depart_date") == n_date_str]
                h_prices = [p for p in combined_leg2 if p.get("depart_date") == n_date_str]
                cheapest_n = None
                if m_prices and h_prices:
                    cheapest_n = (
                        min(float(p.get("value", 999999)) for p in m_prices)
                        + min(float(p.get("value", 999999)) for p in h_prices)
                    ) * pax_count
                neighbor_dates_result.append(
                    NeighborDateResult(
                        date=n_date_str,
                        day_diff=diff,
                        cheapest_price_rub=cheapest_n,
                        packages_count=len(m_prices) * len(h_prices),
                    )
                )

        note = (
            "Цены — ориентир, могут измениться к моменту оплаты. "
            "Сумма с учётом багажа и мест только там, где авиакомпания известна; иначе багаж не включён в расчёт."
        )

        return RouteSearchResponse(
            query=request.model_dump(),
            note=note,
            tabs=tabs,
            neighbor_dates=neighbor_dates_result,
            empty_reasons=empty_reasons,
            manual_search_url=manual_url,
        )


flight_service = FlightSearchService()
