# H1 — построение покрытия

Исходники: [h1_coverage/](h1_coverage/). Генератор строит галсы и задачи
без назначения конкретного БВС. Интеграция использует контракт
[gmp.h1_h2.v3](../docs/contract/H1_H2.md).

[Архитектура](docs/architecture.md) · [Покрытие](docs/coverage.md) ·
[Входные данные](docs/inputs.md) · [Модульные контракты](contracts/h1_modules.v1.json).

Используйте общее окружение из [корневого README](../README.md).
Из корня репозитория:

```bash
.venv/bin/python -m pytest -c pyproject.toml h1/h1_coverage/tests -q
.venv/bin/python scripts/validate_h1_h2_contract.py
```
