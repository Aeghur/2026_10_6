from dataclasses import dataclass, asdict
import math
import numpy as np
from PIL import Image
from .branches import measure_branches
from .fit import phase_track


def finite(*values):
    return all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in values)


def wrapped(value):return (value+180)%360-180


@dataclass(frozen=True)
class Signal:
    frequency_hz: float
    vpp: float
    phase_deg: float=0.  # Increasing register offset ADVANCES phase.

    def __post_init__(self):
        if not finite(self.frequency_hz,self.vpp,self.phase_deg) or self.frequency_hz<=0 or self.vpp<=0 or not 0<=self.phase_deg<360:
            raise ValueError('Invalid actual sine signal')


@dataclass(frozen=True)
class Frame:
    image: object  # PIL image or RGB/grayscale uint8 ndarray; NOT OpenCV BGR.
    signal: Signal
    frame_id: int
    command_id: str
    captured_at_s: float  # Effective acquisition midpoint, same monotonic clock as ACK.
    calibration_id: str


@dataclass(frozen=True)
class Options:
    settle_s: float=.08
    command_latency_s: float=.02
    max_frame_age_s: float=.3
    max_hold_gap_s: float=.25
    hold_seconds: float=5.
    control_timeout_s: float=10.
    shape_tolerance_div: float=.2
    amplitude_tolerance_div: float=.2
    max_drift_hz: float=.05
    max_bad_frames: int=3
    max_corrections: int=4
    timing_error_deg: float=.5
    phase_ack_tolerance_deg: float=.6

    def __post_init__(self):
        for k,v in asdict(self).items():
            if not finite(v) or v<=0:raise ValueError(k+' must be finite and positive')
        if self.hold_seconds<5 or self.control_timeout_s<=self.hold_seconds or self.shape_tolerance_div>.2:
            raise ValueError('hold >=5s; timeout > hold; shape tolerance <=0.2 div')
        if type(self.max_bad_frames) is not int or type(self.max_corrections) is not int:raise ValueError('Counts must be integers')


@dataclass(frozen=True)
class Command:
    command_id: str
    signal: Signal
    apply_at_s: float
    reason: str
    reset_accumulator: bool=False
    # Only correction commands need phase extrapolation to an application time.
    phase_rate_deg_s: float=0.

    def at_time(self,application_time_s):
        if not finite(application_time_s):raise ValueError('Invalid application time')
        delta=application_time_s-self.apply_at_s
        signal=Signal(self.signal.frequency_hz,self.signal.vpp,(self.signal.phase_deg-self.phase_rate_deg_s*delta)%360)
        return Command(self.command_id,signal,float(application_time_s),self.reason,False,self.phase_rate_deg_s)

    def to_dict(self):return asdict(self)


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    command: Command | None=None
    capture_after_s: float | None=None
    success: bool=False
    telemetry: dict | None=None

    def to_dict(self):return asdict(self)


