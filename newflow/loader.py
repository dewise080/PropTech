import json
import math
import re
from pathlib import Path
from typing import Any


# Maps normalized field names to the possible source JSON keys.
# Add new entries here when introducing new fields (phone, rating, address, etc.)
FIELD_MAP: dict[str, list[str]] = {
    "name": ["title", "name"],
    "lat":  ["latitude", "lat"],
    "lng":  ["longtitude", "longitude", "lng", "lon"],  # source data has typo "longtitude"
}

# Keys already consumed as core fields — excluded from extra scalar pass
_CORE_KEYS = {"title", "name", "latitude", "longtitude", "longitude", "lat", "lng"}


def parse_filename_coords(stem: str) -> tuple[float, float, str] | tuple[None, None, str]:
    """
    Parse a filename stem in the format  {lon}.{lat}.{query}
    e.g. "28.6478896.41.0185658.pharmacies"
         → (28.6478896, 41.0185658, "pharmacies")

    Returns (lon, lat, query) on success, or (None, None, stem) if not recognised.
    """
    m = re.match(r'^(-?\d+\.\d+)\.(-?\d+\.\d+)\.(.+)$', stem)
    if m:
        try:
            return float(m.group(1)), float(m.group(2)), m.group(3)
        except ValueError:
            pass
    return None, None, stem


def _pick(record: dict, keys: list[str]) -> Any:
    """Return the first matching value from a record given a list of candidate keys."""
    for k in keys:
        if k in record:
            return record[k]
    return None


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Compute distance in metres between two lat/lng points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize_record(record: dict, idx: int, center_lat: float, center_lng: float) -> dict | None:
    """
    Extract and normalize a single record into the map item format.
    Returns None if required fields (name, lat, lng) are missing or invalid.
    Extend this function when new fields are added to FIELD_MAP.
    """
    name = _pick(record, FIELD_MAP["name"])
    lat  = _pick(record, FIELD_MAP["lat"])
    lng  = _pick(record, FIELD_MAP["lng"])

    if name is None or lat is None or lng is None:
        return None
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None

    entry: dict[str, Any] = {
        "id":         idx,
        "name":       str(name),
        "lat":        lat,
        "lng":        lng,
        "distance_m": round(_haversine_m(center_lat, center_lng, lat, lng)),
    }

    # Include all remaining scalar fields so the popup builder can use any of them
    for k, v in record.items():
        if k not in entry and k not in _CORE_KEYS and not isinstance(v, (dict, list)):
            entry[k] = v

    # Include first 3 user reviews (array — handled explicitly)
    reviews_raw = record.get("user_reviews") or record.get("reviews")
    if isinstance(reviews_raw, list):
        entry["user_reviews"] = reviews_raw[:3]

    return entry


def _parse_content(content: str) -> list[dict]:
    """Parse JSON content — supports standard JSON array/object and NDJSON."""
    content = content.strip()
    try:
        raw = json.loads(content)
        if isinstance(raw, dict):
            raw = list(raw.values()) if all(isinstance(v, dict) for v in raw.values()) else [raw]
        return raw
    except json.JSONDecodeError:
        # Fall back to NDJSON: one JSON object per line
        records = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip malformed lines silently
        return records


def _build_dataset(raw: list, label: str, limit: int | None = None) -> dict:
    """
    Normalize raw records into a map-ready dataset.

    Returns:
        {
            "label":  str,
            "items":  list[dict],
            "center": {"lat": float, "lng": float} | None,
        }
    """
    # First pass: collect all valid coordinates to compute the center
    coords: list[tuple[float, float]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        lat = _pick(r, FIELD_MAP["lat"])
        lng = _pick(r, FIELD_MAP["lng"])
        try:
            coords.append((float(lat), float(lng)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass

    if not coords:
        return {"label": label, "items": [], "center": None}

    center_lat = sum(c[0] for c in coords) / len(coords)
    center_lng = sum(c[1] for c in coords) / len(coords)

    # Second pass: normalize records, apply limit
    items: list[dict] = []
    for idx, r in enumerate(raw):
        if not isinstance(r, dict):
            continue
        if limit is not None and len(items) >= limit:
            break
        entry = normalize_record(r, idx, center_lat, center_lng)
        if entry is not None:
            items.append(entry)

    return {
        "label":  label,
        "items":  items,
        "center": {"lat": center_lat, "lng": center_lng},
    }


def load_json(path: Path, limit: int | None = None) -> dict:
    """Load and normalize a JSON file from disk."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    raw = _parse_content(content)
    return _build_dataset(raw, label=path.stem, limit=limit)


def load_from_upload(content: str, label: str, limit: int | None = None) -> dict:
    """Load and normalize JSON content from an in-memory upload."""
    raw = _parse_content(content)
    return _build_dataset(raw, label=label, limit=limit)
