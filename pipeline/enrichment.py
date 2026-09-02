#!/usr/bin/env python3
"""Merge optional direct-OSM metadata into the compact national camera index.

The compact DeFlock position index is intentionally small and gives FlockAware
broad national coverage. Direct OpenStreetMap/Overpass records can add richer
metadata such as OSM IDs, source links, bearing, operator, mount type, and zone.

This module enriches only cameras that can be matched conservatively by nearby
coordinates and compatible vendor data. It keeps the compact camera ID and
coordinates so dataset versions and new-camera alerts remain stable. Unmatched
enrichment records are not appended; that avoids treating a source expansion as
a newly installed physical camera.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

EARTH_RADIUS_METERS = 6_371_000.0
DEFAULT_MATCH_DISTANCE_METERS = 40.0
GRID_CELL_DEGREES = 0.0005

RICH_FIELDS = (
    "name",
    "operatorName",
    "directionDegrees",
    "directionText",
    "municipality",
    "county",
    "surveillanceZone",
    "mountType",
    "startDate",
    "reference",
    "osmType",
    "osmId",
    "osmVersion",
    "osmTimestamp",
    "sourceName",
    "sourceURL",
    "dataStatus",
    "model",
    "cameraType",
    "powerSource",
    "imageURL",
    "mapillaryKey",
    "website",
    "description",
    "note",
    "street",
)


@dataclass(frozen=True)
class EnrichmentStats:
    base_count: int
    overlay_count: int
    matched_count: int
    enriched_count: int
    basic_count: int
    unmatched_overlay_count: int

    def as_dict(self) -> dict[str, int]:
        return {
            "baseCount": self.base_count,
            "overlayCount": self.overlay_count,
            "matchedCount": self.matched_count,
            "enrichedCount": self.enriched_count,
            "basicCount": self.basic_count,
            "unmatchedOverlayCount": self.unmatched_overlay_count,
        }


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def normalize_vendor(value: Any) -> str | None:
    raw = _text(value)
    if raw is None:
        return None
    lowered = raw.casefold()
    if lowered in {"unknown", "unspecified", "other", "n/a", "na"}:
        return None
    if "flock" in lowered:
        return "flock"
    if "motorola" in lowered or "vigilant" in lowered:
        return "motorola-vigilant"
    if "rekor" in lowered:
        return "rekor"
    if "genetec" in lowered:
        return "genetec"
    if "axis" in lowered:
        return "axis"
    return " ".join(lowered.split())


def vendor_compatible(base_vendor: Any, overlay_vendor: Any) -> bool:
    base = normalize_vendor(base_vendor)
    overlay = normalize_vendor(overlay_vendor)
    return base is None or overlay is None or base == overlay


def haversine_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return EARTH_RADIUS_METERS * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))


def _cell_key(latitude: float, longitude: float) -> tuple[int, int]:
    return (
        math.floor(latitude / GRID_CELL_DEGREES),
        math.floor(longitude / GRID_CELL_DEGREES),
    )


def _neighbor_keys(key: tuple[int, int]) -> Iterable[tuple[int, int]]:
    for latitude_offset in (-1, 0, 1):
        for longitude_offset in (-1, 0, 1):
            yield key[0] + latitude_offset, key[1] + longitude_offset


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def has_rich_metadata(camera: dict[str, Any]) -> bool:
    return any(
        _present(camera.get(field))
        for field in (
            "osmId",
            "sourceURL",
            "directionDegrees",
            "operatorName",
            "municipality",
            "county",
            "surveillanceZone",
            "mountType",
            "osmTimestamp",
        )
    )


def _merge_camera(
    base_camera: dict[str, Any],
    overlay_camera: dict[str, Any],
    distance_meters: float,
    overlay_source: str | None,
) -> dict[str, Any]:
    merged = dict(base_camera)

    base_vendor = normalize_vendor(base_camera.get("vendor"))
    overlay_vendor = overlay_camera.get("vendor")
    if base_vendor is None and _present(overlay_vendor):
        merged["vendor"] = overlay_vendor

    for field in RICH_FIELDS:
        value = overlay_camera.get(field)
        if _present(value):
            merged[field] = value

    # Preserve the compact source identity and coordinates. These are the stable
    # keys used by version diffs and new-camera notifications.
    merged["id"] = base_camera["id"]
    merged["latitude"] = base_camera["latitude"]
    merged["longitude"] = base_camera["longitude"]
    merged["stateCode"] = base_camera.get("stateCode") or overlay_camera.get("stateCode")
    merged["detailLevel"] = "enriched"
    merged["enrichmentSource"] = overlay_source or "OpenStreetMap Overpass"
    merged["enrichmentMatchMeters"] = round(distance_meters, 1)
    return merged


def enrich_cameras(
    base_cameras: list[dict[str, Any]],
    overlay_cameras: list[dict[str, Any]],
    *,
    overlay_source: str | None = None,
    max_distance_meters: float = DEFAULT_MATCH_DISTANCE_METERS,
) -> tuple[list[dict[str, Any]], EnrichmentStats]:
    """Return stable-ID base records enriched by conservative OSM matches."""

    overlay_buckets: dict[tuple[int, int], list[int]] = {}
    for index, camera in enumerate(overlay_cameras):
        latitude = camera.get("latitude")
        longitude = camera.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            continue
        overlay_buckets.setdefault(_cell_key(float(latitude), float(longitude)), []).append(index)

    used_overlay_indices: set[int] = set()
    merged_cameras: list[dict[str, Any]] = []
    matched_count = 0

    for base_camera in base_cameras:
        latitude = float(base_camera["latitude"])
        longitude = float(base_camera["longitude"])
        best_index: int | None = None
        best_distance = math.inf

        for key in _neighbor_keys(_cell_key(latitude, longitude)):
            for overlay_index in overlay_buckets.get(key, []):
                if overlay_index in used_overlay_indices:
                    continue
                overlay_camera = overlay_cameras[overlay_index]
                if not vendor_compatible(base_camera.get("vendor"), overlay_camera.get("vendor")):
                    continue
                distance = haversine_meters(
                    latitude,
                    longitude,
                    float(overlay_camera["latitude"]),
                    float(overlay_camera["longitude"]),
                )
                if distance <= max_distance_meters and distance < best_distance:
                    best_index = overlay_index
                    best_distance = distance

        if best_index is None:
            basic = dict(base_camera)
            basic["detailLevel"] = "enriched" if has_rich_metadata(basic) else "basic"
            merged_cameras.append(basic)
            continue

        used_overlay_indices.add(best_index)
        matched_count += 1
        merged_cameras.append(
            _merge_camera(
                base_camera,
                overlay_cameras[best_index],
                best_distance,
                overlay_source,
            )
        )

    enriched_count = sum(1 for camera in merged_cameras if camera.get("detailLevel") == "enriched")
    stats = EnrichmentStats(
        base_count=len(base_cameras),
        overlay_count=len(overlay_cameras),
        matched_count=matched_count,
        enriched_count=enriched_count,
        basic_count=len(merged_cameras) - enriched_count,
        unmatched_overlay_count=max(0, len(overlay_cameras) - len(used_overlay_indices)),
    )
    return merged_cameras, stats
