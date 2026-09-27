from typing import List, Dict, Any, Optional, Tuple
import re
import httpx
from datetime import date, datetime

from app.core.config import settings


class AviasalesAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# OTA / agency gates — сквозной тариф не верифицируем как единый билет авиакомпании
AGENCY_GATES = frozenset(
    {
        "superkassa",
        "city.travel",
        "city travel",
        "kiwi.com",
        "trip.com",
        "onetravel",
        "gotogate",
        "mytrip",
        "edreams",
        "kupi.com",
        "aviakassa",
        "clickavia",
        "southwind",
    }
)


def _parse_depart_date_from_v3(item: Dict[str, Any]) -> Optional[str]:
    dep_at = item.get("departure_at")
    if dep_at and isinstance(dep_at, str) and len(dep_at) >= 10:
        return dep_at[:10]
    return None


def _parse_dep_time_from_v3(item: Dict[str, Any]) -> Optional[str]:
    dep_at = item.get("departure_at")
    if not dep_at or not isinstance(dep_at, str):
        return None
    try:
        # ISO with offset
        dt = datetime.fromisoformat(dep_at.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return None


def parse_airports_from_link(link: Optional[str], duration: Optional[int]) -> List[str]:
    """Extract airport sequence from Aviasales link `t=` payload after duration."""
    if not link or duration is None:
        return []
    idx = link.find("t=")
    if idx < 0:
        return []
    t = link[idx + 2 :].split("&")[0]
    dur_str = str(int(duration))
    pos = t.find(dur_str)
    if pos < 0:
        return []
    rest = t[pos + len(dur_str) :]
    return re.findall(r"[A-Z]{3}", rest)


def normalize_v3_item(item: Dict[str, Any], origin: str, destination: str) -> Dict[str, Any]:
    transfers = item.get("transfers")
    if transfers is None:
        changes = item.get("number_of_changes", 0)
    else:
        changes = int(transfers)
    duration = item.get("duration")
    link = item.get("link")
    airports = parse_airports_from_link(link, duration if duration is not None else None)
    return {
        "depart_date": _parse_depart_date_from_v3(item),
        "value": float(item.get("price") or item.get("value") or 0),
        "airline": item.get("airline"),
        "flight_number": item.get("flight_number"),
        "departure_at": item.get("departure_at"),
        "dep_time": _parse_dep_time_from_v3(item),
        "duration": duration,
        "number_of_changes": changes,
        "origin_airport": item.get("origin_airport") or item.get("origin") or origin,
        "destination_airport": item.get("destination_airport") or item.get("destination") or destination,
        "origin": origin,
        "destination": destination,
        "gate": item.get("gate"),
        "link": link,
        "created_at": item.get("created_at"),
        "api_version": "v3",
        "itinerary_airports": airports,
    }


def normalize_v2_item(item: Dict[str, Any], origin: str, destination: str) -> Dict[str, Any]:
    return {
        "depart_date": item.get("depart_date"),
        "value": float(item.get("value") or 0),
        "airline": item.get("airline"),
        "flight_number": item.get("flight_number"),
        "departure_at": None,
        "dep_time": None,
        "duration": item.get("duration"),
        "number_of_changes": int(item.get("number_of_changes") or 0),
        "origin_airport": item.get("origin_airport") or item.get("origin") or origin,
        "destination_airport": item.get("destination_airport") or item.get("destination") or destination,
        "origin": item.get("origin") or origin,
        "destination": item.get("destination") or destination,
        "gate": item.get("gate"),
        "link": None,
        "created_at": item.get("created_at"),
        "api_version": "v2",
        "itinerary_airports": [],
    }


class AviasalesDataClient:
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.token = token or settings.AVIASALES_TOKEN
        self.base_url = (base_url or settings.TRAVELPAYOUTS_API_BASE_URL).rstrip("/")

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.token.strip())

    async def check_health(self) -> Dict[str, Any]:
        """
        Check token configuration and test connectivity to Data API.
        NEVER leaks the token value.
        """
        if not self.is_configured:
            return {
                "configured": False,
                "api_reachable": False,
                "message": "Не задан AVIASALES_TOKEN — скопируйте .env.example в .env и впишите токен",
            }

        url = f"{self.base_url}/aviasales/v3/prices_for_dates"
        params = {
            "origin": "MOW",
            "destination": "IST",
            "departure_at": "2026-11",
            "currency": "rub",
            "limit": 1,
            "token": self.token,
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(url, params=params)
                if res.status_code == 200:
                    return {
                        "configured": True,
                        "api_reachable": True,
                        "message": "Travelpayouts Data API (v3) доступен и токен валиден",
                    }
                elif res.status_code in (401, 403):
                    return {
                        "configured": True,
                        "api_reachable": False,
                        "message": "Неверный или неактивный AVIASALES_TOKEN",
                    }
                else:
                    return {
                        "configured": True,
                        "api_reachable": False,
                        "message": f"Ошибка Data API (HTTP {res.status_code})",
                    }
        except Exception as e:
            return {
                "configured": True,
                "api_reachable": False,
                "message": f"Ошибка соединения с Data API: {str(e)}",
            }

    async def get_prices_for_dates(
        self,
        origin: str,
        destination: str,
        departure_at: str,
        currency: str = "rub",
        limit: int = 1000,
        page: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Calls /aviasales/v3/prices_for_dates.
        departure_at: YYYY-MM-DD or YYYY-MM (month calendar).
        """
        if not self.is_configured:
            raise AviasalesAPIError("Не задан AVIASALES_TOKEN — скопируйте .env.example в .env и впишите токен")

        url = f"{self.base_url}/aviasales/v3/prices_for_dates"
        params: Dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "departure_at": departure_at,
            "currency": currency,
            "limit": limit,
            "page": page,
            "token": self.token,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(url, params=params)
                if res.status_code != 200:
                    raise AviasalesAPIError(
                        f"Aviasales Data API v3 returned status {res.status_code}: {res.text[:200]}",
                        status_code=res.status_code,
                    )
                data = res.json()
                if data.get("success") is False:
                    err = data.get("error", "Unknown error")
                    raise AviasalesAPIError(f"Data API v3 error: {err}")
                return data.get("data") or []
        except httpx.RequestError as e:
            raise AviasalesAPIError(f"Network error connecting to Data API v3: {str(e)}")

    async def get_latest_prices(
        self,
        origin: str,
        destination: str,
        depart_date: Optional[str] = None,
        return_date: Optional[str] = None,
        currency: str = "rub",
        limit: int = 30,
        page: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Fallback: /v2/prices/latest.
        """
        if not self.is_configured:
            raise AviasalesAPIError("Не задан AVIASALES_TOKEN — скопируйте .env.example в .env и впишите токен")

        url = f"{self.base_url}/v2/prices/latest"
        params: Dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "currency": currency,
            "limit": limit,
            "page": page,
            "token": self.token,
        }
        if depart_date:
            params["depart_date"] = depart_date
        if return_date:
            params["return_date"] = return_date

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url, params=params)
                if res.status_code != 200:
                    raise AviasalesAPIError(
                        f"Aviasales Data API returned status {res.status_code}: {res.text[:200]}",
                        status_code=res.status_code,
                    )
                data = res.json()
                if not data.get("success", False):
                    err = data.get("error", "Unknown error")
                    raise AviasalesAPIError(f"Data API error: {err}")
                return data.get("data", [])
        except httpx.RequestError as e:
            raise AviasalesAPIError(f"Network error connecting to Data API: {str(e)}")

    async def fetch_route_prices(
        self,
        origin: str,
        destination: str,
        target_date: date,
        currency: str = "rub",
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Primary v3 (month bucket), fallback v2.
        Returns (normalized rows, source_label).
        """
        month_str = target_date.strftime("%Y-%m")
        origin_u = origin.upper()
        dest_u = destination.upper()
        try:
            raw_v3 = await self.get_prices_for_dates(
                origin=origin_u,
                destination=dest_u,
                departure_at=month_str,
                currency=currency,
                limit=1000,
            )
            if raw_v3:
                return (
                    [normalize_v3_item(r, origin_u, dest_u) for r in raw_v3],
                    "v3/prices_for_dates",
                )
        except AviasalesAPIError:
            pass

        raw_v2 = await self.get_latest_prices(
            origin=origin_u,
            destination=dest_u,
            depart_date=target_date.strftime("%Y-%m-%d"),
            currency=currency,
            limit=30,
        )
        return (
            [normalize_v2_item(r, origin_u, dest_u) for r in raw_v2],
            "v2/prices/latest",
        )


aviasales_client = AviasalesDataClient()
