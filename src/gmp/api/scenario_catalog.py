"""Dataset templates plus versioned scenarios shipped inside the application."""
from pathlib import Path
import json
import re


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "scenario_templates"

# Presentation only: scenario inputs and their reproducible hashes stay intact.
# Public numbering follows the existing order within simple and complex folders.
# Persistent IDs remain stable for saved scenes, references and result hashes.
TEMPLATE_CODES = {
    f"S{old:02d}": f"S{new:02d}"
    for new, old in enumerate((0, 4, 5, 6, 7, 8, 9, 10, 11, 19,
                               1, 2, 3, 12, 13, 14, 15, 16, 17, 18))
}

TEMPLATE_LABELS = {
    "S00": ("Первая RGB-съёмка", "Одна область и один аппарат: от построения галсов до готового маршрута."),
    "S01": ("МГУ: съёмка территории 100 км²", "Большая территория, несколько площадок и совместная работа четырёх аппаратов."),
    "S02": ("Переделкино: область с вырезом", "Съёмка составной области с внутренним вырезом и реальными перепадами высот."),
    "S03": ("Орехово: полёты с перерывом", "Световое окно 09:00–19:00 и запрет 09:30–10:30 (МСК). Работа до и после перерыва."),
    "S04": ("Дозарядка между вылетами", "Один аппарат выполняет большую задачу за несколько вылетов с обслуживанием на базе."),
    "S05": ("Два аппарата, два датчика", "RGB- и мультиспектральная съёмка с разных стартовых площадок."),
    "S06": ("Слишком далёкая стартовая база", "Ресурса на вылет не хватает. Сценарий показывает причину отказа и вариант другой площадки."),
    "S07": ("Не хватает ресурса на посадку", "Единственная разрешённая площадка посадки слишком далеко: ожидается объяснимый отказ."),
    "S08": ("Самолёт рядом с запретной зоной", "Самолётный аппарат, препятствие и запретная зона: проверка пролётов и разворотов."),
    "S09": ("Слишком сильный ветер", "Ветер 14 м/с превышает возможности подходящего аппарата: ожидается отказ."),
    "S10": ("Старт и финиш на разных базах", "Вылет с одной площадки и обязательная посадка на другой."),
    "S11": ("Пять датчиков — с ветром", "Пять типов съёмки и подбор совместимых аппаратов. Западный ветер 3 м/с; сравните с S19."),
    "S12": ("МГУ: три зоны, четыре аппарата", "Группа Геоскан 701 распределяет RGB-съёмку трёх областей."),
    "S13": ("МГУ: четыре зоны, четыре аппарата", "RGB-съёмка четырёх областей группой Геоскан 701; сравните с S12."),
    "S14": ("МГУ: совместная работа 201 и 401", "Четыре области и аппараты с разными возможностями: три Геоскан 401 и один 201."),
    "S15": ("МГУ: пять зон для трёх аппаратов", "Распределение RGB-съёмки между двумя Геоскан 401 и одним 201."),
    "S16": ("МГУ: недостаток времени — решение не ожидается", "Семь зон RGB и LiDAR. Ограничения времени полёта и удалённые базы не позволяют текущему расчёту собрать полный план."),
    "S17": ("МГУ: дозарядка по пути к другой базе", "Четыре сложные области, Геоскан 201 и 401, RGB и LiDAR. Общий старт на западе, дозарядка только в центре, финиш на востоке."),
    "S18": ("МГУ: 18 зон и четыре аппарата", "Масштабный пример RGB- и лазерной съёмки с несколькими вылетами и дозарядками."),
    "S19": ("Пять датчиков — без ветра", "Те же условия, что в S11, но без ветра: можно сравнить направление галсов и маршруты."),
}


def template_presentation(directory: Path, spec: dict, metadata: dict) -> dict:
    def read(name):
        path = directory / "input" / name
        return json.loads(path.read_text()) if path.is_file() else {}

    code = directory.name.split("_")[0]
    title, summary = TEMPLATE_LABELS.get(code, (spec.get("title", directory.name), spec.get("purpose_ru", "")))
    summary = re.sub(r"\bS\d{2}\b", lambda match: TEMPLATE_CODES.get(match[0], match[0]), summary)
    areas = read("survey_areas.geojson").get("features", [])
    sites = read("landing_sites.geojson").get("features", [])
    wind = read("mission.json").get("wind", {})
    return {
        "display_code": TEMPLATE_CODES.get(code, code),
        "display_name": title,
        "summary": summary,
        "folder": "complex" if metadata.get("real_elevation", False) else "simple",
        "zone_count": len(areas),
        "site_count": len(sites),
        "sensor_types": sorted({f.get("properties", {}).get("survey_type") for f in areas}
                               - {None, ""}),
        "wind_ms": wind.get("speed_ms", 0),
    }


def scenario_directories(dataset: Path) -> dict[str, Path]:
    directories = {}
    for root in (dataset / "scenarios", TEMPLATE_ROOT):
        for directory in sorted(root.glob("S*")):
            if not (directory / "scenario.json").is_file():
                continue
            if directory.name in directories:
                raise ValueError(f"Duplicate template ID: {directory.name}")
            directories[directory.name] = directory
    return directories


def scenario_directory(dataset: Path, identifier: str) -> Path:
    return scenario_directories(dataset)[identifier]
