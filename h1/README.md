# h1 — Scene & Coverage

Канон кода: [`h1_coverage/`](h1_coverage/). Выход: `H1H2Bundle` (`gmp.h1_h2.v1`).

| Файл | |
|------|--|
| [`AGENTS.md`](AGENTS.md) | Правила зоны |
| [`docs/architecture.md`](docs/architecture.md) | Архитектура модулей |
| [`docs/coverage.md`](docs/coverage.md) | DIY vs F2C |
| [`docs/inputs.md`](docs/inputs.md) | Входы |
| [`docs/acceptance.md`](docs/acceptance.md) · [`docs/backlog.md`](docs/backlog.md) | Приёмка |
| [`docs/demo.md`](docs/demo.md) | Demo path |
| [`literature/`](literature/) | Must-read для агентов |

```bash
cd h1 && source .venv/bin/activate
pip install -e "./h1_coverage[dev]"
h1-coverage export-fixtures --out-dir fixtures/h1_h2
pytest h1_coverage/tests -q
python3 ../h3/scripts/validate_h1_h2_contract.py
```
