from typing import List, Dict, Any, Optional
import httpx
from datetime import date

from app.core.config import settings


class AviasalesAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


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

        url = f"{self.base_url}/v2/prices/latest"
        params = {
            "origin": "MOW",
            "destination": "IST",
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
                        "message": "Travelpayouts Data API доступен и токен валиден",
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
        Calls /v2/prices/latest.
        Returns list of price objects from data field.
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
            # Format YYYY-MM or YYYY-MM-DD
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
                    # Data API sometimes returns {"success": false, "error": "..."}
                    err = data.get("error", "Unknown error")
                    raise AviasalesAPIError(f"Data API error: {err}")
                return data.get("data", [])
        except httpx.RequestError as e:
            raise AviasalesAPIError(f"Network error connecting to Data API: {str(e)}")


aviasales_client = AviasalesDataClient()
