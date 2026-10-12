"""Cache age. Missing or unreadable found_at is possibly stale — we do not invent a timestamp."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_found_at(found_at: Optional[str]) -> Optional[datetime]:
    if not found_at or not isinstance(found_at, str):
        return None
    text = found_at.strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            parsed = _as_utc(parsed)
    except ValueError:
        return None
    return parsed


def classify_freshness(
    found_at: Optional[str],
    now: datetime,
    max_age_hours: Optional[int] = None,
) -> tuple[str, str]:
    hours = settings.PRICE_FRESH_HOURS if max_age_hours is None else max_age_hours
    now_utc = _as_utc(now)
    parsed = parse_found_at(found_at)
    if parsed is None:
        if found_at:
            return (
                "possibly_stale",
                "Время находки цены в кэше не разбирается — помечаем цену как возможно устаревшую.",
            )
        return (
            "possibly_stale",
            "В кэше нет found_at — помечаем цену как возможно устаревшую.",
        )
    age = now_utc - parsed
    if timedelta(0) <= age <= timedelta(hours=hours):
        return (
            "fresh",
            f"Цена в кэше свежая: found_at {found_at}. Порог свежести {hours} ч.",
        )
    return (
        "possibly_stale",
        f"Цена в кэше возможно устарела: found_at {found_at}. Порог свежести {hours} ч.",
    )