class PhaseController:
    """One instance per measurement. Known source on exact 100 Hz grid.

    Call start(actual_signal,...), then step(frame,now_s). When step returns a
    command, apply it and ACK using applied(). Images while ACK is pending are
    never consumed. No frequencies or amplitudes are changed by this controller.
    """
    def __init__(self,source_frequency_hz,mode='circle',options=None,target_height_div=None,continuous_phase=False,output_command_frequency_hz=None):
        if not finite(source_frequency_hz) or not 1000<=source_frequency_hz<=100000 or source_frequency_hz%100:
            raise ValueError('Known input frequency must be an exact 100 Hz multiple, 1..100 kHz')
        if mode not in ('circle','diagonal','infinity'):raise ValueError('Unknown target mode')
        if target_height_div not in (None,2,4,6,8):raise ValueError('Target height: None/2/4/6/8 div')
        if not continuous_phase:raise ValueError('Caller must confirm DAC accumulator remains continuous')
        self.source_hz=float(source_frequency_hz);self.mode=mode;self.options=options or Options()
        self.ratio=2 if mode=='infinity' else 1;self.target_height=target_height_div
        # Local host-calibration integration: Signal retains the ACKed register
        # frequency, while source_hz and geometry use nominal physical units.
        self.output_command_hz=self.source_hz*self.ratio if output_command_frequency_hz is None else output_command_frequency_hz
        if not finite(self.output_command_hz) or self.output_command_hz<=0:raise ValueError('Invalid output command frequency')
        self.started=False;self.pending=None;self.finished=False

    def _check_signal(self,signal):
        if not isinstance(signal,Signal) or abs(signal.frequency_hz-self.output_command_hz)>1e-6:
            raise ValueError('Actual output must already have the target 1:1 or 2:1 frequency ratio')

    def start(self,signal,command_id,applied_at_s):
        self._check_signal(signal)
        if not finite(applied_at_s) or not command_id:raise ValueError('Actual command id/time required')
        self.signal=signal;self.command_id=str(command_id);self.applied_at=float(applied_at_s);self.started_at=float(applied_at_s)
        self.started=True;self.finished=False;self.pending=None;self.phase='probe';self.samples=[];self.base_phase=signal.phase_deg
        self.last_id=-1;self.last_time=float('-inf');self.calibration=None;self.bad=0;self.corrections=0;self.seq=0;self.hold_start=None;self.last_hold=None;self.last_fit=None
        return Decision('capture','Capture fresh calibrated sine XY image',capture_after_s=self.applied_at+self.options.settle_s)

    def _issue(self,phase,now,reason,rate=0.):
        self.seq+=1
        cmd=Command(f'phase-{self.seq}',Signal(self.signal.frequency_hz,self.signal.vpp,phase%360),now+self.options.command_latency_s,reason,False,rate)
        self.pending=cmd
        return Decision('set_phase',reason,command=cmd,telemetry=self.last_fit)

    def retime_pending(self,apply_at_s):
        """Call immediately BEFORE sending, if hardware cannot use the old time."""
        if self.pending is None:raise ValueError('No pending command')
        self.pending=self.pending.at_time(apply_at_s);return self.pending

    def applied(self,command_id,signal,applied_at_s):
        if not self.started or self.pending is None:raise ValueError('No command awaiting ACK')
        self._check_signal(signal)
        if not finite(applied_at_s) or applied_at_s<self.applied_at:raise ValueError('ACK times must be monotonic')
        cmd=self.pending
        if command_id!=cmd.command_id or abs(wrapped(signal.phase_deg-cmd.signal.phase_deg))>self.options.phase_ack_tolerance_deg or abs(signal.vpp-cmd.signal.vpp)>1e-6:
            raise ValueError('ACK must describe the actual requested phase command')
        late_error=abs(cmd.phase_rate_deg_s*(applied_at_s-cmd.apply_at_s))
        self.signal=signal;self.command_id=command_id;self.applied_at=float(applied_at_s);self.pending=None
        if self.phase=='await_correction':
            self.phase='hold';self.hold_start=None;self.last_hold=None
            if late_error>self.options.timing_error_deg:self._restart_probe()
        return Decision('capture','ACK recorded; wait for new display/camera data',capture_after_s=self.applied_at+self.options.settle_s)

    def _restart_probe(self):
        self.phase='probe';self.samples=[];self.base_phase=self.signal.phase_deg;self.hold_start=None;self.last_hold=None

    def step(self,frame,now_s):
        if not self.started:raise ValueError('Call start() first')
        if not finite(now_s,frame.captured_at_s):raise ValueError('Finite times required')
        if self.finished:return Decision('complete','Phase shape already verified',success=True,telemetry=self.last_fit)
        if now_s-self.started_at>self.options.control_timeout_s:return Decision('timeout','No verified phase lock within configured measurement time')
        if self.pending:return Decision('await_ack','Apply pending command and call applied()',command=self.pending)
        if not isinstance(frame.frame_id,int) or isinstance(frame.frame_id,bool) or frame.frame_id<=self.last_id or frame.captured_at_s<=self.last_time:
            return Decision('recapture','Duplicate or non-monotonic frame')
        if frame.captured_at_s>now_s+.001 or now_s-frame.captured_at_s>self.options.max_frame_age_s:
            return Decision('recapture','Stale frame or clock mismatch')
        if frame.command_id!=self.command_id or frame.signal!=self.signal:
            return Decision('recapture','Frame does not correspond to actual acknowledged output')
        if frame.captured_at_s<self.applied_at+self.options.settle_s:
            return Decision('wait','Display/camera has not settled',capture_after_s=self.applied_at+self.options.settle_s)
        if not frame.calibration_id:return Decision('needs_calibration','Calibration id required')
        if self.calibration and self.calibration!=frame.calibration_id:return Decision('restart_required','Geometry calibration changed during measurement')
        self.calibration=frame.calibration_id;self.last_id=frame.frame_id;self.last_time=frame.captured_at_s
        array=np.asarray(frame.image)
        if array.shape not in ((384,384),(384,384,3)) or array.dtype!=np.uint8:raise ValueError('Expected calibrated 384x384 uint8 grayscale/RGB bitmap')
        image=Image.fromarray(array).convert('L');fit=measure_branches(image,self.mode)
        if not fit.valid:
            self.bad+=1;self.hold_start=None;self.last_hold=None
            status='needs_better_image' if self.bad>=self.options.max_bad_frames else 'recapture'
            return Decision(status,fit.reason,telemetry={'residual_div':fit.residual_div,'bad_frames':self.bad})
        self.bad=0
        if self.target_height is not None and abs(fit.y_height_div-self.target_height)>self.options.amplitude_tolerance_div:
            return Decision('needs_amplitude_adjustment','Set output amplitude externally, then start a new measurement',telemetry={'height_div':fit.y_height_div})
        if self.phase=='probe':
            offset=wrapped(self.signal.phase_deg-self.base_phase)
            self.samples.append((image,frame.captured_at_s,offset))
            if len(self.samples)<6:
                next_offset=90 if len(self.samples)%2 else 0
                return self._issue(self.base_phase+next_offset,now_s,'Collect quadrature probe '+str(len(self.samples)+1)+'/6')
            try:
                # Current output already has the desired frequency. Keep it fixed.
                result=phase_track(self.samples,self.source_hz*self.ratio,self.mode,self.base_phase,
                                   command_delay_s=now_s+self.options.command_latency_s-frame.captured_at_s)
            except ValueError as e:
                self._restart_probe();return Decision('needs_frequency_check',str(e))
            self.last_fit=result
            if not result['accepted']:
                self._restart_probe();return Decision('needs_frequency_check','Phase/drift fit is ambiguous',telemetry=result)
            if abs(result['residual_output_frequency_hz'])>self.options.max_drift_hz:
                self._restart_probe();return Decision('needs_frequency_check','Residual physical drift exceeds phase-only limit; nominal frequency stays on 100 Hz grid',telemetry=result)
            self.corrections+=1
            if self.corrections>self.options.max_corrections:return Decision('not_converged','Too many phase corrections',telemetry=result)
            self.phase='await_correction'
            return self._issue(result['suggested_absolute_offset_deg'],now_s,'Correct fitted relative/generalized phase',360*result['residual_output_frequency_hz'])
        u=np.asarray(fit.u);mid=np.asarray(fit.middle);gap=np.asarray(fit.half_gap)
        target_mid=u if self.mode=='diagonal' else np.zeros_like(u)
        target_gap=np.zeros_like(u) if self.mode=='diagonal' else np.sqrt(np.maximum(0,1-u*u))*(2*np.abs(u) if self.mode=='infinity' else 1)
        shape=float(np.percentile(np.maximum(np.abs(mid-target_mid),np.abs(gap-target_gap)),95)*fit.y_height_div/2)
        telemetry=dict(self.last_fit or {},shape_error_div=shape,height_div=fit.y_height_div)
        if shape>self.options.shape_tolerance_div:
            self._restart_probe();return Decision('recapture','Target phase contour outside tolerance; begin a new probe batch',telemetry=telemetry)
        if self.last_hold is None or frame.captured_at_s-self.last_hold>self.options.max_hold_gap_s:self.hold_start=frame.captured_at_s
        self.last_hold=frame.captured_at_s
        held=frame.captured_at_s-self.hold_start;telemetry['verified_hold_s']=held;self.last_fit=telemetry
        if held>=self.options.hold_seconds:
            self.finished=True;return Decision('complete','Target phase contour verified by fresh sampled frames for >=5 seconds',success=True,telemetry=telemetry)
        return Decision('hold','Continue fresh image validation',capture_after_s=frame.captured_at_s+min(.1,self.options.max_hold_gap_s/2),telemetry=telemetry)
