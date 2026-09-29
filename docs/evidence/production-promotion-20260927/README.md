# Production promotion — 2026-09-27

The development version from https://45.87.246.124:9443/ is published at
https://45.87.246.124/ as release `20260927-routing-workspace`.

- Published source: `4bc5a929e00a32c2e215a55c5fc3b8d22da502c5`.
- Previous deployed source snapshot: `e73f1433ab4e469e74392d8c145be0d224d64b0e`,
  branch `backup/production-before-routing-20260927`.
- Previous release: `/opt/geoscan-mvp/releases/20260925-reviewer-3`.
- Current release: `/opt/geoscan-mvp/releases/20260927-routing-workspace`.
- Independent Python environment: `/opt/geoscan-mvp/venvs/20260927-routing-workspace`.
- Private operational backup: `/opt/geoscan-mvp/backups/20260927-223019-before-routing`.
  It contains the PostgreSQL dump, workspace files, previous environment and service
  configuration, and previous downloadable archive. Private data is not in Git.

The release uses the existing production PostgreSQL database and authentication
configuration. All 104 scenes and 86 plans present immediately before the switch
were preserved. Subsequent browser verification added a control calculation.

## Verification

- `regression.log`: 96 API, workspace, terrain, routing and safety tests passed.
- `contract.log`: `make validate`, 22 contract bundles and 4 acceptance tests passed.
- `runtime.log`: independent runtime imports and `pip check` passed.
- `isolated-smoke.json`: S00 completed SAFE, 100% coverage in 4.95 seconds in an
  isolated temporary workspace before the service switch.
- `promotion.json`: source integrity, idle service, readiness and database row
  counts verified during promotion.
- `production-browser.json` and `production.png`: actual production login,
  19 templates, source-identical web assets, fleet base table, graph display and
  S00 calculation verified in Chromium. The production calculation completed SAFE
  with 100% coverage in 36.61 seconds; makespan 1183.32 seconds. These timings are
  individual smoke measurements, not a performance comparison. The scenario
  input remained unchanged by calculation. All six exports returned HTTP 200.
  No JavaScript errors occurred. Production and development readiness passed.
- `delivery.json`: downloadable archive replaced atomically with this source
  release; previous archive retained in the private backup.

The browser check ignored TLS certificate validation; it verifies application
behavior, not the certificate trust chain.

## Rollback reference

During a maintenance window, stop `geoscan-mvp.service`, restore the environment
file from the private backup to `/etc/geoscan-mvp/environment`, remove only the
new `/etc/systemd/system/geoscan-mvp.service.d/routing.conf` override, and point
`/opt/geoscan-mvp/current` back to the previous release above. Reload systemd and
start the service; verify `/health/ready`. Restore the previous delivery archive
if reverting the download as well. The old release and its Python environment
remain available. No database schema migration was performed; a code rollback
does not require restoring the database and discarding subsequent user work.

The evidence commit made after publication contains only reports and does not
change the deployed application source commit recorded above.
