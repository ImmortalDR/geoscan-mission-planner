"""Authenticated access to the checked-in reviewer catalog and test evidence."""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def documentation_router(root: Path, web: Path):
    router = APIRouter()

    @router.get("/documentation", include_in_schema=False)
    def page():
        return FileResponse(web / "documentation.html")

    @router.get("/api/v1/documentation")
    def catalog():
        def read(path):
            file = root / path
            return json.loads(file.read_text()) if file.is_file() else None
        return {"catalog": read("algorithms/catalog.json"),
                "tests": read("docs/evidence/algorithms/summary.json"),
                "load": read("docs/evidence/load/summary.json"),
                "system": read("docs/evidence/system.json"),
                "documents": [{"title": name, "path": f"docs/{path}.md"} for name, path in (
                    ("Обзор для эксперта", "reviewer-guide"), ("Архитектура", "architecture"),
                    ("Текущий алгоритм и требования ТЗ", "current-planning"), ("Пояснительная записка", "submit-note"),
                    ("Руководство пользователя", "user-guide"), ("Форматы полётных заданий", "flight-export"),
                    ("Стек и библиотеки", "stack"), ("Внутренний контур и безопасность", "security"),
                    ("Эксплуатация и восстановление", "operations"), ("Покрытие требований", "requirements-matrix"))]}

    @router.get("/api/v1/documentation/file")
    def file(path: str):
        allowed = {str(p.relative_to(root)) for folder in (root / "docs", root / "algorithms", root / "examples")
                   for p in folder.rglob("*") if p.is_file() and not p.is_symlink() and p.suffix in {".md", ".csv", ".json", ".xml"}}
        if path not in allowed:
            raise HTTPException(404, "Document not found")
        return FileResponse(root / path, filename=Path(path).name)

    return router
