from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class HubSpec:
    """A city the passenger can connect in. Search uses every airport code plus the city code."""

    code: str
    name_ru: str
    name_prep: str
    airports: Tuple[str, ...]
    # Only Istanbul has a checked airport-change floor. Other hubs stay None.
    airport_change_min_hours: Optional[float]
    aliases: Tuple[str, ...]

    @property
    def search_codes(self) -> Tuple[str, ...]:
        codes = list(self.airports)
        if self.code not in codes:
            codes.append(self.code)
        return tuple(codes)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "name_ru": self.name_ru,
            "airports": list(self.airports),
            "search_codes": list(self.search_codes),
            "airport_change_min_hours": self.airport_change_min_hours,
            "aliases": list(self.aliases),
        }


HUBS: Tuple[HubSpec, ...] = (
    HubSpec(
        code="IST",
        name_ru="Стамбул",
        name_prep="Стамбуле",
        airports=("IST", "SAW"),
        airport_change_min_hours=6.0,
        aliases=("IST", "SAW"),
    ),
    HubSpec(
        code="BJS",
        name_ru="Пекин",
        name_prep="Пекине",
        airports=("PEK", "PKX"),
        airport_change_min_hours=None,
        aliases=("BJS", "PEK", "PKX"),
    ),
    HubSpec(
        code="SHA",
        name_ru="Шанхай",
        name_prep="Шанхае",
        airports=("SHA", "PVG"),
        airport_change_min_hours=None,
        aliases=("SHA", "PVG"),
    ),
    HubSpec(
        code="HKG",
        name_ru="Гонконг",
        name_prep="Гонконге",
        airports=("HKG",),
        airport_change_min_hours=None,
        aliases=("HKG",),
    ),
)

_BY_ALIAS = {alias: hub for hub in HUBS for alias in hub.aliases}


def resolve_hub_code(code: str) -> Optional[HubSpec]:
    return _BY_ALIAS.get(code.strip().upper())


def all_hubs() -> Tuple[HubSpec, ...]:
    return HUBS
