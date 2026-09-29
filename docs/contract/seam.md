# Seam agreements H1 ↔ H2 (S08–S11)

Status: **accepted policy** for v1 — do not silently expand `eligible_*` or mutate
task geometry. Align scenes / chunking / fleet with the model instead.

Contract: [`H1_H2.md`](H1_H2.md) · ship bars: [`../guide.md`](../guide.md).

## Shared principles

1. Empty `eligible_uav_ids` → H2 keeps the task in `unassigned` with an explicit
   reason (S08). Never drop silently.
2. AtomicTask that cannot fit one sortie of its weakest eligible UAV → H1 must
   chunk smaller; H2 does not split geometry.
3. Geographic DEM / temporal NFZ / hard obstacles for transit → optional
   `h2.scene.v1` sidecar ([`../../h2/docs/scene.md`](../../h2/docs/scene.md)).
   Bundle alone ⇒ flat AMSL assumption; H3 remains authoritative for SAFE.

## S09 — wind / chunk adjacency

**Issue:** H1 chunked by ~30% of the weakest eligible endurance → many short
transect tasks; spatial snake connectivity is lost.

**Agreement:**
- H1 SHOULD prefer contiguous transect groups (adjacency / stable
  `extensions.h1.chunk_group` when emitting many slices of one swath family).
- H2 MAY use group hints for ordering when present; without them, nearest-task
  heuristics apply.
- Wind remains an **H1 eligibility** filter in v1; H2 does not re-score drain
  by wind direction.

## S10 — different start / end sites

**Issue:** Survey work needs ≥2 sorties, but `landing` role cannot relaunch.

**Agreement:**
- H2 returns **INFEASIBLE** with
  `landing_site_cannot_launch_required_next_sortie` (or capacity window proof).
- Do **not** teleport UAVs between sites or widen site roles.
- Fix options (choose one per scenario revision):
  1. H1 scene: give a `both` (or second start) site for re-launch; or
  2. Shrink work / enlarge fleet / window so one sortie suffices; or
  3. Explicit future ground-ferry model (out of H2 v1).

## S11 — payload / resource window

**Issue:** Many LiDAR tasks eligible only on one UAV; lower bound exceeds mission
window under H2's linear time model.

**Agreement:**
- H2 keeps eligibility as given; INFEASIBLE with
  `forced_work_exceeds_window` is correct under the declared model.
- Fix options: more LiDAR-capable UAVs, longer window, fewer tasks, or H1
  coarser chunking — not silent eligibility expansion.

## Demo / acceptance expectation

| Scene | Expected H2 posture |
|-------|---------------------|
| S09 | FEASIBLE respecting H1 wind ineligibility |
| S10 | INFEASIBLE until scene/fleet revised |
| S11 | INFEASIBLE until fleet/window revised |
| S08 | unassigned empty-eligible tasks preserved |

Joint E2E smoke (feasible path): S00, S02, S05 (+ scene sidecar when temporal) →
H2 plan → H3 validate. See `h2/scripts/e2e_acceptance.py`.

---

## Appendix — исходные вопросы H2 (архив)

Формулировки до фиксации политики. Для работы — секции выше.

### Принято с Тимофеем

- Bundle `gmp.h1_h2.v1` не изменяем; геосцену — sidecar. H2 не выдаёт Safety Certificate.
- S10: не ослаблять; явная INFEASIBLE.

### К H1 / H3 / общие

- S10/S11/S08 детали сцен; sidecar NFZ/DEM; chunk adjacency; AGL→AMSL flat-only без DEM.
- SAFE/сертификат только H3; VRPTW regression; landing_site без телепорта между вылетами.
