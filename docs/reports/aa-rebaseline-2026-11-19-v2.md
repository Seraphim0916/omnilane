# AA v5.0.1 Gemini XHigh transport correction

Snapshot `aa-v5.0.1-2026-11-19-v2` is **approved**. Operator basis: Vincent, 2026-11-19 —「AA基準有更新就更新／然後我核准直接做到上線」.

The v2 snapshot reuses the same captured AA pages as v1; it adds no new benchmark capture and changes no AA measured value. Antigravity returned HTTP 400 for Hydrogen and Neon XHigh selectors, while the other Neon efforts had successful provider responses in the existing evidence. The two XHigh measurements remain comparison-only reference rows; neither selector is a scored dispatch row, named alias, or transport-probe target. `routing.yaml` is unchanged.

Neon High, Medium and Low remain scored at 60, 55 and 44. Neon XHigh (61) and Hydrogen XHigh (54) remain in `reference_configs` only. The final registry has **88 measured rows, 22 derived rows, 3 comparison-only rows, 68 aliases and 60 unknown configurations** (110 scored rows total).

Rebuilt from the v1 registry at main commit `1a576553c66a21bc7d4c0daa3d03149622d99361` and the saved extract `docs/reports/aa-v5.0.1-extract-2026-11-19.json`, using `scripts/aa_rebaseline.py build --approval approved --approval-basis ... --revision 2`. Two independent builds produced the same registry SHA-256:

`78670f983091212c3f090fefe369b1f22affa3abd48b212314c12f93bde293fc`

The v1 compatibility entry remains in `config/transport-compatibility.json`; the v2 entry now pins the approved registry. The four vendor evidence reports use the v2 suffix and include the approval basis.
