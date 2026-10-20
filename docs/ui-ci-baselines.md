# Desktop UI screenshot baselines

`tests/ui_visual_baselines.json` stores the reviewed macOS desktop fingerprints used by `test_ui_regression_gate.py`. The test also checks layout, overflow, contrast, page errors, and blank screens.

To update a baseline after an intentional design change:

1. Generate the affected screenshots with the locked dependencies on macOS.
2. Inspect each actual PNG for layout, content, contrast, clipping, and navigation.
3. Update only the fingerprints for reviewed screenshots.
4. Run the complete `test_ui_regression_gate.py` suite.

A passing fingerprint check verifies that the rendered desktop screenshot matches the reviewed reference. It does not replace interaction tests.
