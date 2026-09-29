import viewer.server as server

from viewer.server import FIXTURES, scenario_index


def test_viewer_lists_every_h1_bundle():
    rows = scenario_index()
    assert {row["file"] for row in rows} == {
        path.name for path in FIXTURES.glob("*.bundle.json")
    }
    for row in rows:
        assert row["kind"] == "h1_output"
        assert row["tasks"] > 0 and row["uavs"] > 0
        assert "multi_uav" in row
    assert rows[0]["uavs"] >= rows[-1]["uavs"]
    assert rows[0]["file"].startswith("S01_")


def test_cpsat_and_lns_algorithms_exposed(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARTIFACTS", tmp_path)
    assert set(server.ALGORITHMS) >= {"greedy_baseline", "cpsat_warmstart", "lns_anytime"}
    run_id, item = server.run_scenario("S00_smoke_rgb.bundle.json", "cpsat_warmstart")
    assert item["plan"]["settings"]["cpsat"] is True
    assert "deconfliction" in item["plan"]
    deco = item["plan"]["deconfliction"]
    assert "ladder_used" in deco
    row = server.scenario_results("S00_smoke_rgb.bundle.json")[0]
    assert "conflicts_before" in row and "conflicts_remaining" in row


def test_greedy_preview_only_writes_after_save(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARTIFACTS", tmp_path)
    run_id, item = server.run_scenario("S00_smoke_rgb.bundle.json", "greedy_baseline")
    assert item["plan"]["settings"]["cpsat"] is False
    assert item["plan"]["settings"]["max_iterations"] == 0
    assert "H2 diagnostic plan" in item["report"]
    assert list(tmp_path.iterdir()) == []

    target = server.save_run(run_id)
    assert target == tmp_path / "S00_smoke_rgb" / "greedy_baseline"
    assert {path.name for path in target.iterdir()} == {
        "plan.json", "report.html", "routes.geojson"
    }


def test_results_survive_save_and_are_scoped_to_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(server, "RESULTS", {})
    name = "S00_smoke_rgb.bundle.json"
    assert server.scenario_results(name) == []
    run_id, item = server.run_scenario(name, "greedy_baseline")
    rows = server.scenario_results(name)
    assert len(rows) == 1
    assert rows[0]["metrics"] == item["plan"]["metrics"]
    assert rows[0]["saved"] is False
    assert server.scenario_results("S02_multipolygon_holes.bundle.json") == []
    server.save_run(run_id)
    assert server.scenario_results(name)[0]["saved"] is True
    server.RESULTS.clear()  # Simulate restarting the server.
    rows = server.scenario_results(name)
    assert len(rows) == 1 and rows[0]["saved"] is True
    assert rows[0]["metrics"] == item["plan"]["metrics"]
    server.run_scenario(name, "greedy_baseline")
    rows = server.scenario_results(name)
    assert len(rows) == 1 and rows[0]["saved"] is False
