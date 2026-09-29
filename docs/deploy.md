# Integrated H1/H2/H3 Deployment

The public application is the H3 workspace with LIVE planning through canonical
`h1_coverage` and the standalone `h2.planner` package. The legacy `gmp.planner`
and H2 fixture viewer are not the integrated LIVE backend.

## Sources and Runtime

| Component | Source in this repository | Origin |
| --- | --- | --- |
| H1 | `h1/h1_coverage` | Canonical local package snapshot |
| H2 | `h2` | Local H2 snapshot via Git subtree |
| H3, API, UI, delivery | `src`, `web/h3`, `deploy` | `ImmortalDR/geoscan` |
| Demonstration inputs | `data` | Versioned, SHA-256-verified dataset |

Runtime uses a fixed, self-contained snapshot, not the changing worktrees.
`/opt/geoscan-mvp/current` points to a release containing `app/`, `h1/`, `h2/`,
`dataset/`, and a SHA-256 `MANIFEST.json` with its source commit. Inspect
`/health/ready` and `readlink -f /opt/geoscan-mvp/current` for the actual release.
A Git push alone does not deploy it. Container and internal-network setup:
[operations.md](operations.md).

`geoscan-mvp.service` runs as `geoscan-h3` on loopback `127.0.0.1:8092`.
nginx terminates TLS on port 443. Port 8443 redirects to 443. Persistent user
data after migration stays in `/var/lib/geoscan-mvp-pg`, with records in the
local PostgreSQL `geoscan_mvp` database. The old SQLite workspace remains in
`/var/lib/geoscan-h3` for rollback; it must not receive new writes after cutover.
Access credentials remain exclusively in
`/etc/geoscan-h3/environment`. The second environment file
`/etc/geoscan-mvp/environment` overrides runtime paths and the local socket DSN.
Named-user hashes are in `/etc/geoscan-mvp/users.json`; demonstration passwords
are in the root-only `/etc/geoscan-mvp/demo-credentials.txt`, never in Git.
The public demo preserves shared-code login. Set `GMP_DEPLOYMENT_MODE=internal`
and remove `GMP_ACCESS_CODE` for a named-users-only corporate deployment.

## Build and Stage

Build into a new version directory; never edit an active snapshot in place:

```bash
python3 deploy/prepare_h3_bundle.py /opt/geoscan-mvp/releases/RELEASE_ID
```

Use a new release-specific environment under `/opt/geoscan-mvp/venvs/`, prepared
by `scripts/install.py`; do not upgrade the running service environment in place.
The current server has an older
CPU: do not upgrade NumPy wheels blindly. Tested versions include NumPy 1.26.4,
Shapely 2.0.7, rasterio 1.3.11, and OR-Tools 9.10.4067.

Before public cutover, run the candidate with `GMP_DATA_DIR` pointing to the
separate `/var/lib/geoscan-review-stage` workspace and `geoscan_review_stage`
database. Two application processes must never recover or write the same
workspace concurrently; both filesystem and PostgreSQL advisory locks enforce it.
Local staging authentication keeps Secure cookies enabled; the acceptance
script permits their explicit replay only with `--loopback-staging` and a
`http://127.0.0.1` URL.

```bash
.venv/bin/python scripts/verify_h3_live.py http://127.0.0.1:8092 \
  --loopback-staging --env-file /etc/geoscan-h3/environment \
  --budget 5 --timeout 600 --output /secure/evidence/staging-live.json
```

The planner budget is the optimization budget, not the entire H1 generation,
trajectory construction, and independent validation wall-clock limit.

## Cutover and Rollback

For this existing host only, `deploy/cutover_mvp.py RELEASE VENV` performs a
read-only preflight. Add `--execute` after staging acceptance. It refuses active
jobs or an uncommitted release, creates a private backup, migrates the offline
SQLite state, switches systemd and restores the old configuration on readiness
failure. Create the destination PostgreSQL database and named-user file first.
The source SQLite workspace remains untouched. This script is not a generic
installer for a new server; use Compose or operations.md for that case.

1. Check that the live workspace has no queued/running jobs.
2. Preserve existing nginx configurations, runtime, environment, and state in
   a private backup directory. Never put this backup in a source delivery.
3. Stop `geoscan-mvp`, copy its state and configuration into a private backup,
   then migrate SQLite into an empty PostgreSQL database as in operations.md.
4. Stop staging, point `current` to the tested version, set the new data directory,
   DSN and `GMP_RELEASE_ID`. Update the systemd Python path and writable directories.
5. Start `geoscan-mvp`, check `/health/ready`, and only then enable the new
   nginx configuration. Run `nginx -t` before every reload.
6. Verify trusted HTTPS, LIVE calculations, refusals, exports, browser views,
   and completed-export persistence across a service restart.
7. Enable the new service at boot; disable the old H3 unit to avoid two writers.

For rollback, stop `geoscan-mvp`, restore its backed-up unit/environment and the
previous `current` symlink, run `systemctl daemon-reload`, and start the service
against the preserved SQLite state. Do not start the obsolete `geoscan-h3` unit.
Post-cutover writes in PostgreSQL are not copied back automatically: preserve
them before any rollback after users resume work. nginx need not change when
the loopback port remains 8092.

## Verification

```bash
curl --fail https://45.87.246.124/health/ready
systemctl is-active geoscan-mvp.service nginx.service
systemctl is-enabled geoscan-mvp.service
.venv/bin/python scripts/smoke_h3_service.py https://45.87.246.124 \
  --env-file /etc/geoscan-h3/environment --all-fixtures \
  --output /secure/evidence/service.json
```

SAFE means the independent H3 gate accepted the exact input and result in the
declared computational model. It is neither a global-optimality proof nor an
authorization for real-world flights. Proven infeasibility may be detected by
H3 before calling H1/H2; provenance explicitly records that short circuit.
Search failures remain uncertified and are not mathematical impossibility proofs.
