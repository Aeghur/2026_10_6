"""Adapter contract example. Import and call run(adapter); does not auto-run.

adapter.apply(Signal) -> actual Signal AFTER hardware acknowledges application
adapter.capture_after(timestamp) -> (clean_image, capture_midpoint_s, camera_frame_id)
adapter.signal -> actual Signal; adapter.calibration_id -> this measurement ID
No guessed UART packet format or camera-buffer flush is hidden in this example.
"""
import time
from lissajous_control import Controller, Capabilities, Frame, Target


def run(adapter, mode='circle', height_div=8):
    control = Controller(adapter.calibration_id,
        Capabilities(ramp_supported=True, continuous_phase=True,
                     max_frequency_hz=adapter.max_frequency_hz, max_vpp=adapter.max_vpp))
    decision = control.start(Target(mode, height_div), adapter.signal)
    try:
        while decision.status not in ('done', 'failed', 'stopped'):
            if decision.status == 'apply':
                command = decision.command
                actual = adapter.apply(command.signal)
                control.applied(command.command_id, actual, time.monotonic())
                capture_after = control.capture_after
            elif decision.status == 'capture':
                capture_after = decision.capture_after_s or time.monotonic()
            else:
                raise RuntimeError('Unexpected status: '+decision.status)
            image, captured_at, sequence = adapter.capture_after(capture_after)
            decision = control.step(Frame(image, adapter.signal, control.command.command_id,
                sequence, captured_at, adapter.calibration_id))
        # Light/buzzer only after done. Backend never toggles GPIO itself.
        return decision.to_dict()
    except BaseException:
        control.stop()
        raise
