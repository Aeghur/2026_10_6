"""Replace these adapter calls with your existing camera/UART implementation.

No camera or DAC is accessed by importing this file. All phase values below
are register OFFSETS, not instantaneous carrier phases.
"""
import time
from phase_control import PhaseController,Signal,Frame


def align_phase(camera,dac,known_source_hz,mode='circle'):
    # dac.actual_signal() returns Signal with actual acknowledged settings.
    # Before entering this function, sine frequency ratio and amplitude are set.
    ctrl=PhaseController(known_source_hz,mode,continuous_phase=True)
    current=dac.actual_signal();cid=dac.last_command_id()
    decision=ctrl.start(current,cid,dac.last_applied_at_s())
    while True:
        if decision.command is not None:
            # Reserve an actual application time on the SAME monotonic clock.
            # The adapter must implement prediction/scheduling and acknowledge it.
            when=dac.reserve_application_time(time.monotonic())
            command=ctrl.retime_pending(when)
            ack=dac.apply_sine_phase(command.signal.phase_deg,at_s=command.apply_at_s,
                                     command_id=command.command_id,reset_accumulator=False)
            # ACK must contain actual signal, id and application time.
            current,cid=ack.signal,ack.command_id
            decision=ctrl.applied(cid,current,ack.applied_at_s)
        if decision.success:return decision
        if decision.status in {'needs_frequency_check','needs_better_image','needs_amplitude_adjustment','needs_calibration','restart_required','timeout','not_converged'}:
            return decision  # Ask the corresponding subsystem to resolve it.
        if decision.capture_after_s:
            delay=decision.capture_after_s-time.monotonic()
            if delay>0:time.sleep(delay)
        # Capture must return FRESH exposure midpoint, sequence number and
        # geometry calibration id. Crop/correct/mask UI BEFORE calling step.
        shot=camera.capture_rectified()
        decision=ctrl.step(Frame(shot.image,current,shot.sequence,cid,
                                  shot.effective_captured_at_s,shot.calibration_id),time.monotonic())
