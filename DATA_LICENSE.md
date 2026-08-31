# Data licensing, attribution, and provenance

## OpenStreetMap-derived camera records

Camera records in `states/` are derived from OpenStreetMap features tagged as automatic license plate reader surveillance equipment, including:

```text
man_made=surveillance
surveillance:type=ALPR
```

OpenStreetMap data is available under the **Open Database License (ODbL) 1.0**.

**Required attribution:** © OpenStreetMap contributors

- OpenStreetMap: https://www.openstreetmap.org
- ODbL 1.0: https://opendatacommons.org/licenses/odbl/1-0/
- OpenStreetMap copyright and licensing: https://www.openstreetmap.org/copyright

Each normalized record preserves a direct OpenStreetMap object link whenever the upstream object ID is available.

## What the data means

A published point means that the upstream source contained a feature mapped as an ALPR camera at the time of the platform build. It does **not** mean that Mignone Labs LLC independently field-verified the equipment, its operator, its vendor, its direction, its activity, or its continued physical presence.

Records may be:

- incomplete;
- duplicated;
- incorrectly tagged;
- moved or removed;
- outdated;
- missing available metadata; or
- absent even though equipment exists in the area.

Accordingly, absence from the dataset does not establish that a route, property, municipality, or jurisdiction is free of ALPR or other surveillance equipment.

## Change records and alerts

`changes/` describes differences between consecutive validated publications. An entry marked `added` means **newly present in the published dataset**, not necessarily newly installed in the physical world. User-facing alerts must preserve that distinction.

## Other data layers

Future public-record, agency-policy, traffic-camera, and community-report layers will retain separate provenance and licensing metadata. They will not be silently represented as OpenStreetMap-derived or independently verified.

## Affiliation disclaimer

FlockAwareNJ+ and Mignone Labs LLC are not affiliated with, endorsed by, or sponsored by Flock Safety, OpenStreetMap, DeFlock, FlockHopper, any camera manufacturer, any government agency, or any law-enforcement organization.

## Code in this repository

Automation and normalization scripts are provided separately from the ODbL-covered database content. See `LICENSE` for the repository script license.
