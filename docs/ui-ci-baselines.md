# UI screenshot baselines in CI

The responsive UI gate uses a **16×16 grayscale image fingerprint** (SHA-256)
in addition to layout, overflow, accessibility, page-error, and blank-screen checks.
A fingerprint is inherently sensitive to browser/font rendering. Do **not**
update the hashes to dismiss layout regressions.

Two independently maintained references exist:

- `tests/ui_visual_baselines.json`: original development reference, retained
  for macOS and other non-Linux platforms.
- `tests/ui_visual_baselines_linux.json`: GitHub Actions Ubuntu 24.04 reference,
  captured on 2026-10-13 from the [UI regression run](https://github.com/longnt27/jobradar/actions/runs/38381726571)
  (run artifact `ui-regression-1`). That run used Python 3.12,
  Playwright 1.63.0, Chromium 153.0.8010.12, Tectonic 0.17.0, and
  DejaVu Serif as a fallback when Times New Roman was unavailable.

## Review and update procedure

1. Use the pinned CI runner and locked dependencies to generate screenshots.
2. Download the **actual PNG** artifact for every changed surface, theme and
   viewport. Inspect layout, content, contrast, clipping and navigation.
3. Compare old and new screenshots visually; reject unexpected differences.
4. Update only the hashes for reviewed images. Keep the opposite platform's
   baseline intact.
5. Re-run the complete `test_ui_regression_gate.py` suite. The baseline
   assertions must remain active: no unconditional skip/auto-approval.
6. Changes to runner image, font stack, browser version, dynamic fixture content
   or image-size normalization may require a deliberate baseline migration.

A pass proves that the current screenshot fingerprints match the reviewed
reference. It does **not** prove pixel-perfect equivalence to another OS.
