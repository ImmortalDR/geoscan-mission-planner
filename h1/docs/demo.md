# DEMO_PATH — H1 Scene Coverage (Q-06)

## Happy path (≤5 мин)

```bash
cd h1
source .venv/bin/activate
pip install -e "./h1_coverage[dev]"

# 1) Модули
h1-coverage modules

# 2) S00 → bundle + карта + geojson
h1-coverage run \
  --scene h1_coverage/fixtures/conformance/S00_smoke_rgb \
  --output /tmp/S00.bundle.json \
  --export-geojson /tmp/S00_transects.geojson \
  --export-html /tmp/S00_map.html \
  --export-report /tmp/S00_angles.md \
  --no-strict-coverage \
  --print-meta

# 3) Открыть карту
xdg-open /tmp/S00_map.html   # или просто открыть файл в браузере

# 5) Live fixtures для H2
h1-coverage export-fixtures --out-dir fixtures/h1_h2
cp fixtures/h1_h2/*.bundle.json ../h3/fixtures/h1_h2/
python ../h3/scripts/validate_h1_h2_contract.py

# Handoff: ../docs/contract/H1_READY.md
python scripts/validate_h1_module_contracts.py
```

**DoD клик:** HTML-карта показывает полигон, NFZ (если есть), галсы и площадки.
