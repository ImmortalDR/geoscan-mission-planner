# gmp ↔ H1

Canonical H1 algorithms live in `h1/h1_coverage`.

This directory only bridges:

```text
plan_mission() → gmp.coverage.build_coverage(scene)
               → h1_coverage.pipeline.run_h1(scene.source_dir)
               → convert AtomicTask → gmp.models
```

Do **not** re-add lawnmower / NFZ / feasibility here.
