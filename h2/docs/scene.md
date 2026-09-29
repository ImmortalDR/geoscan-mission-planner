# Optional geographic scene sidecar (`h2.scene.v1`) — **standard**

> **Policy (P0):** for any demo or acceptance that claims temporal NFZ, DEM-based
> AMSL, or hard transit obstacles, pass a sidecar via `--scene` / `Settings.scene`.
> Bundle-only runs are valid but assume **flat z = 0 m AMSL** and no temporal
> airspace — they must not be marketed as geography-validated.

The immutable H1 bundle remains `gmp.h1_h2.v1`. Geographic transit context is
supplied separately, never injected into mandatory bundle fields. The sidecar
uses `schema_version: "h2.scene.v1"` and `crs: {"metric_epsg": <bundle epsg>}`.
Mismatched CRS is rejected, not reprojected.

## Minimal example

```json
{
  "schema_version": "h2.scene.v1",
  "crs": {"metric_epsg": 32637},
  "forbidden": [
    {"type": "Feature", "properties": {"buffer_m": 5},
     "geometry": {"type": "Polygon", "coordinates": [[[40, -10], [60, -10], [60, 10], [40, 10], [40, -10]]]}}
  ],
  "terrain": {"origin": [0, 0], "cell_size_m": 100,
              "values": [[100, 110], [120, 130]]},
  "temporal": [
    {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]},
     "start_s": 0, "end_s": 3600}
  ]
}
```

## Field semantics

`forbidden` is optional and contains valid GeoJSON Polygon/MultiPolygon
geometries or Features. These are conservative exclusions at every altitude;
feature `properties.buffer_m` adds a nonnegative obstacle-specific margin.
Alternatively supply already buffered geometries with margin zero.

`allowed` optionally contains a Polygon/MultiPolygon geometry or Feature.
Routes must remain inside this area, including its concavities and holes.
The per-route `buffer_m` expands obstacles and contracts the allowed area; a
micrometre extra exclusion clearance avoids contact with original obstacle
boundaries. A visibility graph computes shortest polygonal paths. Results are
cached in both directions. Disconnected or prohibited endpoints raise `NoPath`.
Buffers alone do not prove fixed-wing turn-radius feasibility: a caller must
also check dynamic manoeuvres. Task geometry is checked, never repaired here.

Optional `terrain` is a regular finite rectangular grid of AMSL elevations in
metres. `origin` is the southwest grid node, columns increase x, rows increase
y, and `cell_size_m` is the spacing on both axes. At least two rows and columns
are required. Bilinear interpolation includes the outermost nodes. Queries
outside the grid fail explicitly. No terrain means an explicit flat zero-metre
reference assumption (`has_terrain == false`), not a terrain safety guarantee.

For integration with the independent H3 validator, use the original DEM raster:
`"terrain": {"kind": "raster", "path": "/absolute/path/to/dem.tif"}`.
Install the `terrain` optional dependency (`pip install '.[terrain]'`). The
sampler converts scene metric coordinates into the raster CRS and reads the
containing pixel, without bilinear smoothing or nodata extrapolation. Missing
CRS, nodata and out-of-bounds queries fail explicitly. Route sampling is at most
half a raster pixel or `sample_step_m`, whichever is smaller. H3 still checks
clearance at every crossed raster cell; this sampling is not a substitute for
that independent verification.

Takeoff and landing start/end at zero AGL. Their durations are at least the
declared aircraft times and long enough for the configured vertical speed.
Conflict resolution shifts departure times and downstream sorties. If the
mission window blocks a shift, conflicts remain explicit; the resolver does
not alter H1 survey altitude or introduce an unbudgeted airborne hold.

Optional `temporal` entries have `geometry` (Polygon/MultiPolygon), `start_s`
and `end_s` relative to mission origin. The scheduler conservatively moves a
whole affected sortie beyond the restriction's end (and shifts downstream
sorties of that aircraft). All altitudes are excluded: altitude bands are not
silently guessed. Independent checks inspect every segment and its time span.
This is conservative and can miss feasible altitude-specific alternatives.

## Fixture convention

| Bundle | Sidecar path (when used) |
|--------|--------------------------|
| `fixtures/S03_temporal_airspace_daylight.bundle.json` | `fixtures/scenes/S03_temporal.scene.json` |
| S05 / conflict demos needing DEM | `fixtures/scenes/<scene_id>.scene.json` |

CLI: `python -m h2.cli plan --bundle … --scene fixtures/scenes/S03_temporal.scene.json`

H3 still performs authoritative independent flight safety validation.

`operating_window` optionally restricts departures/completions to daylight:
`{"start_s": 600, "end_s": 36000}`, in seconds from the bundle time origin.
The annealing evaluator intersects it with the mission window; the H3 adapter
populates it from the original daylight interval. H3 independently checks it.
