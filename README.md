# Radxa camera calibrator

This component runs on the ROCK 4D. It observes `/dev/video0`, never reads
X-channel state, and supports either the real MSPM0 UART link or the original
HTTP simulator. `config.json` selects the backend; the real setup uses
`control_backend: uart` and `/dev/ttyS4`.

```bash
cd /home/radxa/vision_sorter
.venv/bin/python vision_calibrator.py --once
.venv/bin/python vision_calibrator.py --image artifacts/captures/camera_check.jpg
.venv/bin/python vision_calibrator.py --check-control
.venv/bin/python vision_calibrator.py
```

`--once` is read-only: it captures and analyzes the current oscilloscope image
without changing DAC frequency, phase or amplitude. Run it before enabling the
closed-loop solver and inspect `artifacts/diagnostics/current.jpg` and
`artifacts/diagnostics/current_mask.png`.
`--image` performs the same read-only analysis on a saved image and does not
open `/dev/video0`, so it can be used while another preview process owns the
camera.
`--check-control` only opens and closes the selected UART/HTTP backend; it does
not send a waveform command or change the DAC output.

The real oscilloscope uses a white XY trace, so `trace_color` is `white` and
`grid_corners` must describe the four analysis-region corners in the corrected
1280x720 camera image. The real DAC currently has no zero-amplitude command; therefore
`capture_background` is disabled and the bright white trace is thresholded
directly.

Generated diagnostics:

- `artifacts/diagnostics/current.jpg`, `current_mask.png`: current rectified grid and trace
- `artifacts/results/final_result.jpg`, `final_result_mask.png`: final result
- `artifacts/results/result.json`: frequency, phase, score and measurement count

Capture one timestamped real-scope frame plus a candidate lens-corrected copy:

```bash
.venv/bin/python capture_scope.py
```

## Real oscilloscope geometry calibration

The photographed graticule has 12 horizontal divisions and 8 vertical
divisions.  Calibration starts from the four corners of the complete graticule
and derives the centred 8x8-div analysis square.  The waveform itself must not
be used as a geometry reference.

For the current fixed camera position, review or regenerate the calibration
with:

```bash
cd /home/radxa/vision_sorter
.venv/bin/python calibrate_scope.py --image artifacts/captures/camera_test.jpg \
  --full-grid-corners 155 24 1127 24 1142 681 158 681
```

Inspect `artifacts/calibration/scope/scope_calibration_overlay.jpg`: the yellow outline must follow the
full graticule and the green lattice must follow the central 8x8 divisions.
Only after visual review, persist a new calibration by adding
`--write-config`. Recalibrate whenever the camera, lens, focus, resolution or
oscilloscope position changes.

## Lens distortion calibration

Lens correction runs before the graticule perspective transform. Photograph a
10x7-inner-corner checkerboard at the same 1280x720 camera resolution in at least
12 varied positions: centre, all four edges, all four corners, tilted, near and
far. Keep the full checkerboard visible and do not change focus afterward.

With the camera fixed, move only the checkerboard. Over SSH, capture one valid
pose per Enter press (detected previews are saved beside the raw images):

```bash
cd /home/radxa/vision_sorter
.venv/bin/python capture_lens_images.py --count 15 --columns 10 --rows 7
```

For live positioning, start the browser preview instead:

```bash
.venv/bin/python lens_preview.py
```

Open `http://192.168.1.198:8080` on a computer on the same Wi-Fi. The page
shows live video, complete-corner status and the next suggested pose. Press
Space or click Save only while the overlay reports `CORNERS OK`.

```bash
cd /home/radxa/vision_sorter
.venv/bin/python calibrate_lens.py 'artifacts/calibration/lens/images/*.jpg' \
  --columns 10 --rows 7 --write-config
```

Review the JSON and preview under `artifacts/calibration/lens/`. A mean view
error below roughly 0.5 px is a useful target. After enabling a new lens
calibration, capture a fresh oscilloscope image and rerun `calibrate_scope.py`
with corners measured in the undistorted image; raw-image corner coordinates
are no longer valid.

## Image layout

- `assets/calibration/`: stable source assets such as the clean checkerboard
- `artifacts/captures/`: raw camera checks and their rectified counterparts
- `artifacts/calibration/scope/`: graticule calibration report and review images
- `artifacts/calibration/lens/`: checkerboard captures, lens report and preview
- `artifacts/diagnostics/`: current masks and axis-intersection diagnostics
- `artifacts/results/`: final solver images and machine-readable result
- `artifacts/legacy/`: retained images produced by the earlier simulator flow
