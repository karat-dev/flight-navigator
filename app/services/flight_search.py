import asyncio
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
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
    DateMatch,
    Freshness,
    RulesConfidence,
)
from app.services.aviasales_client import (
    aviasales_client,
    AviasalesDataClient,
    AviasalesAPIError,
    AGENCY_GATES,
)
from app.services.pricing import BasketQuote, calculate_basket_price
from app.services.cta import build_dual_cta, build_aviasales_deep_link, build_verify_price_cta
from app.services.freshness import classify_freshness
from app.services.airline_rules import exclusion_reason, get_airline, worst_confidence
from app.data.hubs import HubSpec, resolve_hub_code


@dataclass
class DirectionOutcome:
    tabs: TabsResult
    empty_reasons: List[str]
    neighbor_dates: Optional[List[NeighborDateResult]]
    comparison_note: Optional[str]
    exclusions: List[str]
    manual_url: str


def hubs_for_request(request: RouteSearchRequest) -> Tuple[List[HubSpec], List[str]]:
    if request.hubs:
        codes = [code.strip().upper() for code in request.hubs if code and code.strip()]
    else:
        codes = [request.hub.upper()]
    found: List[HubSpec] = []
    unknown: List[str] = []
    seen = set()
    for code in codes:
        spec = resolve_hub_code(code)
        if spec is None:
            unknown.append(code)
            continue
        if spec.code in seen:
            continue
        seen.add(spec.code)
        found.append(spec)
    return found, unknown


