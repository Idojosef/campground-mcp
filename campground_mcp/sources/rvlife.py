from __future__ import annotations

import httpx

from .models import Campground, CampgroundType

BASE_URL = "https://campgrounds.rvlife.com/api"

PARK_TYPE_MAP = {
    "commercial": CampgroundType.COMMERCIAL,
    "national": CampgroundType.NATIONAL,
    "state": CampgroundType.STATE,
    "dnr": CampgroundType.BLM_DNR,
    "usfs": CampgroundType.USFS,
    "coe": CampgroundType.COE,
    "county": CampgroundType.COUNTY,
    "city": CampgroundType.CITY,
    "military": CampgroundType.MILITARY,
}

# Attribute string positions map to filter categories in order:
# Park Features (14), Hookups (8), Amenities (22), Recreation (16), Connectivity (1) = 61
ATTRIBUTE_KEYS = [
    # Park Features (0-13)
    "Pull-thru Sites", "Pets Allowed", "Big Rig Access", "Tent Camping",
    "55+ Only", "Kid Friendly", "Boondocking", "Cabins & Rentals",
    "Dump Station", "Group Camping", "Dispersed Camping", "Workamping",
    "Class A Only", "No Minors",
    # Hookups (14-21)
    "Full Hookup", "50 AMP", "Electric 30/20/15", "Sewer",
    "Cable TV", "Central Water Spigot", "Public Phone", "Water",
    # Amenities (22-43)
    "Restrooms", "Showers", "Laundry", "Camp Store", "Pet Area",
    "Propane", "Cafe/Snack Bar", "Clubhouse", "Firewood", "Group Kitchen",
    "Horse Camp", "Landing Strip", "Picnic Shelter", "Vault Toilets",
    "Church Affiliated", "Clothing Optional", "Winery", "RV Dealership",
    "Fairground", "Specialty Park", "Permanent Only", "Members Only",
    # Recreation (44-59)
    "Pool", "Playground", "Recreation Trails", "Rec Room", "Casino",
    "Fishing", "Beach", "Biking", "Boating", "Golf",
    "Gym", "Horseshoes", "Mini-Golf", "Outdoor Courts", "Pickleball",
    "Water Access",
    # Connectivity (60)
    "WiFi",
]

HOOKUP_INDICES = set(range(14, 22))


def _decode_attributes(attr_str: str) -> tuple[list[str], list[str]]:
    """Decode the attribute bit-string. 0=no, 1=yes, 2=not reported."""
    amenities = []
    hookups = []
    for i, ch in enumerate(attr_str):
        if i >= len(ATTRIBUTE_KEYS):
            break
        if ch == "1":
            name = ATTRIBUTE_KEYS[i]
            if i in HOOKUP_INDICES:
                hookups.append(name)
            else:
                amenities.append(name)
    return amenities, hookups


def _parse_park(park: dict) -> Campground:
    park_type = park.get("type") or park.get("park_type", "unknown")
    # Attribute bit-string decoding is approximate — positions not fully documented
    amenities: list[str] = []
    hookups: list[str] = []

    badges = []
    raw_badges = park.get("badges", {})
    if isinstance(raw_badges, dict):
        badges = list(raw_badges.values())

    return Campground(
        id=f"rvlife:{park.get('id') or park.get('cg_id')}",
        source="rvlife",
        name=park.get("name") or park.get("cg_name", "Unknown"),
        city=park.get("city", {}).get("name", "") if isinstance(park.get("city"), dict) else park.get("city_name", ""),
        state=park.get("region", {}).get("abbr", "") if isinstance(park.get("region"), dict) else park.get("region_abbvr", ""),
        lat=park.get("lat", 0),
        lon=park.get("lon") or park.get("lng", 0),
        campground_type=PARK_TYPE_MAP.get(park_type, CampgroundType.UNKNOWN),
        price_per_night=park.get("price") or park.get("avg_rate"),
        rating=park.get("rating_avg") or park.get("cg_avg_rating"),
        rating_count=park.get("rating_count") or park.get("cg_rating_count", 0),
        num_sites=park.get("sites") or park.get("cg_site_count"),
        elevation=park.get("elevation"),
        url=(
            park["url"] if park["url"].startswith("http")
            else f"https://campgrounds.rvlife.com{park['url']}"
        ) if park.get("url") else None,
        amenities=amenities,
        hookups=hookups,
        badges=badges,
    )


async def search_nearby(
    lat: float,
    lon: float,
    limit: int = 20,
    page: int = 1,
    park_types: list[str] | None = None,
    country: str = "us",
) -> list[Campground]:
    """Search RV Life for campgrounds near coordinates."""
    params: dict = {
        "page": page,
        "country": country,
        "sort": "true",
        "limit": limit,
    }
    if park_types:
        for pt in park_types:
            params[f"park_types[{pt}]"] = "true"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{BASE_URL}/parks/parks-nearby/{lat}/{lon}",
            params=params,
        )
        resp.raise_for_status()

    data = resp.json()
    parks = data if isinstance(data, list) else data.get("data", [])
    return [_parse_park(p) for p in parks]


async def get_filters() -> dict:
    """Fetch the available filter options from RV Life."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BASE_URL}/filters", params={"v": "2"})
        resp.raise_for_status()
    return resp.json()
