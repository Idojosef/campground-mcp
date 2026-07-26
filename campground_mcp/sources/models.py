from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CampgroundType(str, Enum):
    COMMERCIAL = "commercial"
    NATIONAL = "national"
    STATE = "state"
    BLM_DNR = "dnr"
    USFS = "usfs"
    COE = "coe"
    COUNTY = "county"
    CITY = "city"
    MILITARY = "military"
    UNKNOWN = "unknown"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "Available"
    RESERVED = "Reserved"
    NOT_AVAILABLE = "Not Available"
    UNKNOWN = "Unknown"


@dataclass
class Campground:
    id: str
    source: str  # "rvlife" or "recreation_gov"
    name: str
    city: str
    state: str
    lat: float
    lon: float
    campground_type: CampgroundType
    price_per_night: float | None = None
    rating: float | None = None
    rating_count: int = 0
    num_sites: int | None = None
    elevation: int | None = None
    url: str | None = None
    amenities: list[str] = field(default_factory=list)
    hookups: list[str] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"**{self.name}** ({self.city}, {self.state})"]
        parts.append(f"  Type: {self.campground_type.value}")
        if self.price_per_night:
            parts.append(f"  Price: ${self.price_per_night}/night")
        if self.rating:
            parts.append(f"  Rating: {self.rating}/10 ({self.rating_count} reviews)")
        if self.num_sites:
            parts.append(f"  Sites: {self.num_sites}")
        if self.elevation:
            parts.append(f"  Elevation: {self.elevation} ft")
        if self.amenities:
            parts.append(f"  Amenities: {', '.join(self.amenities[:8])}")
        if self.hookups:
            parts.append(f"  Hookups: {', '.join(self.hookups)}")
        if self.url:
            parts.append(f"  URL: {self.url}")
        return "\n".join(parts)


@dataclass
class SiteAvailability:
    campsite_id: str
    campsite_name: str
    campsite_type: str
    dates: dict[str, AvailabilityStatus] = field(default_factory=dict)

    @property
    def available_dates(self) -> list[str]:
        return [d for d, s in self.dates.items() if s == AvailabilityStatus.AVAILABLE]


@dataclass
class CampgroundAvailability:
    campground_id: str
    campground_name: str
    sites: list[SiteAvailability] = field(default_factory=list)

    @property
    def total_available_sites(self) -> dict[str, int]:
        """Count of available sites per date."""
        counts: dict[str, int] = {}
        for site in self.sites:
            for date, status in site.dates.items():
                if status == AvailabilityStatus.AVAILABLE:
                    counts[date] = counts.get(date, 0) + 1
        return counts