class FlightSearchService:
    def __init__(self, client: Optional[AviasalesDataClient] = None, now_fn=None):
        self.client = client or aviasales_client
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def _parse_date(self, d_str: str) -> Optional[date]:
        try:
            return datetime.strptime(d_str[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    def _price_date_flags(self, requested: date, priced: date) -> List[str]:
        if priced == requested:
            return []
        return [
            (
                f"Цена найдена на {priced.strftime('%d.%m.%Y')}. "
                f"Запрошенная дата — {requested.strftime('%d.%m.%Y')}. Это соседняя дата."
            ),
            "Ссылка «проверить актуальную цену» открывает запрошенную дату.",
        ]

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

        # Exact-date fares stay ahead of neighbouring dates so a nearby day does not take their slot.
        valid.sort(key=lambda x: (0 if x["day_diff"] == 0 else 1, x["price"], x["day_diff"]))
        return [v["item"] for v in valid]

    def _gate_is_agency(self, gate: Optional[str]) -> bool:
        if not gate:
            return False
        return gate.strip().lower() in AGENCY_GATES

    def _infer_stopover_hub(self, row: Dict[str, Any]) -> Optional[str]:
        airports = row.get("itinerary_airports") or []
        if len(airports) >= 2:
            middle = airports[1:-1]
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

    def _row_key(self, row: Dict[str, Any]) -> tuple:
        return (
            row.get("airline"),
            row.get("flight_number"),
            row.get("depart_date"),
            row.get("origin_airport") or row.get("origin"),
            row.get("destination_airport") or row.get("destination"),
            row.get("value"),
            row.get("gate"),
        )

    def _exclusion_reason_for_row(self, row: Dict[str, Any]) -> Optional[str]:
        airline = (row.get("airline") or "").upper().strip()
        name = row.get("airline_name") or ""
        flight_number = (row.get("flight_number") or "").upper().replace(" ", "")
        mahan = (
            airline == "W5"
            or "mahan" in name.lower()
            or (flight_number.startswith("W5") and airline in {"", "W5"})
        )
        if mahan:
            record = get_airline("W5")
            if record and record.get("exclusion_reason"):
                return record["exclusion_reason"]
            return "Mahan Air (W5) исключена из рекомендаций: риск санкций OFAC."
        return exclusion_reason(airline)

    def _dedupe_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        unique = []
        for row in rows:
            key = self._row_key(row)
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    def _airport_relation(self, arr: Optional[str], dep: Optional[str], hub: HubSpec) -> str:
        if not arr or not dep:
            return "unclear"
        airports = set(hub.airports)
        if arr == dep:
            if arr == hub.code and arr not in airports and len(airports) > 1:
                return "unclear"
            return "same"
        if arr in airports and dep in airports:
            return "change"
        if arr in airports or dep in airports or arr == hub.code or dep == hub.code:
            return "unclear"
        return "unclear"

    def _evaluate_connection(
        self,
        leg1: Dict[str, Any],
        leg2: Dict[str, Any],
        hub: HubSpec,
        preset: LayoverPreset,
        allow_airport_change: bool,
        times_unknown: bool,
    ) -> Tuple[ConnectionInfo, List[str], bool]:
        """Returns connection, flags, and whether the pair must be dropped."""
        arr_airport = leg1.get("destination_airport") or leg1.get("destination")
        dep_airport = leg2.get("origin_airport") or leg2.get("origin")
        relation = self._airport_relation(arr_airport, dep_airport, hub)
        if relation == "change" and not allow_airport_change:
            return ConnectionInfo(hub=hub.code, airport_change=True), [], True

        flags: List[str] = []
        preset_ok: Optional[bool] = None if times_unknown else True
        is_airport_change = relation == "change"

        if is_airport_change and hub.airport_change_min_hours:
            hours = int(hub.airport_change_min_hours)
            left, right = arr_airport, dep_airport
            if {arr_airport, dep_airport} == {"IST", "SAW"}:
                left, right = "IST", "SAW"
            flags.append(
                f"Смена аэропорта в {hub.name_prep}: {left} ↔ {right} (закладывайте от {hours} часов)"
            )
        elif is_airport_change:
            flags.append(
                f"Смена аэропорта в {hub.name_prep}: {arr_airport} ↔ {dep_airport}. "
                "Подтверждённого минимального времени нет."
            )
        elif relation == "unclear":
            flags.append(
                f"Аэропорты стыковки в {hub.name_prep}: {arr_airport} и {dep_airport}. "
                "Один это аэропорт или два — по кэшу неясно."
            )
            if preset_ok is True:
                preset_ok = None

        if times_unknown:
            flags.append("Время стыковки неизвестно")

        d1 = self._parse_date(leg1.get("depart_date", ""))
        d2 = self._parse_date(leg2.get("depart_date", ""))
        transfer_notes = None

        if d1 and d2:
            day_gap = (d2 - d1).days
            if day_gap < 0:
                if preset_ok is not None:
                    preset_ok = False
                flags.append("Дата вылета второго сегмента раньше даты первого")
            elif times_unknown:
                if day_gap == 0:
                    transfer_notes = "Стыковка в один календарный день (время неизвестно)"
                elif day_gap == 1:
                    transfer_notes = f"Стыковка на следующий день в {hub.name_prep}"
                else:
                    transfer_notes = f"Между сегментами {day_gap} календарных дн. (время стыковки неизвестно)"
                if is_airport_change and hub.airport_change_min_hours:
                    flags.append(
                        "Смена аэропорта без точного времени рейсов: проверьте, что запаса хватает"
                    )
            elif day_gap == 0:
                transfer_notes = f"Вылет второго сегмента в тот же день ({hub.name_ru})"
                if is_airport_change:
                    flags.append("Смена аэропорта в один день: проверьте расписание перед оплатой")
            elif day_gap == 1:
                transfer_notes = f"Стыковка на следующий день в {hub.name_prep}"
                if preset == LayoverPreset.FASTER:
                    preset_ok = False
            else:
                transfer_notes = f"Длинная пересадка в {hub.name_prep} ({day_gap} дн.)"
                if preset != LayoverPreset.BUFFER_12H:
                    preset_ok = False

        connection = ConnectionInfo(
            hub=hub.code,
            airport_change=is_airport_change,
            from_airport=arr_airport,
            to_airport=dep_airport,
            duration_min=None,
            preset_ok=preset_ok,
            transfer_notes=transfer_notes,
            hub_in_api=arr_airport if arr_airport in set(hub.airports) else None,
        )
        return connection, flags, False

    def _cluster_packages(self, packages: List[Package]) -> List[Package]:
        if not packages:
            return []

        sorted_pkgs = sorted(
            packages,
            key=lambda p: (0 if p.date_status == DateMatch.EXACT else 1, p.full_basket_rub),
        )
        clustered: List[Package] = []

        for p in sorted_pkgs:
            placed = False
            for leader in clustered:
                if leader.date_status != p.date_status:
                    continue
                price_diff = abs(p.full_basket_rub - leader.full_basket_rub)
                percent_diff = price_diff / leader.full_basket_rub if leader.full_basket_rub > 0 else 0
                if price_diff <= 1000.0 or percent_diff <= 0.03:
                    seg = p.segments[0]
                    leader.cluster_alternatives.append({
                        "id": p.id,
                        "class": p.package_class.value,
                        "full_basket_rub": p.full_basket_rub,
                        "carrier": seg.carrier,
                        "date": seg.price_date or seg.date,
                        "requested_date": p.requested_date,
                        "date_status": p.date_status.value,
                        "freshness": p.freshness.value,
                        "gate": seg.gate,
                        "flags": p.flags,
                    })
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
        if self._is_through_fare(package):
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
            neighbour = 1 if p.date_status == DateMatch.NEIGHBOUR else 0
            is_assembly = 1 if p.package_class == PackageClass.ASSEMBLY else 0
            is_unverified = 1 if p.package_class == PackageClass.THROUGH_UNVERIFIED else 0
            has_airport_change = 1 if p.connection.airport_change else 0
            has_visa_warning = 1 if self._has_visa_warning(p) else 0
            layover = self._estimated_layover_minutes(p)
            return (
                neighbour,
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
        requested: date,
    ) -> FlightSegment:
        parsed = self._parse_date(row.get("depart_date") or "")
        if parsed is None:
            price_d = requested
            date_status = DateMatch.NEIGHBOUR
        else:
            price_d = parsed
            date_status = DateMatch.EXACT if parsed == requested else DateMatch.NEIGHBOUR
        price_date_str = price_d.strftime("%Y-%m-%d")
        found = row.get("found_at") or row.get("created_at")
        freshness, freshness_note = classify_freshness(found, self._now())
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
            found_at=found,
            requested_date=requested.strftime("%Y-%m-%d"),
            date_status=date_status,
            freshness=Freshness(freshness),
            freshness_note=freshness_note,
        )

    def _merge_quotes(self, quotes: List[BasketQuote]) -> Tuple[float, PriceConfidence, str, List[str], list, str]:
        total = round(sum(quote.full_basket_rub for quote in quotes), 2)
        confidence = self._merge_confidence(*(quote.confidence for quote in quotes))
        rules = worst_confidence(*(quote.rules_confidence for quote in quotes))
        flags: List[str] = []
        addons = []
        for quote in quotes:
            flags.extend(quote.flags)
            addons.extend(quote.addons)
        display = " | ".join(quote.basket_display for quote in quotes if quote.basket_display)
        return total, confidence, rules, flags, addons, display

    def _package_freshness(self, segments: List[FlightSegment]) -> Tuple[Freshness, str]:
        stale = [seg for seg in segments if seg.freshness == Freshness.POSSIBLY_STALE]
        if stale:
            return Freshness.POSSIBLY_STALE, stale[0].freshness_note or "Цена возможно устарела."
        if segments and segments[0].freshness_note:
            return Freshness.FRESH, segments[0].freshness_note
        return Freshness.FRESH, ""

    def _comparison_note(self, packages: List[Package], requested: date) -> Optional[str]:
        through = [p for p in packages if self._is_through_fare(p)]
        assembly = [p for p in packages if p.package_class == PackageClass.ASSEMBLY]
        if not through or not assembly:
            return None

        def pick(group: List[Package]) -> Tuple[Package, bool]:
            exact = [p for p in group if p.date_status == DateMatch.EXACT]
            pool = exact or group
            best = min(pool, key=lambda p: p.full_basket_rub)
            return best, not bool(exact)

        through_best, through_neighbour = pick(through)
        assembly_best, assembly_neighbour = pick(assembly)
        diff = assembly_best.full_basket_rub - through_best.full_basket_rub
        if diff > 0:
            relation = f"сквозной дешевле на {abs(diff):.0f} ₽"
        elif diff < 0:
            relation = f"сборка дешевле на {abs(diff):.0f} ₽"
        else:
            relation = "рублёвая часть совпала"
        bits = [
            (
                f"Сравнение на {requested.isoformat()}: сквозной от {through_best.full_basket_rub:.0f} ₽, "
                f"сборка от {assembly_best.full_basket_rub:.0f} ₽, {relation}."
            )
        ]
        if through_neighbour or assembly_neighbour:
            bits.append(
                f"В сравнении цены с соседних дат. Запрошенная дата — {requested.strftime('%d.%m.%Y')}."
            )
        if (
            through_best.price_confidence == PriceConfidence.UNKNOWN
            or assembly_best.price_confidence == PriceConfidence.UNKNOWN
        ):
            bits.append("Рублёвая часть не включает неизвестный багаж или место.")
        exact_prices = [p.full_basket_rub for p in packages if p.date_status == DateMatch.EXACT]
        neighbour_prices = [p.full_basket_rub for p in packages if p.date_status == DateMatch.NEIGHBOUR]
        if exact_prices and neighbour_prices and min(neighbour_prices) < min(exact_prices):
            bits.append("В кэше есть более низкая цена на соседнюю дату, она помечена отдельно.")
        bits.append("Сборка — отдельные билеты.")
        return " ".join(bits)

    async def _fetch_map(
        self,
        pairs: List[Tuple[str, str]],
        travel_date: date,
        empty_reasons: List[str],
    ) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
        async def one(pair: Tuple[str, str]):
            origin, dest = pair
            try:
                rows, _source = await self.client.fetch_route_prices(origin, dest, travel_date)
                return pair, rows, None
            except AviasalesAPIError as exc:
                return pair, [], str(exc)

        fetched: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        results = await asyncio.gather(*(one(pair) for pair in pairs))
        for pair, rows, err in results:
            fetched[pair] = rows
            if err:
                empty_reasons.append(f"route_error_{pair[0]}_{pair[1]}: {err}")
        return fetched

    def _route_pairs(
        self,
        origin: str,
        destination: str,
        hubs: List[HubSpec],
    ) -> List[Tuple[str, str]]:
        pairs = [(origin, destination)]
        for hub in hubs:
            for code in hub.search_codes:
                if code != origin:
                    pairs.append((origin, code))
                if code != destination:
                    pairs.append((code, destination))
        unique: List[Tuple[str, str]] = []
        seen = set()
        for pair in pairs:
            if pair in seen:
                continue
            seen.add(pair)
            unique.append(pair)
        return unique

    async def _search_direction(
        self,
        request: RouteSearchRequest,
        origin: str,
        destination: str,
        travel_date: date,
        hubs: List[HubSpec],
    ) -> DirectionOutcome:
        empty_reasons: List[str] = []
        pax_count = request.passengers.total
        travel_str = travel_date.strftime("%Y-%m-%d")
        manual_url = build_aviasales_deep_link(
            origin=origin,
            destination=destination,
            depart_date_str=travel_str,
            passengers=pax_count,
        )
        pairs = self._route_pairs(origin, destination, hubs)
        fetched = await self._fetch_map(pairs, travel_date, empty_reasons)

        def picked_for(pair: Tuple[str, str], through: bool) -> List[Dict[str, Any]]:
            rows = fetched.get(pair, [])
            return self._filter_and_pick_leg_prices(
                rows,
                target_date=travel_date,
                max_changes=request.max_changes_per_leg + (1 if through else 0),
                day_window=2 if through else 5,
            )

        through_rows = picked_for((origin, destination), through=True)[:8]
        excluded: Dict[str, set] = {}
        all_packages: List[Package] = []

        def remember_excluded(row: Dict[str, Any]) -> bool:
            reason = self._exclusion_reason_for_row(row)
            if not reason:
                return False
            excluded.setdefault(reason, set()).add(self._row_key(row))
            return True

        for idx, row in enumerate(through_rows):
            if remember_excluded(row):
                continue
            verified = self._is_verified_unified(row)
            pkg_class = PackageClass.UNIFIED if verified else PackageClass.THROUGH_UNVERIFIED
            carrier = row.get("airline")
            gate = row.get("gate")
            api_changes = int(row.get("number_of_changes", 0))
            duration_minutes = int(row["duration"]) if row.get("duration") is not None else None
            bare_price = float(row.get("value", 0))
            quote = calculate_basket_price(
                fare_per_pax=bare_price,
                num_pax=pax_count,
                num_legs=1,
                carrier=carrier,
                baggage=request.baggage,
                seats=request.seats,
                separate_tickets=False,
            )
            seg = self._segment_from_row(row, origin, destination, travel_date)
            flags: List[str] = []
            if verified:
                flags.append("Единый сквозной билет (one booking)")
                flags.append("Багаж: обычно сквозной до конечной — уточните при регистрации")
            else:
                flags.append("Сквозной тариф от агентства — может оказаться двумя билетами, проверьте")
                if gate:
                    flags.append(f"В кэше указано агентство (gate): {gate}")
            priced = self._parse_date(seg.price_date or seg.date) or travel_date
            flags.extend(self._price_date_flags(travel_date, priced))
            flags.extend(self._duration_flags(duration_minutes))
            flags.extend(quote.flags)

            stopover = self._infer_stopover_hub(row)
            selected_codes = {code for hub in hubs for code in hub.search_codes}
            if stopover and stopover not in selected_codes:
                flags.append(f"Стыковка в кэше: {stopover}.")
            elif api_changes >= 1 and not stopover:
                flags.append(
                    f"По кэшу Data API: пересадок {api_changes}; аэропорт стыковки в ответе не указан"
                )

            conn = ConnectionInfo(
                hub=stopover,
                airport_change=False,
                from_airport=seg.from_airport,
                to_airport=seg.to_airport,
                duration_min=None,
                preset_ok=True if verified else None,
                transfer_notes=f"Сквозной тариф {origin}–{destination}",
                hub_in_api=stopover,
                changes_in_api=api_changes,
            )
            verify = build_verify_price_cta(
                origin=origin,
                destination=destination,
                depart_date_str=travel_str,
                passengers=pax_count,
            )
            cta = build_dual_cta(
                package_class=pkg_class,
                origin=origin,
                destination=destination,
                hub=hubs[0].code if hubs else request.hub.upper(),
                leg1_date=seg.price_date or seg.date,
                leg2_date=seg.price_date or seg.date,
                carrier=carrier,
                passengers=pax_count,
                full_basket_rub=quote.full_basket_rub,
                airline_price_rub=None,
            )
            freshness, freshness_note = self._package_freshness([seg])
            all_packages.append(
                Package(
                    id=f"THROUGH-{idx + 1}-{seg.from_airport}-{seg.to_airport}-{seg.date}",
                    package_class=pkg_class,
                    segments=[seg],
                    connection=conn,
                    bare_fare_rub=bare_price * pax_count,
                    full_basket_rub=quote.full_basket_rub,
                    basket_display=quote.basket_display,
                    currency="RUB",
                    price_confidence=quote.confidence,
                    rules_confidence=RulesConfidence(quote.rules_confidence),
                    addons=quote.addons,
                    freshness=freshness,
                    freshness_note=freshness_note,
                    date_status=seg.date_status,
                    requested_date=travel_str,
                    verify_price=verify,
                    flags=list(dict.fromkeys(flags)),
                    cluster_alternatives=[],
                    cta=cta,
                    disclaimer_required=True,
                )
            )

        assembly_created = 0
        for hub in hubs:
            leg1_rows: List[Dict[str, Any]] = []
            leg2_rows: List[Dict[str, Any]] = []
            for code in hub.search_codes:
                if code != origin:
                    leg1_rows.extend(picked_for((origin, code), through=False))
                if code != destination:
                    leg2_rows.extend(picked_for((code, destination), through=False))
            leg1_pool = self._dedupe_rows(leg1_rows)[:8]
            leg2_pool = self._dedupe_rows(leg2_rows)[:8]
            for leg1 in leg1_pool:
                for leg2 in leg2_pool:
                    if remember_excluded(leg1) or remember_excluded(leg2):
                        continue
                    d1 = self._parse_date(leg1.get("depart_date", ""))
                    d2 = self._parse_date(leg2.get("depart_date", ""))
                    if d1 and d2 and d2 < d1:
                        continue
                    arr = leg1.get("destination_airport") or leg1.get("destination")
                    dep = leg2.get("origin_airport") or leg2.get("origin")
                    carrier1 = leg1.get("airline")
                    carrier2 = leg2.get("airline")
                    bare1 = float(leg1.get("value", 0))
                    bare2 = float(leg2.get("value", 0))
                    quote1 = calculate_basket_price(
                        fare_per_pax=bare1,
                        num_pax=pax_count,
                        num_legs=1,
                        carrier=carrier1,
                        baggage=request.baggage,
                        seats=request.seats,
                        separate_tickets=True,
                    )
                    quote2 = calculate_basket_price(
                        fare_per_pax=bare2,
                        num_pax=pax_count,
                        num_legs=1,
                        carrier=carrier2,
                        baggage=request.baggage,
                        seats=request.seats,
                        separate_tickets=True,
                    )
                    full_basket, confidence, rules, basket_flags, addons, display = self._merge_quotes([quote1, quote2])
                    seg1 = self._segment_from_row(leg1, origin, arr or hub.code, travel_date)
                    seg2 = self._segment_from_row(leg2, dep or hub.code, destination, travel_date)
                    times_unknown = seg1.dep_time is None or seg2.dep_time is None
                    conn, conn_flags, drop = self._evaluate_connection(
                        leg1=leg1,
                        leg2=leg2,
                        hub=hub,
                        preset=request.layover_preset,
                        allow_airport_change=request.allow_airport_change,
                        times_unknown=times_unknown,
                    )
                    if drop:
                        continue
                    flags = [
                        "Два раздельных билета (self-transfer)",
                        "При опоздании на первый рейс второй билет сгорает без компенсации",
                    ]
                    flags.extend(self._price_date_flags(travel_date, self._parse_date(seg1.price_date or seg1.date) or travel_date))
                    flags.extend(self._price_date_flags(travel_date, self._parse_date(seg2.price_date or seg2.date) or travel_date))
                    if request.baggage not in (BaggageChoice.NONE, BaggageChoice.CABIN_ONLY):
                        flags.append("Багаж на самостоятельной стыковке нужно получить и сдать заново")
                    flags.extend(conn_flags)
                    flags.extend(basket_flags)
                    flags.extend(self._duration_flags(seg1.duration_minutes))
                    flags.extend(self._duration_flags(seg2.duration_minutes))
                    verify = build_verify_price_cta(
                        origin=origin,
                        destination=destination,
                        depart_date_str=travel_str,
                        passengers=pax_count,
                        hub=seg1.to_airport,
                        second_depart_date_str=travel_str,
                        second_origin=seg2.from_airport,
                    )
                    cta = build_dual_cta(
                        package_class=PackageClass.ASSEMBLY,
                        origin=origin,
                        destination=destination,
                        hub=seg1.to_airport,
                        leg1_date=seg1.price_date or seg1.date,
                        leg2_date=seg2.price_date or seg2.date,
                        carrier=carrier1,
                        passengers=pax_count,
                        full_basket_rub=full_basket,
                        leg2_origin=seg2.from_airport,
                    )
                    date_status = (
                        DateMatch.NEIGHBOUR
                        if seg1.date_status == DateMatch.NEIGHBOUR or seg2.date_status == DateMatch.NEIGHBOUR
                        else DateMatch.EXACT
                    )
                    freshness, freshness_note = self._package_freshness([seg1, seg2])
                    assembly_created += 1
                    all_packages.append(
                        Package(
                            id=f"ASSEMBLY-{hub.code}-{assembly_created}-{seg1.from_airport}-{seg2.to_airport}-{seg1.date}",
                            package_class=PackageClass.ASSEMBLY,
                            segments=[seg1, seg2],
                            connection=conn,
                            bare_fare_rub=(bare1 + bare2) * pax_count,
                            full_basket_rub=full_basket,
                            basket_display=display,
                            currency="RUB",
                            price_confidence=confidence,
                            rules_confidence=RulesConfidence(rules),
                            addons=addons,
                            freshness=freshness,
                            freshness_note=freshness_note,
                            date_status=date_status,
                            requested_date=travel_str,
                            verify_price=verify,
                            flags=list(dict.fromkeys(flags)),
                            cluster_alternatives=[],
                            cta=cta,
                            disclaimer_required=True,
                        )
                    )

        leg1_any = False
        leg2_any = False
        for hub in hubs:
            for code in hub.search_codes:
                if code != origin and picked_for((origin, code), through=False):
                    leg1_any = True
                if code != destination and picked_for((code, destination), through=False):
                    leg2_any = True
        if hubs and not leg1_any:
            empty_reasons.append("no_leg1_in_window")
            empty_reasons.append("no_leg1_for_assembly")
        if hubs and not leg2_any:
            empty_reasons.append("no_leg2_in_window")
            empty_reasons.append("no_leg2_for_assembly")
        if hubs and leg1_any and leg2_any and assembly_created == 0:
            empty_reasons.append("no_assembly_pairs_after_filters")

        exclusions = [
            f"{reason} В кэше было вариантов: {len(keys)}. В рекомендации и во вкладку риска они не попадают."
            for reason, keys in excluded.items()
        ]
        if exclusions and not all_packages:
            empty_reasons.append("all_cached_options_excluded")

        recommend_list: List[Package] = []
        risk_list: List[Package] = []
        for package in all_packages:
            if self._is_through_fare(package):
                recommend_list.append(package)
            elif package.connection.preset_ok is True and not package.connection.airport_change:
                recommend_list.append(package)
            else:
                risk_list.append(package)

        recommend_sorted = self._sort_recommend_tab(self._cluster_packages(recommend_list))
        risk_sorted = sorted(
            self._cluster_packages(risk_list),
            key=lambda p: (0 if p.date_status == DateMatch.EXACT else 1, p.full_basket_rub),
        )

        has_assembly_in_recommend = any(p.package_class == PackageClass.ASSEMBLY for p in recommend_sorted)
        if not has_assembly_in_recommend:
            assemblies = sorted(
                [p for p in all_packages if p.package_class == PackageClass.ASSEMBLY],
                key=lambda p: (0 if p.date_status == DateMatch.EXACT else 1, p.full_basket_rub),
            )
            if assemblies:
                best_asm = assemblies[0]
                if best_asm not in recommend_sorted:
                    best_asm.flags = list(dict.fromkeys(
                        best_asm.flags + ["Показан для сравнения со сквозным тарифом"]
                    ))
                    recommend_sorted.append(best_asm)
                    recommend_sorted = self._sort_recommend_tab(recommend_sorted)

        neighbor_dates_result: Optional[List[NeighborDateResult]] = None
        if request.include_neighbor_dates:
            leg1_raw: List[Dict[str, Any]] = []
            leg2_raw: List[Dict[str, Any]] = []
            for hub in hubs:
                for code in hub.search_codes:
                    if code != origin:
                        leg1_raw.extend(fetched.get((origin, code), []))
                    if code != destination:
                        leg2_raw.extend(fetched.get((code, destination), []))
            neighbor_dates_result = []
            for diff in (-1, 1):
                n_date = travel_date + timedelta(days=diff)
                n_date_str = n_date.strftime("%Y-%m-%d")
                m_prices = [
                    row for row in leg1_raw
                    if row.get("depart_date") == n_date_str and not self._exclusion_reason_for_row(row)
                ]
                h_prices = [
                    row for row in leg2_raw
                    if row.get("depart_date") == n_date_str and not self._exclusion_reason_for_row(row)
                ]
                cheapest = None
                if m_prices and h_prices:
                    cheapest = (
                        min(float(row.get("value", 999999)) for row in m_prices)
                        + min(float(row.get("value", 999999)) for row in h_prices)
                    ) * pax_count
                neighbor_dates_result.append(
                    NeighborDateResult(
                        date=n_date_str,
                        day_diff=diff,
                        cheapest_price_rub=cheapest,
                        packages_count=len(m_prices) * len(h_prices),
                    )
                )

        return DirectionOutcome(
            tabs=TabsResult(recommend=recommend_sorted, risk=risk_sorted),
            empty_reasons=empty_reasons,
            neighbor_dates=neighbor_dates_result,
            comparison_note=self._comparison_note(all_packages, travel_date),
            exclusions=exclusions,
            manual_url=manual_url,
        )

    async def search_routes(self, request: RouteSearchRequest) -> RouteSearchResponse:
        origin = request.origin.upper()
        destination = request.destination.upper()
        hubs, unknown_hubs = hubs_for_request(request)
        pax_count = request.passengers.total
        dep_date = request.departure_date
        manual_url = build_aviasales_deep_link(
            origin=origin,
            destination=destination,
            depart_date_str=dep_date.strftime("%Y-%m-%d"),
            return_date_str=request.return_date.strftime("%Y-%m-%d") if request.return_date else None,
            passengers=pax_count,
        )
        empty_reasons = [
            f"unknown_hub_{code}" for code in unknown_hubs
        ]
        empty_reasons.extend(
            f"Хаб {code} не настроен — сборку через него не строим." for code in unknown_hubs
        )

        if not self.client.is_configured:
            empty_reasons.append("Не задан AVIASALES_TOKEN — скопируйте .env.example в .env и впишите токен")
            return_manual = None
            if request.return_date:
                return_manual = build_aviasales_deep_link(
                    origin=destination,
                    destination=origin,
                    depart_date_str=request.return_date.strftime("%Y-%m-%d"),
                    passengers=pax_count,
                )
            return RouteSearchResponse(
                query=request.model_dump(mode="json"),
                tabs=TabsResult(recommend=[], risk=[]),
                empty_reasons=empty_reasons,
                manual_search_url=manual_url,
                return_manual_search_url=return_manual,
            )

        outbound = await self._search_direction(request, origin, destination, dep_date, hubs)
        exclusions = list(outbound.exclusions)
        return_tabs = None
        return_empty: List[str] = []
        return_comparison = None
        return_neighbors = None
        return_manual = None
        if request.return_date:
            inbound = await self._search_direction(
                request,
                destination,
                origin,
                request.return_date,
                hubs,
            )
            return_tabs = inbound.tabs
            return_empty = inbound.empty_reasons
            return_comparison = inbound.comparison_note
            return_neighbors = inbound.neighbor_dates
            return_manual = inbound.manual_url
            for item in inbound.exclusions:
                if item not in exclusions:
                    exclusions.append(item)

        note = (
            "Цены из кэша — ориентир. Свежесть смотрите в freshness: fresh или possibly_stale, по found_at. "
            "Соседняя дата помечена и не подменяет запрошенную. "
            "Рублёвая корзина — это билет плюс только те доплаты, которые есть в правилах и уже в рублях. "
            "Если багаж неизвестен, сумма его не включает. "
            "Ссылка «проверить актуальную цену» ведёт на Авиасейлс с маркером и запрошенной датой."
        )
        return RouteSearchResponse(
            query=request.model_dump(mode="json"),
            note=note,
            tabs=outbound.tabs,
            neighbor_dates=outbound.neighbor_dates,
            empty_reasons=empty_reasons + outbound.empty_reasons,
            manual_search_url=manual_url,
            comparison_note=outbound.comparison_note,
            return_tabs=return_tabs,
            return_empty_reasons=return_empty,
            return_comparison_note=return_comparison,
            return_neighbor_dates=return_neighbors,
            return_manual_search_url=return_manual,
            exclusions=exclusions,
        )


flight_service = FlightSearchService()
