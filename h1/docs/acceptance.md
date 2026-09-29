# Проверки H1

Используйте окружение, установленное по корневому [README](../../README.md).
Из корня репозитория:

```bash
.venv/bin/python -m pytest -c pyproject.toml h1/h1_coverage/tests -q
.venv/bin/python scripts/validate_h1_h2_contract.py
```

Модульные тесты проверяют чтение сцены, координатные преобразования, DSM,
параметры БВС и датчиков, покрытие, отверстия, ограничения, кандидатов,
совместимость и выходной bundle. Контрольные данные включены в поставку.

[Модульные контракты](../contracts/h1_modules.v1.json) ·
[Фикстуры](../fixtures/h1_modules/INDEX.md) ·
[Контракт H1↔H2](../../docs/contract/H1_H2.md).
Наличие валидного bundle не заменяет проверку конечной траектории в H3.
