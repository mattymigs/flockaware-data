#!/usr/bin/env python3

from pathlib import Path

path = Path("pipeline/build_us_states.py")
text = path.read_text()

old = '''def stats_for_existing_dataset(dataset: dict[str, Any]) -> EnrichmentStats:
    cameras = dataset.get("cameras", [])
    enriched_count = sum(1 for camera in cameras if has_rich_metadata(camera))
    return EnrichmentStats(
        base_count=len(cameras),
        overlay_count=enriched_count,
        matched_count=enriched_count,
        enriched_count=enriched_count,
        basic_count=len(cameras) - enriched_count,
        unmatched_overlay_count=0,
    )


def alert_event(
'''
new = '''def tag_existing_dataset_detail_levels(dataset: dict[str, Any]) -> dict[str, Any]:
    tagged_dataset = dict(dataset)
    tagged_cameras: list[dict[str, Any]] = []
    for camera in dataset.get("cameras", []):
        tagged_camera = dict(camera)
        tagged_camera["detailLevel"] = (
            "enriched" if has_rich_metadata(tagged_camera) else "basic"
        )
        tagged_cameras.append(tagged_camera)
    tagged_dataset["cameras"] = tagged_cameras
    return tagged_dataset


def stats_for_existing_dataset(dataset: dict[str, Any]) -> EnrichmentStats:
    cameras = dataset.get("cameras", [])
    enriched_count = sum(
        1
        for camera in cameras
        if camera.get("detailLevel") == "enriched" or has_rich_metadata(camera)
    )
    return EnrichmentStats(
        base_count=len(cameras),
        overlay_count=enriched_count,
        matched_count=enriched_count,
        enriched_count=enriched_count,
        basic_count=len(cameras) - enriched_count,
        unmatched_overlay_count=0,
    )


def alert_event(
'''
if old not in text:
    raise SystemExit("Existing-dataset statistics block not found")
text = text.replace(old, new, 1)

old = '''            if state_code == "NJ" and old_dataset and old_dataset.get("cameras"):
                candidate = old_dataset
                enrichment_stats = stats_for_existing_dataset(candidate)
'''
new = '''            if state_code == "NJ" and old_dataset and old_dataset.get("cameras"):
                candidate = tag_existing_dataset_detail_levels(old_dataset)
                enrichment_stats = stats_for_existing_dataset(candidate)
'''
if old not in text:
    raise SystemExit("NJ preservation block not found")
text = text.replace(old, new, 1)
path.write_text(text)
