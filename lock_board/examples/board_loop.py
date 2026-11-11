"""Adapter skeleton: importing does not access camera/DAC hardware."""
import time
from lock_control import LockController,Signal,Frame,Options

def run(camera,dac,known_nominal_source_hz,mode='circle'):
    ctrl=LockController(known_nominal_source_hz,mode,
                        Options(frequency_step_hz=dac.frequency_step_hz),
                        use_policy=True,continuous_phase=True)
    current=dac.actual_signal();cid=dac.last_command_id()
    decision=ctrl.start(current,cid,dac.last_applied_at_s(),now_s=time.monotonic())
    while True:
        if decision.command:
            # Adapter must predict/reserve application time in board monotonic
            # clock. Retiming recomputes phase for transport/processing latency.
            cmd=ctrl.retime_pending(dac.reserve_application_time(time.monotonic()))
            ack=dac.set_sine(frequency_hz=cmd.signal.frequency_hz,vpp=cmd.signal.vpp,
                             phase_offset_deg=cmd.signal.phase_deg,apply_at_s=cmd.apply_at_s,
                             command_id=cmd.command_id,reset_accumulator=False)
            current,cid=ack.signal,ack.command_id
            decision=ctrl.applied(cid,current,ack.applied_at_s)
        if decision.success:return decision
        if decision.status in {'insufficient_time','timeout','not_converged','trim_limit','needs_frequency_check','needs_calibration','restart_required','needs_amplitude_adjustment'}:return decision
        if decision.capture_after_s:
            wait=decision.capture_after_s-time.monotonic()
            if wait>0:time.sleep(wait)
        shot=camera.capture_rectified()
        decision=ctrl.step(Frame(shot.image,current,shot.sequence,cid,
                                  shot.effective_captured_at_s,shot.calibration_id),time.monotonic())
