# H2 demo

1. Install using README and run `pytest -q`.
2. `python scripts/demo.py`.
3. Open `artifacts/demo/S04_h2_multi_sortie/makespan/report.html`: two sorties,
   one physical UAV, service gap, all three tasks assigned.
4. Open `artifacts/demo/S05_h2_crossing/makespan/report.html`: two aircraft,
   delayed departure, zero residual conflicts. Move the time slider.
5. Open `artifacts/demo/S10_h2_different_sites/makespan/report.html`: distinct
   launch and landing sites.
6. Load an H1 output from `fixtures/S*.bundle.json` using `h2 plan` and inspect
   geometry and assumptions.
7. Для продвинутых алгосов: `fixtures/S01_full_customer_acceptance_100km2.bundle.json`
   (**10 БВС**) и viewer — см. [`../../docs/contract/H2_TEST_SCENES.md`](../../docs/contract/H2_TEST_SCENES.md).

Do not describe H2 FEASIBLE as an H3 SAFE certificate, VRPTW as real flight
validation, or fixed-wing scheduling envelopes as autopilot manoeuvre commands.
