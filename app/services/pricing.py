"""Full basket = ticket + required bags, only when the rules actually say so."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.flight import AddonQuote, BaggageChoice, PriceConfidence, SeatChoice
from app.services.airline_rules import (
    baggage_coverage,
    get_airline,
    worst_confidence,
)

CONF_RU = {
    "confirmed": "подтверждено",
    "approx": "приблизительно",
    "unknown": "неизвестно",
}

REQUESTED_KG = {
    BaggageChoice.NONE: 0,
    BaggageChoice.CABIN_ONLY: 0,
    BaggageChoice.CHECKED_10KG: 10,
    BaggageChoice.CHECKED_20KG: 20,
    BaggageChoice.CHECKED_23KG: 23,
    BaggageChoice.CHECKED_30KG: 30,
}


@dataclass
class BasketQuote:
    full_basket_rub: float
    confidence: PriceConfidence
    rules_confidence: str
    flags: list[str] = field(default_factory=list)
    addons: list[AddonQuote] = field(default_factory=list)
    basket_display: str = ""


def _ru(level: str) -> str:
    return CONF_RU.get(level, level)


def _family_lines(airline: dict, key: str) -> str:
    parts = []
    for family in airline.get("fare_families") or []:
        label = family.get("name") or "бренд не назван"
        item = family.get(key) or {}
        parts.append(f"{label}: {item.get('display') or 'нет данных'} ({_ru(item.get('confidence') or 'unknown')})")
    return "; ".join(parts)


def _money_phrase(item: dict) -> str:
    amount = item.get("amount")
    currency = item.get("currency")
    if amount is None or not currency:
        return item.get("display") or "сумма неизвестна"
    if item.get("confidence") == "approx":
        return f"от {amount:g} {currency}"
    return f"{amount:g} {currency}"


def _addon_from_rule(kind: str, item: dict, included_in_rub: bool) -> AddonQuote:
    return AddonQuote(
        kind=kind,
        display=item.get("display") or "",
        amount=item.get("amount"),
        currency=item.get("currency"),
        confidence=item.get("confidence") or "unknown",
        source_url=item.get("source_url"),
        checked_at=item.get("checked_at"),
        included_in_rub_total=included_in_rub,
    )


def calculate_basket_price(
    fare_per_pax: float,
    num_pax: int,
    num_legs: int,
    carrier: Optional[str],
    baggage: BaggageChoice,
    seats: SeatChoice,
    separate_tickets: bool = False,
) -> BasketQuote:
    """
    Rub total is the ticket plus only ruble add-ons we actually have.
    Foreign add-ons stay in their currency. Unknown bag or seat fees are not guessed.
    """
    ticket = round(float(fare_per_pax) * num_pax, 2)
    flags: list[str] = []
    addons: list[AddonQuote] = []
    rules_levels: list[str] = []
    unresolved = False
    foreign_required = False
    baggage_phrase = ""
    seat_phrase = ""

    requested_kg = REQUESTED_KG[baggage]
    airline = get_airline(carrier) if carrier else None

    if baggage == BaggageChoice.NONE:
        baggage_phrase = "Зарегистрированный багаж не запрашивали."
        rules_levels.append("confirmed")
    elif not carrier:
        unresolved = True
        rules_levels.append("unknown")
        baggage_phrase = "Багаж не учтён в цене: авиакомпания в кэше не указана, норму не угадываем."
        flags.append(baggage_phrase)
        addons.append(
            AddonQuote(
                kind="baggage",
                display=baggage_phrase,
                confidence="unknown",
                included_in_rub_total=False,
            )
        )
    elif airline is None:
        unresolved = True
        rules_levels.append("unknown")
        baggage_phrase = (
            f"Багаж не учтён в цене: правил для авиакомпании {carrier} в справочнике нет, доплату не угадываем."
        )
        flags.append(baggage_phrase)
        addons.append(
            AddonQuote(
                kind="baggage",
                display=baggage_phrase,
                confidence="unknown",
                included_in_rub_total=False,
            )
        )
    elif baggage == BaggageChoice.CABIN_ONLY:
        cabin_levels = [
            (family.get("cabin_baggage") or {}).get("confidence")
            for family in airline.get("fare_families") or []
        ]
        cabin_confidence = worst_confidence(*cabin_levels)
        rules_levels.append(cabin_confidence)
        summary = _family_lines(airline, "cabin_baggage")
        if cabin_confidence == "unknown":
            unresolved = True
            baggage_phrase = f"Ручная кладь: норма неизвестна ({summary}). Доплату не добавляем и не угадываем."
        else:
            baggage_phrase = (
                f"Ручная кладь: {summary}. Уверенность: {_ru(cabin_confidence)}. Отдельную доплату не добавляем."
            )
        flags.append(baggage_phrase)
        sample = (airline.get("fare_families") or [{}])[0].get("cabin_baggage") or {}
        addons.append(_addon_from_rule("baggage", {**sample, "display": baggage_phrase, "amount": None, "currency": None, "confidence": cabin_confidence}, False))
    else:
        coverage = baggage_coverage(airline, float(requested_kg))
        summary = _family_lines(airline, "checked_baggage")
        family_levels = [
            (family.get("checked_baggage") or {}).get("confidence")
            for family in airline.get("fare_families") or []
        ]
        allowance_confidence = worst_confidence(*family_levels)
        add_rule = airline.get("add_bag") or {}
        if separate_tickets and airline.get("add_bag_separate_tickets"):
            add_rule = airline["add_bag_separate_tickets"]

        if coverage == "included":
            rules_levels.append(allowance_confidence)
            baggage_phrase = (
                f"Багаж {baggage.value}: входит в известные тарифы эконома ({summary}). "
                f"Уверенность: {_ru(allowance_confidence)}. Бренд тарифа в кэше не указан, доплату не добавляем."
            )
            flags.append(baggage_phrase)
            addons.append(
                AddonQuote(
                    kind="baggage",
                    display=baggage_phrase,
                    amount=0,
                    currency="RUB",
                    confidence=allowance_confidence,
                    source_url=(airline.get("fare_families") or [{}])[0].get("checked_baggage", {}).get("source_url"),
                    checked_at=(airline.get("fare_families") or [{}])[0].get("checked_baggage", {}).get("checked_at"),
                    included_in_rub_total=True,
                )
            )
        elif coverage == "not_included":
            usable = _usable_add_bag(add_rule, requested_kg)
            if usable:
                rules_levels.append(add_rule.get("confidence") or "unknown")
                money = _money_phrase(add_rule)
                pax_note = f" × {num_pax} пасс." if num_pax > 1 else ""
                leg_note = f" × {num_legs} сегм." if num_legs > 1 else ""
                baggage_phrase = (
                    f"Багаж {baggage.value}: в известных тарифах нормы не хватает ({summary}). "
                    f"Доплата {money}{pax_note}{leg_note}, уверенность: {_ru(add_rule.get('confidence') or 'unknown')}. "
                    "В рубли не переводим, в рублёвую сумму не входит."
                )
                flags.append(baggage_phrase)
                included_in_rub = False
                if (add_rule.get("currency") or "").upper() == "RUB":
                    ticket = round(ticket + float(add_rule["amount"]) * num_pax * max(num_legs, 1), 2)
                    included_in_rub = True
                else:
                    foreign_required = True
                addons.append(_addon_from_rule("baggage", {**add_rule, "display": baggage_phrase}, included_in_rub))
            else:
                unresolved = True
                rules_levels.append("unknown")
                baggage_phrase = (
                    f"Багаж {baggage.value}: в известных тарифах нормы не хватает ({summary}). "
                    f"Доплата неизвестна ({add_rule.get('display') or 'суммы нет'}) и в итог не входит."
                )
                flags.append(baggage_phrase)
                addons.append(_addon_from_rule("baggage", {**add_rule, "display": baggage_phrase, "amount": None, "currency": None, "confidence": "unknown"}, False))
        else:
            unresolved = True
            rules_levels.append("unknown")
            extra = ""
            if add_rule.get("amount") is not None and _usable_add_bag(add_rule, requested_kg):
                extra = (
                    f" Известная доплата {_money_phrase(add_rule)} "
                    f"({_ru(add_rule.get('confidence') or 'unknown')}) в итог не входит: неизвестно, нужна ли она этому билету."
                )
                addons.append(_addon_from_rule("baggage", add_rule, False))
            elif add_rule:
                addons.append(
                    _addon_from_rule(
                        "baggage",
                        {**add_rule, "amount": None, "currency": None, "confidence": "unknown"},
                        False,
                    )
                )
            why = "зависит от тарифа, а бренд в кэше неизвестен" if coverage == "mixed" else "норма неизвестна"
            baggage_phrase = (
                f"Багаж {baggage.value}: {why} ({summary}). В сумму не включаем и не угадываем.{extra}"
            )
            flags.append(baggage_phrase)

    if seats == SeatChoice.YES:
        seat_phrase, seat_addons, seat_level, seat_complete = _seat_quote(airline, carrier, num_pax)
        flags.append(seat_phrase)
        addons.extend(seat_addons)
        rules_levels.append(seat_level)
        if not seat_complete:
            unresolved = True

    rules_confidence = worst_confidence(*rules_levels)
    if baggage == BaggageChoice.NONE and seats != SeatChoice.YES:
        confidence = PriceConfidence.EXACT
        rules_confidence = "confirmed"
    elif unresolved or rules_confidence == "unknown":
        confidence = PriceConfidence.UNKNOWN
    elif foreign_required or baggage != BaggageChoice.NONE or seats == SeatChoice.YES:
        confidence = PriceConfidence.PARTIAL
    else:
        confidence = PriceConfidence.EXACT

    pieces = [f"{ticket:.0f} ₽ за билет"]
    if baggage_phrase:
        pieces.append(baggage_phrase)
    if seat_phrase:
        pieces.append(seat_phrase)
    display = " ".join(pieces)

    return BasketQuote(
        full_basket_rub=ticket,
        confidence=confidence,
        rules_confidence=rules_confidence,
        flags=flags,
        addons=addons,
        basket_display=display,
    )


def _usable_add_bag(add_rule: dict, requested_kg: int) -> bool:
    if not add_rule or add_rule.get("amount") is None or not add_rule.get("currency"):
        return False
    if add_rule.get("confidence") == "unknown":
        return False
    cap = add_rule.get("max_piece_weight_kg")
    if cap is not None and requested_kg > float(cap):
        return False
    return True


def _seat_quote(airline: Optional[dict], carrier: Optional[str], num_pax: int) -> tuple[str, list[AddonQuote], str, bool]:
    if not carrier or airline is None:
        text = "Выбор места: цена неизвестна, в сумму не включаем."
        if carrier and airline is None:
            text = f"Выбор места для {carrier}: правил в справочнике нет, цену не угадываем."
        elif not carrier:
            text = "Выбор места: авиакомпания неизвестна, цену не угадываем."
        return (
            text,
            [AddonQuote(kind="seat", display=text, confidence="unknown", included_in_rub_total=False)],
            "unknown",
            False,
        )

    families = airline.get("fare_families") or []
    seats = [family.get("seat") or {} for family in families]
    included_vals = [seat.get("included") for seat in seats]
    summary = _family_lines(airline, "seat")
    if not seats or any(value is None for value in included_vals):
        text = f"Выбор места: неизвестно ({summary}). В сумму не включаем."
        return (
            text,
            [AddonQuote(kind="seat", display=text, confidence="unknown", source_url=(airline.get("seat_price") or {}).get("source_url"), checked_at=(airline.get("seat_price") or {}).get("checked_at"), included_in_rub_total=False)],
            "unknown",
            False,
        )
    if all(value is True for value in included_vals):
        level = worst_confidence(*[seat.get("confidence") for seat in seats])
        text = f"Выбор места входит в известные тарифы ({summary}). Уверенность: {_ru(level)}."
        return (
            text,
            [
                AddonQuote(
                    kind="seat",
                    display=text,
                    amount=0,
                    currency="RUB",
                    confidence=level,
                    source_url=seats[0].get("source_url"),
                    checked_at=seats[0].get("checked_at"),
                    included_in_rub_total=True,
                )
            ],
            level,
            level != "unknown",
        )
    if all(value is False for value in included_vals):
        amounts = [seat.get("amount") for seat in seats]
        currencies = {seat.get("currency") for seat in seats}
        if all(amount is not None for amount in amounts) and len(currencies) == 1 and len(set(amounts)) == 1:
            level = worst_confidence(*[seat.get("confidence") for seat in seats])
            money = _money_phrase(seats[0])
            pax_note = f" × {num_pax} пасс." if num_pax > 1 else ""
            text = (
                f"Выбор места платный во всех известных тарифах: {money}{pax_note}. "
                f"Уверенность: {_ru(level)}. В рубли не переводим."
            )
            return (
                text,
                [_addon_from_rule("seat", {**seats[0], "display": text}, False)],
                level,
                False,
            )
        text = f"Выбор места платный, сумма для этого билета неизвестна ({summary}). В итог не входит."
        return (
            text,
            [AddonQuote(kind="seat", display=text, confidence="unknown", source_url=seats[0].get("source_url"), checked_at=seats[0].get("checked_at"), included_in_rub_total=False)],
            "unknown",
            False,
        )

    text = f"Выбор места зависит от тарифа ({summary}). Бренд в кэше неизвестен, доплату в сумму не включаем."
    return (
        text,
        [AddonQuote(kind="seat", display=text, confidence="unknown", source_url=(airline.get("seat_price") or {}).get("source_url"), checked_at=(airline.get("seat_price") or {}).get("checked_at"), included_in_rub_total=False)],
        "unknown",
        False,
    )
