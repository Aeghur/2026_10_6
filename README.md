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
1280x720 camera image. The real DAC has no zero-amplitude command. With
`capture_background` enabled, the solver captures six deliberately different
traces and forms a low-percentile reference image. This removes the fixed
graticule and reflections before trace geometry is measured.

Validate trace separation and integer frequency-ratio lobe recognition against
a known X-channel source before running the closed loop. This changes Y to 1x,
2x and 3x the known frequency, saves every raw/warped/mask image, and finally
leaves Y at the known frequency and 45 degrees:

```bash
.venv/bin/python diagnose_axes.py --known-x-hz 1000
```

The report is written below `artifacts/diagnostics/frequency_validation_*`.

## Sawtooth Y-axis timebase

`sawtooth_frequency.py` is an independent frequency-measurement route. It
commands a free-running Y sawtooth, rectifies the complete 12x8 graticule,
removes the thin flyback by selecting the main trace component, and tests
whether repeated sweeps form one thin, single-valued sideways curve. At the
fundamental lock, X frequency equals the commanded sawtooth frequency.

Use 6/8 amplitude so the complete ramp remains visible:

```bash
.venv/bin/python sawtooth_frequency.py \
  --saw-frequency 1000 --amplitude 6 --samples 3
```

Search an unknown frequency automatically:

```bash
.venv/bin/python sawtooth_frequency.py --auto --amplitude 6 --samples 3
```

The automatic route first estimates high input frequencies from the spacing
of horizontal extrema bands produced by low-frequency sawtooth sweeps. It
falls back to sine-lobe geometry when too few bands are visible, then performs
four shrinking sawtooth searches around the estimate. If another source already
provides a coarse estimate, skip the sine probe with
`--initial-frequency 40000`.

For a known reference, add `--known-x-hz 1000`; the report then includes the
frequency error. Outputs are saved below
`artifacts/diagnostics/sawtooth_*`. A successful digital ACK is not sufficient:
the program returns an error unless a trace spanning the Y direction is also
visible. A mismatched free-running sawtooth produces a dense lattice because
successive sweeps start at different X phases. Independent clocks can also
retain a slow phase drift after the spatial search reaches the frequency. In
that case the program reports `estimated` with `estimated_x_hz`, while
`phase_locked` remains false.

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

## Collaborator frequency and phase strategy

The imported `board_strategy/` package comes from the collaborator branch
`huasomebody-strategy-main`. Its controller does not open the camera or UART.
`strategy_control.py` adapts it to this project's fixed camera calibration and
MSPM0 waveform protocol.

The camera runs in V4L2 manual exposure mode (`auto_exposure=1`). Before the
controller starts, the adapter outputs the standard 175 us ramp probe, captures
all values in `strategy_exposure_candidates`, and selects a usable fixed value
closest to the model's `strategy_target_exposure` (80 = approximately 8 ms).
Every candidate and `exposure_report.json` are saved below the run directory.

Run one target with:

```bash
.venv/bin/python strategy_control.py --mode circle --height-div 8
```

To review exposure candidates before allowing frequency/phase control, run:

```bash
.venv/bin/python strategy_control.py --exposure-test-only
```

Other modes are `diagonal` and `infinity`. The current UART protocol supports
1/2/3/4 Vpp nominal steps and at most 110 kHz. The adapter stops with a clear
error if the controller requests an intermediate amplitude or a higher
frequency; those cases require a firmware/protocol extension rather than a
fabricated acknowledgement. Raw frames, the corrected graticule, 384x384 model
inputs, exposure trials, decisions and the final result are saved under
`artifacts/diagnostics/strategy_*`.
