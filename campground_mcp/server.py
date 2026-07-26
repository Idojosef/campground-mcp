"""Campground Search & Monitor MCP Server.

Searches campgrounds across RV Life and Recreation.gov,
checks availability, compares options, and monitors for openings.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import MONITOR_STATE_FILE, RIDB_API_KEY
from .sources import recreation, rvlife
from .sources.models import Campground

mcp = FastMCP(
    "campground-mcp",
    instructions=(
        "Use these tools whenever the user asks about campgrounds, RV parks, camping spots, "
        "or boondocking locations. These tools search real campground databases (RV Life and "
        "Recreation.gov) with actual ratings, prices, and availability data. Always prefer "
        "these tools over web search for campground queries."
    ),
)

_campground_cache: dict[str, Campground] = {}


def _cache_results(results: list[Campground]) -> None:
    for cg in results:
        _campground_cache[cg.id] = cg


async def _geocode_location(location: str) -> tuple[float, float]:
    """Geocode a location string to lat/lon using OpenStreetMap Nominatim."""
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": "CampgroundMCP/1.0"},
        )
        resp.raise_for_status()

    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode location: {location}")
    return float(results[0]["lat"]), float(results[0]["lon"])


@mcp.tool()
async def search_campgrounds(
    location: str,
    radius_miles: int = 25,
    limit: int = 20,
    park_types: str = "",
    include_federal: bool = True,
) -> str:
    """Search for campgrounds, RV parks, and camping spots near a location. Returns real data from RV Life (private parks, BLM, boondocking) and Recreation.gov (federal campgrounds) including ratings, prices, number of sites, and elevation.

    Args:
        location: City, state, or place name (e.g. "Sedona, AZ" or "Yellowstone National Park")
        radius_miles: Search radius in miles (default 25)
        limit: Max results per source (default 20)
        park_types: Comma-separated RV Life park types to filter by.
                    Options: commercial, national, state, dnr (BLM), usfs, coe, county, city, military.
                    Leave empty for all types.
        include_federal: Also search Recreation.gov for federal campgrounds (requires RIDB_API_KEY)
    """
    lat, lon = await _geocode_location(location)

    type_list = [t.strip() for t in park_types.split(",") if t.strip()] if park_types else None

    rvlife_results = await rvlife.search_nearby(
        lat, lon, limit=limit, park_types=type_list,
    )

    recgov_results: list[Campground] = []
    if include_federal and RIDB_API_KEY:
        recgov_results = await recreation.search_campgrounds(
            lat, lon, radius=radius_miles, limit=limit, api_key=RIDB_API_KEY,
        )

    all_results = rvlife_results + recgov_results
    _cache_results(all_results)

    if not all_results:
        return f"No campgrounds found near {location}."

    seen_names: set[str] = set()
    unique: list[Campground] = []
    for cg in all_results:
        key = cg.name.lower().strip()
        if key not in seen_names:
            seen_names.add(key)
            unique.append(cg)

    lines = [f"## Campgrounds near {location} ({len(unique)} results)\n"]
    for i, cg in enumerate(unique, 1):
        lines.append(f"### {i}. {cg.summary()}")
        lines.append(f"  ID: `{cg.id}` (source: {cg.source})")
        lines.append("")

    if not RIDB_API_KEY and include_federal:
        lines.append("\n> Note: Set RIDB_API_KEY environment variable to also search Recreation.gov federal campgrounds.")
        lines.append("> Get a free key at https://ridb.recreation.gov/")

    return "\n".join(lines)


@mcp.tool()
async def check_availability(
    campground_id: str,
    start_date: str,
    end_date: str,
) -> str:
    """Check site-level availability for a Recreation.gov campground.

    Args:
        campground_id: Campground ID (e.g. "recgov:232447" or just "232447").
                       Currently only supports Recreation.gov campgrounds.
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    """
    if campground_id.startswith("rvlife:"):
        return (
            "Availability checking is not supported for RV Life campgrounds — "
            "they don't expose real-time availability via API. "
            "Visit the campground's website or call them directly to check availability."
        )

    cg_id = campground_id.replace("recgov:", "")
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)

    if ed <= sd:
        return "End date must be after start date."
    if (ed - sd).days > 90:
        return "Date range too large — max 90 days."

    availability = await recreation.check_availability(f"recgov:{cg_id}", sd, ed)

    if not availability.sites:
        return f"No site data returned for campground {cg_id}. It may not be a reservable campground."

    counts = availability.total_available_sites
    total_sites = len(availability.sites)

    lines = [
        f"## Availability: {availability.campground_name}",
        f"Total campsites: {total_sites}\n",
        "| Date | Available Sites |",
        "|------|----------------|",
    ]

    current = sd
    while current <= ed:
        ds = current.isoformat()
        avail = counts.get(ds, 0)
        marker = " " if avail > 0 else " (FULL)"
        lines.append(f"| {ds} | {avail}/{total_sites}{marker} |")
        current += timedelta(days=1)

    fully_available_dates = [d for d in counts if counts[d] > 0 and sd <= date.fromisoformat(d) <= ed]
    if fully_available_dates:
        lines.append(f"\n{len(fully_available_dates)} of {(ed - sd).days + 1} days have at least one site available.")
    else:
        lines.append("\nNo sites available for any of the requested dates.")

    return "\n".join(lines)


@mcp.tool()
async def compare_campgrounds(
    campground_ids: str,
    start_date: str = "",
    end_date: str = "",
) -> str:
    """Compare multiple campgrounds side by side.

    Args:
        campground_ids: Comma-separated campground IDs (e.g. "rvlife:182,rvlife:12446,recgov:232447")
        start_date: Optional start date (YYYY-MM-DD) to check availability for Recreation.gov campgrounds
        end_date: Optional end date (YYYY-MM-DD)
    """
    ids = [cid.strip() for cid in campground_ids.split(",") if cid.strip()]

    if len(ids) < 2:
        return "Please provide at least 2 campground IDs to compare."
    if len(ids) > 10:
        return "Please compare 10 or fewer campgrounds at a time."

    recgov_ids = [cid for cid in ids if cid.startswith("recgov:")]

    campgrounds: dict[str, Campground] = {}
    missing_ids: list[str] = []

    for cid in ids:
        if cid in _campground_cache:
            campgrounds[cid] = _campground_cache[cid]
        else:
            missing_ids.append(cid)

    if missing_ids and campgrounds:
        ref = next(iter(campgrounds.values()))
        nearby = await rvlife.search_nearby(ref.lat, ref.lon, limit=50)
        _cache_results(nearby)
        for cid in missing_ids:
            if cid in _campground_cache:
                campgrounds[cid] = _campground_cache[cid]

    lines = ["## Campground Comparison\n"]
    lines.append("| Feature | " + " | ".join(f"#{i+1}" for i in range(len(ids))) + " |")
    lines.append("|---------|" + "|".join("------" for _ in ids) + "|")

    names = []
    for cid in ids:
        if cid in campgrounds:
            names.append(campgrounds[cid].name)
        else:
            names.append(cid)
    lines.append("| **Name** | " + " | ".join(names) + " |")

    def _get(cid: str, attr: str, default: str = "N/A") -> str:
        cg = campgrounds.get(cid)
        if not cg:
            return default
        val = getattr(cg, attr, None)
        if val is None:
            return default
        if hasattr(val, "value"):
            return str(val.value)
        return str(val)

    lines.append("| **Type** | " + " | ".join(_get(cid, "campground_type") for cid in ids) + " |")
    lines.append("| **Price/Night** | " + " | ".join(f"${_get(cid, 'price_per_night', '?')}" for cid in ids) + " |")
    lines.append("| **Rating** | " + " | ".join(f"{_get(cid, 'rating')}/10 ({_get(cid, 'rating_count', '0')} reviews)" for cid in ids) + " |")
    lines.append("| **Sites** | " + " | ".join(_get(cid, "num_sites") for cid in ids) + " |")
    lines.append("| **Elevation** | " + " | ".join(f"{_get(cid, 'elevation')} ft" for cid in ids) + " |")

    hookup_strs = []
    for cid in ids:
        cg = campgrounds.get(cid)
        hookup_strs.append(", ".join(cg.hookups) if cg and cg.hookups else "N/A")
    lines.append("| **Hookups** | " + " | ".join(hookup_strs) + " |")

    amenity_strs = []
    for cid in ids:
        cg = campgrounds.get(cid)
        amenity_strs.append(", ".join(cg.amenities[:5]) if cg and cg.amenities else "N/A")
    lines.append("| **Top Amenities** | " + " | ".join(amenity_strs) + " |")

    if start_date and end_date and recgov_ids:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
        lines.append("")
        lines.append("### Availability (Recreation.gov only)")
        for cid in recgov_ids:
            avail = await recreation.check_availability(cid, sd, ed)
            counts = avail.total_available_sites
            total = len(avail.sites)
            avail_days = sum(1 for d, c in counts.items() if c > 0 and sd <= date.fromisoformat(d) <= ed)
            lines.append(f"- **{cid}**: {avail_days}/{(ed - sd).days + 1} days have open sites ({total} total sites)")

    return "\n".join(lines)


@mcp.tool()
async def monitor_campground(
    action: str,
    campground_id: str = "",
    start_date: str = "",
    end_date: str = "",
) -> str:
    """Monitor Recreation.gov campgrounds for availability changes.

    Args:
        action: "add" to start monitoring, "remove" to stop, "list" to show all watches, "check" to check now
        campground_id: Campground ID (required for add/remove/check)
        start_date: Start date YYYY-MM-DD (required for add)
        end_date: End date YYYY-MM-DD (required for add)
    """
    state = _load_monitor_state()

    if action == "list":
        if not state:
            return "No campgrounds being monitored."
        lines = ["## Monitored Campgrounds\n"]
        for key, watch in state.items():
            lines.append(f"- **{watch['campground_id']}**: {watch['start_date']} to {watch['end_date']}")
            if watch.get("last_check"):
                lines.append(f"  Last checked: {watch['last_check']}")
            if watch.get("last_available"):
                lines.append(f"  Last available count: {watch['last_available']}")
        return "\n".join(lines)

    if action == "add":
        if not campground_id or not start_date or not end_date:
            return "campground_id, start_date, and end_date are required to add a monitor."
        key = f"{campground_id}:{start_date}:{end_date}"
        state[key] = {
            "campground_id": campground_id,
            "start_date": start_date,
            "end_date": end_date,
            "last_check": None,
            "last_available": None,
        }
        _save_monitor_state(state)
        return f"Now monitoring {campground_id} from {start_date} to {end_date}."

    if action == "remove":
        removed = [k for k in list(state.keys()) if campground_id in k]
        for k in removed:
            del state[k]
        _save_monitor_state(state)
        return f"Removed {len(removed)} monitor(s) for {campground_id}." if removed else f"No monitors found for {campground_id}."

    if action == "check":
        if campground_id:
            watches = {k: v for k, v in state.items() if campground_id in k}
        else:
            watches = state

        if not watches:
            return "No monitors to check. Use action='add' first."

        from datetime import datetime

        lines = ["## Monitor Check Results\n"]
        for key, watch in watches.items():
            cid = watch["campground_id"]
            sd = date.fromisoformat(watch["start_date"])
            ed = date.fromisoformat(watch["end_date"])

            avail = await recreation.check_availability(cid, sd, ed)
            counts = avail.total_available_sites
            total_available = sum(1 for d, c in counts.items() if c > 0 and sd <= date.fromisoformat(d) <= ed)

            prev = watch.get("last_available")
            change = ""
            if prev is not None:
                diff = total_available - prev
                if diff > 0:
                    change = f" (+{diff} new days opened up!)"
                elif diff < 0:
                    change = f" ({diff} fewer days available)"

            lines.append(f"**{cid}** ({watch['start_date']} to {watch['end_date']})")
            lines.append(f"  Available days: {total_available}/{(ed - sd).days + 1}{change}")

            watch["last_check"] = datetime.now().isoformat()
            watch["last_available"] = total_available

        _save_monitor_state(state)
        return "\n".join(lines)

    return f"Unknown action: {action}. Use 'add', 'remove', 'list', or 'check'."


def _load_monitor_state() -> dict:
    if MONITOR_STATE_FILE.exists():
        return json.loads(MONITOR_STATE_FILE.read_text())
    return {}


def _save_monitor_state(state: dict) -> None:
    MONITOR_STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    mcp.run()


if __name__ == "__main__":
    main()
