Lissajous Board Strategy
=======================

Image-only Lissajous control SDK for a Linux board such as Radxa ROCK 4D.
The repository includes the exported frequency model, Python/JSON interfaces,
deployment instructions, adapter example, and bounded simulator tests.

Start here: README_zh.txt
Validation scope: VALIDATION_zh.txt
Adapter example: examples/integration_loop.py
Model provenance: lissajous_control/assets/manifest.json

Install in a Python environment with a compatible CPU PyTorch build:
  python -m pip install .
  python -m unittest discover -s tests -v

The learned component estimates frequency and selects ramp probes.
Amplitude, phase and sampled five-second hold use engineering control logic.
Hardware supports the required ramp and continuous phase according to the
team's confirmation; SDK hardware timing/accuracy validation remains pending.

This package returns DAC instructions; the integration application supplies
real hardware acknowledgements, calibrated camera images and capture times.
No training or physical output starts automatically on import.
