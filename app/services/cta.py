from typing import Optional
from app.core.config import settings
from app.models.flight import DualCTA, CTAInfo, PackageClass


def build_aviasales_deep_link(
    origin: str,
    destination: str,
    depart_date_str: str,  # YYYY-MM-DD
    return_date_str: Optional[str] = None,
    hub: Optional[str] = None,
    second_depart_date_str: Optional[str] = None,
    second_origin: Optional[str] = None,
    passengers: int = 1,
) -> str:
    """
    Builds Aviasales deep link with marker.
    Format: DDMM (e.g. 0611 for Nov 6).
    For multi-city: MOW0611IST-IST0711HKT2?marker=765617
    For direct: MOW0611HKT1?marker=765617
    """
    marker = settings.AVIASALES_MARKER or "765617"
    
    # parse YYYY-MM-DD to DDMM
    def to_ddmm(d_str: str) -> str:
        parts = d_str.split("-")
        if len(parts) == 3:
            return f"{parts[2]}{parts[1]}"
        return "0101"

    d1 = to_ddmm(depart_date_str)
    
    if hub and second_depart_date_str:
        d2 = to_ddmm(second_depart_date_str)
        mid = second_origin or hub
        route_part = f"{origin}{d1}{hub}-{mid}{d2}{destination}{passengers}"
    else:
        route_part = f"{origin}{d1}{destination}{passengers}"

    return f"https://www.aviasales.ru/search/{route_part}?marker={marker}"


def get_airline_official_url(carrier_code: Optional[str]) -> Optional[str]:
    if not carrier_code:
        return None
    code = carrier_code.upper()
    urls = {
        "TK": "https://www.turkishairlines.com",
        "SU": "https://www.aeroflot.ru",
        "PC": "https://www.flypgs.com",
        "WZ": "https://flyredwings.com",
        "DP": "https://pobeda.aero",
        "S7": "https://www.s7.ru",
        "QR": "https://www.qatarairways.com",
        "EK": "https://www.emirates.com",
        "FZ": "https://www.flydubai.com",
    }
    return urls.get(code)


def build_dual_cta(
    package_class: PackageClass,
    origin: str,
    destination: str,
    hub: str,
    leg1_date: str,
    leg2_date: str,
    carrier: Optional[str],
    passengers: int,
    full_basket_rub: float,
    airline_price_rub: Optional[float] = None,
    leg2_origin: Optional[str] = None,
) -> DualCTA:
    """
    Dual CTA rule:
    - Assembly: Aviasales primary by default.
    - Unified: Aviasales primary unless airline is cheaper by >=5% OR >=1500 RUB on full basket.
      Then airline primary, Aviasales secondary.
    - If airline price unknown, show airline as secondary without price claims.
    """
    aviasales_url = build_aviasales_deep_link(
        origin=origin,
        destination=destination,
        depart_date_str=leg1_date,
        hub=hub if package_class == PackageClass.ASSEMBLY else None,
        second_depart_date_str=leg2_date if package_class == PackageClass.ASSEMBLY else None,
        second_origin=leg2_origin if package_class == PackageClass.ASSEMBLY else None,
        passengers=passengers,
    )

    airline_url = get_airline_official_url(carrier)
    carrier_name = carrier or "Авиакомпания"

    if package_class == PackageClass.ASSEMBLY:
        # Assembly: Aviasales primary
        primary = CTAInfo(
            title="Купить оба билета на Авиасейлс",
            url=aviasales_url,
            provider_name="Авиасейлс",
            price_rub=full_basket_rub,
        )
        secondary = None
        if airline_url:
            secondary = CTAInfo(
                title=f"Сайт {carrier_name} (отдельный билет)",
                url=airline_url,
                provider_name=carrier_name,
                price_rub=None,
            )
        return DualCTA(primary=primary, secondary=secondary)

    elif package_class in (PackageClass.UNIFIED, PackageClass.THROUGH_UNVERIFIED):
        # Single-ticket (verified or agency through)
        airline_is_cheaper = False
        if airline_price_rub is not None:
            price_diff = full_basket_rub - airline_price_rub
            percent_diff = price_diff / full_basket_rub if full_basket_rub > 0 else 0
            if percent_diff >= 0.05 or price_diff >= 1500:
                airline_is_cheaper = True

        if airline_is_cheaper and airline_url:
            primary = CTAInfo(
                title=f"Купить на сайте {carrier_name}",
                url=airline_url,
                provider_name=carrier_name,
                price_rub=airline_price_rub,
            )
            secondary = CTAInfo(
                title="Сравнить на Авиасейлс",
                url=aviasales_url,
                provider_name="Авиасейлс",
                price_rub=full_basket_rub,
            )
        else:
            primary = CTAInfo(
                title="Купить на Авиасейлс",
                url=aviasales_url,
                provider_name="Авиасейлс",
                price_rub=full_basket_rub,
            )
            secondary = None
            if airline_url:
                secondary = CTAInfo(
                    title=f"Проверить на сайте {carrier_name}",
                    url=airline_url,
                    provider_name=carrier_name,
                    price_rub=airline_price_rub,
                )
        return DualCTA(primary=primary, secondary=secondary)

    else:
        primary = CTAInfo(
            title="Купить на Авиасейлс",
            url=aviasales_url,
            provider_name="Авиасейлс",
            price_rub=full_basket_rub,
        )
        return DualCTA(primary=primary, secondary=None)
