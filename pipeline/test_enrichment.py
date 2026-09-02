#!/usr/bin/env python3

from __future__ import annotations

import unittest

from enrichment import enrich_cameras, haversine_meters, vendor_compatible


class CameraEnrichmentTests(unittest.TestCase):
    def test_nearby_matching_keeps_stable_identity_and_coordinates(self) -> None:
        base = [{
            "id": "index-1",
            "latitude": 40.000000,
            "longitude": -74.000000,
            "vendor": "Flock Safety",
            "stateCode": "NJ",
            "sourceName": "OpenStreetMap",
        }]
        overlay = [{
            "id": "osm-node-99",
            "latitude": 40.000090,
            "longitude": -74.000050,
            "vendor": "Flock Safety",
            "directionDegrees": 180,
            "directionText": "180",
            "osmType": "node",
            "osmId": 99,
            "sourceURL": "https://www.openstreetmap.org/node/99",
            "stateCode": "NJ",
        }]

        cameras, stats = enrich_cameras(base, overlay)
        self.assertEqual(cameras[0]["id"], "index-1")
        self.assertEqual(cameras[0]["latitude"], 40.0)
        self.assertEqual(cameras[0]["longitude"], -74.0)
        self.assertEqual(cameras[0]["osmId"], 99)
        self.assertEqual(cameras[0]["directionDegrees"], 180)
        self.assertEqual(cameras[0]["detailLevel"], "enriched")
        self.assertEqual(stats.matched_count, 1)
        self.assertEqual(stats.unmatched_overlay_count, 0)

    def test_incompatible_known_vendors_do_not_match(self) -> None:
        base = [{
            "id": "index-1",
            "latitude": 40.0,
            "longitude": -74.0,
            "vendor": "Flock Safety",
            "stateCode": "NJ",
        }]
        overlay = [{
            "id": "osm-node-99",
            "latitude": 40.00001,
            "longitude": -74.00001,
            "vendor": "Motorola / Vigilant",
            "osmId": 99,
            "stateCode": "NJ",
        }]

        cameras, stats = enrich_cameras(base, overlay)
        self.assertEqual(cameras[0]["detailLevel"], "basic")
        self.assertNotIn("osmId", cameras[0])
        self.assertEqual(stats.matched_count, 0)

    def test_unknown_vendor_can_match_documented_vendor(self) -> None:
        base = [{
            "id": "index-1",
            "latitude": 40.0,
            "longitude": -74.0,
            "vendor": "unknown",
            "stateCode": "NJ",
        }]
        overlay = [{
            "id": "osm-node-99",
            "latitude": 40.00001,
            "longitude": -74.00001,
            "vendor": "Rekor",
            "osmId": 99,
            "stateCode": "NJ",
        }]

        cameras, stats = enrich_cameras(base, overlay)
        self.assertEqual(cameras[0]["vendor"], "Rekor")
        self.assertEqual(cameras[0]["osmId"], 99)
        self.assertEqual(stats.matched_count, 1)

    def test_one_overlay_record_is_not_reused(self) -> None:
        base = [
            {"id": "a", "latitude": 40.0, "longitude": -74.0, "vendor": None, "stateCode": "NJ"},
            {"id": "b", "latitude": 40.00001, "longitude": -74.00001, "vendor": None, "stateCode": "NJ"},
        ]
        overlay = [{
            "id": "osm-node-99",
            "latitude": 40.0,
            "longitude": -74.0,
            "vendor": None,
            "osmId": 99,
            "stateCode": "NJ",
        }]

        cameras, stats = enrich_cameras(base, overlay)
        enriched = [camera for camera in cameras if camera["detailLevel"] == "enriched"]
        self.assertEqual(len(enriched), 1)
        self.assertEqual(stats.matched_count, 1)

    def test_far_overlay_record_is_ignored(self) -> None:
        base = [{"id": "a", "latitude": 40.0, "longitude": -74.0, "vendor": None, "stateCode": "NJ"}]
        overlay = [{"id": "osm-node-99", "latitude": 40.01, "longitude": -74.01, "vendor": None}]
        cameras, stats = enrich_cameras(base, overlay)
        self.assertEqual(cameras[0]["detailLevel"], "basic")
        self.assertEqual(stats.matched_count, 0)

    def test_distance_calculation_is_reasonable(self) -> None:
        distance = haversine_meters(40.0, -74.0, 40.001, -74.0)
        self.assertGreater(distance, 100)
        self.assertLess(distance, 115)

    def test_vendor_normalization(self) -> None:
        self.assertTrue(vendor_compatible("Motorola / Vigilant", "Vigilant Solutions"))
        self.assertTrue(vendor_compatible("unknown", "Flock Safety"))
        self.assertFalse(vendor_compatible("Flock Safety", "Rekor"))


if __name__ == "__main__":
    unittest.main()
