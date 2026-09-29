# Local Frontend Dependencies

- MapLibre GL JS 4.7.1: `https://unpkg.com/maplibre-gl@4.7.1/dist/`.
- Mapbox GL Draw 1.5.0: `https://unpkg.com/@mapbox/mapbox-gl-draw@1.5.0/dist/`.
- Lucide 0.468.0: `https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js`.

Bundles are pinned and served locally; no external scripts are executed.
The only runtime external resource is the OpenStreetMap raster basemap at
`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, with visible attribution.
Unavailability of that basemap does not discard project geometry.

Integration references:

- https://maplibre.org/maplibre-gl-js/docs/examples/draw-polygon-with-mapbox-gl-draw/
- https://github.com/mapbox/mapbox-gl-draw/blob/main/docs/API.md
- https://lucide.dev/guide/lucide

The editor uses Mapbox GL Draw's maintained geometry and interaction engine,
including vertex editing, polygon holes imported from GeoJSON, and point moves.
Scene geometry is validated again by the server before calculation.
