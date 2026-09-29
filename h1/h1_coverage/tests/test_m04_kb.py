"""M4 UAV KB — contract h1.m4.uav_kb.v1"""
from __future__ import annotations

from h1_coverage.contracts import check_m4_kb
from h1_coverage.kb.catalog import MVP_MODELS, default_kb
from h1_coverage.modules import by_id


def test_module_registry_m4():
    assert by_id("M4").contract_id == "h1.m4.uav_kb.v1"


def test_m4_kb_mvp_models():
    kb = default_kb()
    check_m4_kb(kb)
    assert set(MVP_MODELS).issubset(set(kb.model_ids()))
    assert len(kb.model_ids()) >= 10
    assert kb.provenance_for("geoscan_201")["source"]


def test_a08_passport_vs_operational_derating():
    kb = default_kb()
    for mid in ("geoscan_201", "geoscan_401", "geoscan_701", "geoscan_801", "geoscan_gemini"):
        m = kb.models[mid]
        passport = float(m["passport_endurance_min"])
        operational = float(m["operational_endurance_min"])
        assert operational < passport
        prov = kb.provenance_for(mid)
        assert prov.get("source")
        assert operational <= passport * 0.85 + 1e-6
