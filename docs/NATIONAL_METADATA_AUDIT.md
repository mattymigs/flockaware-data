# FlockAware National Camera Metadata Audit

## Executive summary

FlockAware currently has two complementary national data paths:

1. **Coverage backbone (`pipeline/build_us_states.py`)** — downloads the compact DeFlock U.S. camera-position index, assigns records to states using Census boundaries, and guarantees broad 50-state + DC coverage. Outside NJ, the compact index primarily supplies coordinates and vendor/brand.
2. **OSM enrichment (`scripts/refresh_us.mjs`)** — queries detailed OpenStreetMap ALPR records and can supply substantially richer metadata, but public Overpass endpoints are slower and less reliable and therefore should not be used as one giant nationwide refresh.

New Jersey is intentionally preserved as an enriched dataset by the coverage backbone.

## Current normalized camera schema

The app/data schema already supports:

- id
- name
- latitude / longitude
- vendor
- operatorName
- directionDegrees / directionText
- municipality
- county
- surveillanceZone
- mountType
- startDate
- reference
- osmType / osmId / osmVersion / osmTimestamp
- sourceName / sourceURL
- dataStatus
- stateCode

The NJ-specific enrichment pipeline additionally preserves future intelligence fields that current app builds may safely ignore until adopted:

- model
- cameraType
- powerSource
- imageURL
- mapillaryKey
- website
- description
- note
- street

## Why NJ looks richer

The compact national index is optimized for fast nationwide camera positions. Its records contain latitude, longitude and brand/vendor, but not the detailed OSM tags needed for operator, road context, bearing, municipality/county, source record links, mapped timestamps, mounting information, imagery and descriptive fields.

NJ is sourced directly from detailed OSM elements and therefore retains those tags.

## Production architecture

### Coverage backbone

Run the compact national coverage rebuild **twice weekly**. It is efficient and gives FlockAware+ dependable nationwide coverage even when OSM enrichment is temporarily unavailable.

### Enrichment layer

Run detailed OSM enrichment in small geographic batches **twice weekly**, spread across the day. NJ receives a lightweight daily enrichment pass.

Each state enrichment runs independently. If a state returns a suspicious count drop or all Overpass providers fail, retain that state's last-known-good publication and continue with the remaining states. The final full-publication validator remains authoritative.

## Recommended metadata quality levels in the app

### Basic record

Coordinate + vendor from the national index. This is still a valid camera-location record, but the app should clearly label it as a basic record and avoid implying that missing metadata means the information does not exist.

### Detailed record

An OSM-enriched record containing one or more direct metadata signals such as source record, bearing, operator, jurisdiction, mapped timestamp, mount or other descriptive fields.

The existing app model already contains a `CameraRecordDetailLevel` concept and should continue using that distinction.

## Next enrichment improvements

1. Bring the NJ-only future fields (`model`, `cameraType`, `powerSource`, `imageURL`, `mapillaryKey`, `website`, `description`, `note`, `street`) into the general U.S. OSM enrichment transform.
2. Add metadata coverage counters to each state manifest entry: municipality, county, source URL, imagery and street coverage in addition to vendor/operator/bearing.
3. Add a source-health summary to workflow output so transient 504s and retained last-known-good states are visible without making the whole run red.
4. Consider reverse-geocoding only for records that remain basic after OSM enrichment; avoid doing it for every camera because of cost, rate limits and unnecessary churn.
5. Preserve provenance field-by-field where multiple sources are eventually merged.

## Launch assessment

The current national dataset is suitable for a FlockAware+ launch **provided the UI continues distinguishing basic versus detailed records**. Nationwide location coverage does not need to wait for NJ-level metadata completeness. Rich metadata should improve progressively through the enrichment layer without blocking national availability.
