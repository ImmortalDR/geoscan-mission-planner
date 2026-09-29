# H1 → H2 READY

**Status:** READY  
**Date:** 2026-09-20  
**Schema:** `gmp.h1_h2.v1`  
**Owner H1:** zone `h1` · package `h1_coverage`  
**For:** owner / agent H2

## What you can use now

Live bundles (read these, do not invent fields):

```text
h3/fixtures/h1_h2/*.bundle.json
h1/fixtures/h1_h2/*.bundle.json   # same content
```

Smoke set: `S00`, `S01` (large, **10 UAV**), `S02`, `S08`, `S09`, `S10`, `S11` (+ `S00_contract_smoke` if present).

Где H2 тестировать сложные алгосы (сайт vs bundles): [`H2_TEST_SCENES.md`](H2_TEST_SCENES.md).

Coverage honesty: production engine is **DIY lawnmower** (`diy_lawnmower_v1`); Fields2Cover bridge is unwired — see [`../../h1/docs/architecture.md`](../../h1/docs/architecture.md) §14.

Seam policy for S09/S10/S11: [`seam.md`](seam.md).
Ship bars (demo vs §29): [`../guide.md`](../guide.md).

Optional visuals (not required by H2): `*_transects.geojson`, `*_map.html` next to bundles under `h1/fixtures/h1_h2/`.

## Contract

- Spec: [`H1_H2.md`](H1_H2.md)
- Check: `python3 h3/scripts/validate_h1_h2_contract.py` → must exit 0
- H2 **must not** move task geometry; H1 **does not** assign `uav_id` / departure time
- Additive only: `extensions.h1.*` (energy_hints, site_recommendation, terrain, …) — ignore if unused

## How H1 regenerates fixtures

```bash
cd h1 && source .venv/bin/activate
h1-coverage export-fixtures --out-dir fixtures/h1_h2
cp fixtures/h1_h2/*.bundle.json ../h3/fixtures/h1_h2/
python3 ../h3/scripts/validate_h1_h2_contract.py
```

## H1 done means

- Backlog P0/P1/P2 closed ([`../../h1/docs/backlog.md`](../../h1/docs/backlog.md))
- `pytest h1_coverage/tests` green
- Validator green on `fixtures/h1_h2`

**Next product step is H2**, not more H1 polish.
