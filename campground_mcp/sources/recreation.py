from __future__ import annotations

from datetime import date, timedelta

import httpx

from .models import (
    AvailabilityStatus,
    Campground,
    CampgroundAvailability,
    CampgroundType,
    SiteAvailability,
)

RECGOV_BASE = "https://www.recreation.gov"
RIDB_BASE = "https://ridb.recreation.gov/api/v1"

RECGOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CampgroundMCP/1.0",
}


async def search_campgrounds(
    lat: float,
    lon: float,
    radius: int = 25,
    limit: int = 20,
    api_key: str | None = None,
) -> list[Campground]:
    """Search RIDB for federal campgrounds near coordinates."""
    if not api_key:
        return []

    headers = {"accept": "application/json", "apikey": api_key}
    params = {
        "latitude": lat,
        "longitude": lon,
        "radius": radius,
        "limit": limit,
        "activity": "CAMPING",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{RIDB_BASE}/facilities",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()

    data = resp.json()
    facilities = data.get("RECDATA", [])

    results = []
    for fac in facilities:
        addresses = fac.get("FACILITYADDRESS", [])
        addr = addresses[0] if addresses else {}
        results.append(Campground(
            id=f"recgov:{fac['FacilityID']}",
            source="recreation_gov",
            name=fac.get("FacilityName", "Unknown"),
            city=addr.get("City", ""),
            state=addr.get("AddressStateCode", ""),
            lat=fac.get("FacilityLatitude", 0),
            lon=fac.get("FacilityLongitude", 0),
            campground_type=CampgroundType.NATIONAL,
            url=f"https://www.recreation.gov/camping/campgrounds/{fac['FacilityID']}",
        ))
    return results


async def check_availability(
    campground_id: str,
    start_date: date,
    end_date: date,
) -> CampgroundAvailability:
    """Check site-level availability for a Recreation.gov campground."""
    facility_id = campground_id.replace("recgov:", "")

    months_to_check: set[str] = set()
    current = start_date.replace(day=1)
    while current <= end_date:
        months_to_check.add(current.strftime("%Y-%m-01T00:00:00.000Z"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    all_sites: dict[str, SiteAvailability] = {}

    async with httpx.AsyncClient(timeout=15, headers=RECGOV_HEADERS) as client:
        for month_str in sorted(months_to_check):
            resp = await client.get(
                f"{RECGOV_BASE}/api/camps/availability/campground/{facility_id}/month",
                params={"start_date": month_str},
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            for site_id, site_data in data.get("campsites", {}).items():
                if site_id not in all_sites:
                    all_sites[site_id] = SiteAvailability(
                        campsite_id=site_id,
                        campsite_name=site_data.get("campsite_reserve_type", site_id),
                        campsite_type=site_data.get("campsite_type", "STANDARD"),
                    )

                for date_str, status in site_data.get("availabilities", {}).items():
                    d = date_str[:10]
                    check_date = date.fromisoformat(d)
                    if start_date <= check_date <= end_date:
                        all_sites[site_id].dates[d] = AvailabilityStatus(status) if status in AvailabilityStatus.__members__.values() else AvailabilityStatus.UNKNOWN

    return CampgroundAvailability(
        campground_id=campground_id,
        campground_name=f"Facility {facility_id}",
        sites=list(all_sites.values()),
    )
