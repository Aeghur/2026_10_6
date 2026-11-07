"""Hardware-independent, frame-by-frame frequency-trim/phase feedback."""
from dataclasses import dataclass,asdict
import math
import numpy as np
from PIL import Image
from .branches import measure_branches
from .fit import phase_track
from .geometry import shape_error
from .features import context
from .policy import Policy

GAINS=(.4,.75,1.)
TIMES=((0.,.06,.14,.23,.35,.50),(0.,.10,.24,.42,.68,1.),(0.,.08,.17,.27,.38,.50,.63,.77))
OFFSETS=((0,90,0,90,0,90),(0,60,120,180,240,300),(0,45,90,135,180,225,270,315))
def finite(*xs):return all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in xs)
def wrap(x):return (x+180)%360-180


@dataclass(frozen=True)
class Signal:
    frequency_hz:float
    vpp:float
    phase_deg:float=0.
    def __post_init__(self):
        if not finite(self.frequency_hz,self.vpp,self.phase_deg) or self.frequency_hz<=0 or self.vpp<=0 or not 0<=self.phase_deg<360:raise ValueError('Invalid actual sine settings')


@dataclass(frozen=True)
class Frame:
    image:object
    signal:Signal
    frame_id:int
    command_id:str
    captured_at_s:float
    calibration_id:str


@dataclass(frozen=True)
class Options:
    settle_s:float=.02
    command_latency_s:float=.01
    trim_limit_hz:float=2.25
    frequency_step_hz:float=.0001
    phase_ack_tolerance_deg:float=.6
    max_frame_age_s:float=.3
    max_hold_gap_s:float=.25
    hold_seconds:float=5.
    timeout_s:float=10.
    shape_tolerance_div:float=.2
    lock_residual_hz:float=.004
    max_batches:int=5
    timing_error_deg:float=.5
    target_height_div:int=8
    exposure_ms:float=2.
    def __post_init__(self):
        if any(not finite(v) or v<=0 for v in asdict(self).values()):raise ValueError('Options must be positive and finite')
        if self.hold_seconds<5 or self.timeout_s<=self.hold_seconds or self.shape_tolerance_div>.2:raise ValueError('Hold >=5s; timeout > hold; shape tolerance <=.2div')
        if not 2<=self.trim_limit_hz<=3 or not 0<self.frequency_step_hz<=.02:raise ValueError('Invalid frequency trim limits/step')
        if type(self.max_batches) is not int or self.max_batches>10 or self.target_height_div not in (2,4,6,8):raise ValueError('Invalid batch count or target height')


@dataclass(frozen=True)
class Command:
    command_id:str
    signal:Signal
    apply_at_s:float
    reason:str
    nominal_source_hz:float
    output_nominal_hz:float
    frequency_trim_hz:float
    phase_rate_deg_s:float=0.
    reset_accumulator:bool=False
    def at_time(self,t):
        if not finite(t):raise ValueError('Finite application time required')
        s=Signal(self.signal.frequency_hz,self.signal.vpp,(self.signal.phase_deg-self.phase_rate_deg_s*(t-self.apply_at_s))%360)
        return Command(self.command_id,s,t,self.reason,self.nominal_source_hz,self.output_nominal_hz,self.frequency_trim_hz,self.phase_rate_deg_s,False)
    def to_dict(self):return asdict(self)


@dataclass(frozen=True)
class Decision:
    status:str
    reason:str
    command:Command|None=None
    capture_after_s:float|None=None
    success:bool=False
    telemetry:dict|None=None
    def to_dict(self):return asdict(self)


class LockController:
    """One measurement, one instance. Caller owns camera, clock and DAC I/O."""
    def __init__(self,source_frequency_hz,mode='circle',options=None,use_policy=True,continuous_phase=False):
        if not finite(source_frequency_hz) or not 1000<=source_frequency_hz<=100000 or source_frequency_hz%100:raise ValueError('Known nominal source must be a 100Hz multiple, 1k..100k')
        if mode not in ('circle','diagonal','infinity'):raise ValueError('Unknown target')
        if not continuous_phase:raise ValueError('Confirm DAC changes preserve the phase accumulator')
        self.nominal=float(source_frequency_hz);self.mode=mode;self.ratio=2 if mode=='infinity' else 1;self.output_nominal=self.nominal*self.ratio
        self.options=options or Options();self.policy=Policy() if use_policy else None
        self.started=False;self.pending=None

    def _signal_ok(self,signal):
        if not isinstance(signal,Signal) or abs(signal.frequency_hz-self.output_nominal)>self.options.trim_limit_hz+self.options.frequency_step_hz:
            raise ValueError('Actual DAC frequency must be target nominal ratio plus bounded trim')

    def start(self,signal,command_id,applied_at_s,now_s=None):
        self._signal_ok(signal);now_s=applied_at_s if now_s is None else now_s
        if not finite(applied_at_s,now_s) or now_s<applied_at_s or not command_id:raise ValueError('Actual initial ACK and consistent times required')
        self.signal=signal;self.command_id=command_id;self.applied_at=float(applied_at_s);self.started_at=float(now_s)
        self.pending=None;self.started=True;self.done=False;self.seq=0;self.batch_count=0;self.last_id=-1;self.last_time=-math.inf;self.calibration=None
        self.last_fit=None;self.hold_start=None;self.last_hold=None;self.local_recovery=0;self.correction_rate=0.;self.next_protocol=1
        self._begin_probe(2 if self.mode=='infinity' else 0)
        return Decision('capture','Capture fresh calibrated sine XY image',capture_after_s=max(now_s,self.applied_at+self.options.settle_s))

    def _begin_probe(self,protocol):
        self.stage='probe';self.protocol=protocol;self.samples=[];self.attempts=0;self.base_phase=self.signal.phase_deg
        self.batch_frequency=self.signal.frequency_hz;self.batch_trim=self.signal.frequency_hz-self.output_nominal;self.batch_start=None;self.hold_start=None

    def _issue(self,frequency,phase,now,reason,rate=0.):
        self.seq+=1;trim=frequency-self.output_nominal
        s=Signal(frequency,self.signal.vpp,phase%360)
        self.pending=Command(f'lock-{self.seq}',s,now+self.options.command_latency_s,reason,self.nominal,self.output_nominal,trim,rate,False)
        return Decision('set_signal',reason,self.pending,telemetry=self.last_fit)

    def retime_pending(self,apply_at_s):
        if not self.pending:raise ValueError('No pending command')
        self.pending=self.pending.at_time(apply_at_s);return self.pending

    def applied(self,command_id,signal,applied_at_s):
        if not self.pending:raise ValueError('No pending command')
        self._signal_ok(signal);cmd=self.pending
        if not finite(applied_at_s) or applied_at_s<self.applied_at:raise ValueError('ACK clock moved backwards')
        if command_id!=cmd.command_id or abs(signal.frequency_hz-cmd.signal.frequency_hz)>self.options.frequency_step_hz/2+1e-8 or abs(wrap(signal.phase_deg-cmd.signal.phase_deg))>self.options.phase_ack_tolerance_deg or abs(signal.vpp-cmd.signal.vpp)>1e-6:raise ValueError('ACK differs from the actual requested command')
        old_frequency=self.signal.frequency_hz;late=abs(cmd.phase_rate_deg_s*(applied_at_s-cmd.apply_at_s))
        self.signal=signal;self.command_id=command_id;self.applied_at=float(applied_at_s);self.pending=None
        if self.stage=='await_correction':
            self.correction_rate=self.last_fit['residual_output_frequency_hz']+signal.frequency_hz-old_frequency
            if late>self.options.timing_error_deg:self._begin_probe(self.next_protocol)
            elif abs(self.last_fit['residual_output_frequency_hz'])<=self.options.lock_residual_hz:
                self.stage='hold';self.hold_start=None;self.last_hold=None;self.local_recovery=0
            else:self._begin_probe(self.next_protocol)
        elif self.stage=='await_local':
            if late>self.options.timing_error_deg:self._begin_probe(self.next_protocol)
            else:self.stage='hold';self.hold_start=None;self.last_hold=None
        ready=self.applied_at+self.options.settle_s
        if self.stage=='probe' and self.batch_start is not None:
            ready=max(ready,self.batch_start+TIMES[self.protocol][self.attempts])
        return Decision('capture','ACK recorded; wait for new image',capture_after_s=ready)

    def step(self,frame,now_s):
        if not self.started:raise ValueError('Call start() first')
        if not finite(now_s,frame.captured_at_s):raise ValueError('Finite same-clock acquisition times required')
        if self.done:return Decision('complete','Measurement completed; start a new instance for further monitoring',success=True,telemetry=self.last_fit)
        if now_s-self.started_at>self.options.timeout_s:return Decision('timeout','No verified lock within measurement time',telemetry=self.last_fit)
        if self.pending:return Decision('await_ack','Apply and acknowledge pending command',self.pending)
        if self.stage=='probe' and self.batch_count>=self.options.max_batches and self.attempts==0:return Decision('not_converged','Probe batch limit reached',telemetry=self.last_fit)
        if type(frame.frame_id) is not int or frame.frame_id<=self.last_id or frame.captured_at_s<=self.last_time:return Decision('recapture','Duplicate/non-monotonic image')
        if frame.captured_at_s>now_s+.001 or now_s-frame.captured_at_s>self.options.max_frame_age_s:return Decision('recapture','Stale image or clock mismatch')
        if frame.signal!=self.signal or frame.command_id!=self.command_id:return Decision('recapture','Image is not associated with the acknowledged output')
        ready=self.applied_at+self.options.settle_s
        if self.stage=='probe' and self.batch_start is not None:ready=max(ready,self.batch_start+TIMES[self.protocol][self.attempts])
        if frame.captured_at_s<ready:return Decision('wait','Output/display has not settled or next slot has not arrived',capture_after_s=ready)
        if not frame.calibration_id:return Decision('needs_calibration','Calibrated coordinate mapping required')
        if self.calibration and self.calibration!=frame.calibration_id:return Decision('restart_required','Geometry changed during measurement')
        a=np.asarray(frame.image)
        if a.dtype!=np.uint8 or a.shape not in ((384,384),(384,384,3)):raise ValueError('Expected corrected uint8 384x384 gray/RGB image')
        image=Image.fromarray(a).convert('L');self.calibration=frame.calibration_id;self.last_id=frame.frame_id;self.last_time=frame.captured_at_s
        if self.stage=='probe':return self._probe(image,frame.captured_at_s,now_s)
        return self._hold(image,frame.captured_at_s,now_s)

    def _probe(self,image,t,now):
        if self.batch_start is None:self.batch_start=t
        fitted=measure_branches(image,self.mode);self.attempts+=1
        if fitted.valid and abs(fitted.y_height_div-self.options.target_height_div)>.2:return Decision('needs_amplitude_adjustment','Set amplitude externally and start a new measurement',telemetry={'height_div':fitted.y_height_div})
        if fitted.valid:self.samples.append((image,t,wrap(self.signal.phase_deg-self.base_phase)))
        if self.attempts<len(OFFSETS[self.protocol]):
            return self._issue(self.signal.frequency_hz,self.base_phase+OFFSETS[self.protocol][self.attempts],now,'Collect probe '+str(self.attempts+1)+'/'+str(len(OFFSETS[self.protocol])))
        self.batch_count+=1
        if self.batch_count>self.options.max_batches:return Decision('not_converged','Probe batch limit reached',telemetry=self.last_fit)
        if len(self.samples)<6:
            self._begin_probe(2)
            return Decision('recapture','Fewer than six geometrically valid views; retry multi-angle batch',capture_after_s=now+.06)
        try:
            r=phase_track(self.samples,self.batch_frequency,self.mode,self.base_phase,2.2,now+self.options.command_latency_s-self.samples[-1][1],source_grid_hz=0)
        except ValueError as e:
            self._begin_probe(2);return Decision('recapture',str(e),capture_after_s=now+.06)
        coefficients=[measure_branches(im,self.mode).coefficient for im,_,_ in self.samples]
        r.update(valid_frames=len(self.samples),coefficient_std=float(np.std(coefficients)),batch_span_s=t-self.batch_start,
                 exposure_ms=self.options.exposure_ms,batch_time_s=t-self.started_at,attempted_frames=self.attempts,rejected_frames=self.attempts-len(self.samples))
        # Model timing features are relative to measurement start, while the
        # command calculation retains the absolute, acknowledged clock domain.
        feature_r=dict(r,command_time_s=r['command_time_s']-self.started_at)
        x=context(self.nominal,self.mode,self.batch_trim,feature_r,self.batch_count-1)
        g=2 if self.policy is None else self.policy.choose(x,'frequency')
        self.next_protocol=1 if self.policy is None else self.policy.choose(x,'phase')
        if self.mode=='infinity' and r['rejected_frames']:self.next_protocol=2
        if r['accepted'] and abs(r['residual_output_frequency_hz'])>.1 and r['fit_error']<.002:g=2
        r.update(gain=GAINS[g],next_protocol=self.next_protocol,nominal_source_hz=self.nominal)
        self.last_fit=r
        if not r['accepted'] or abs(r['residual_output_frequency_hz'])>2.15:
            self._begin_probe(2);return Decision('needs_frequency_check','Phase/drift ambiguous or outside +/-2Hz acquisition range',telemetry=r)
        proposed=self.batch_trim-GAINS[g]*r['residual_output_frequency_hz']
        if abs(proposed)>self.options.trim_limit_hz+self.options.frequency_step_hz:
            return Decision('trim_limit','Required compensation exceeds configured bound',telemetry=r)
        trim=float(np.clip(round(proposed/self.options.frequency_step_hz)*self.options.frequency_step_hz,-self.options.trim_limit_hz,self.options.trim_limit_hz))
        phi=r['phase_at_reference_deg']+360*r['residual_output_frequency_hz']*(now+self.options.command_latency_s-r['reference_time_s'])
        target=90 if self.mode=='circle' else 0
        self.stage='await_correction'
        return self._issue(self.output_nominal+trim,self.base_phase+target-phi,now,'Compensate relative frequency and phase',360*r['residual_output_frequency_hz'])

    def _hold(self,image,t,now):
        error=shape_error(image,self.mode,self.options.target_height_div)
        telemetry=dict(self.last_fit or {},shape_error_div=error,frequency_trim_hz=self.signal.frequency_hz-self.output_nominal)
        if error is None or error>self.options.shape_tolerance_div:
            f=measure_branches(image,self.mode)
            if self.local_recovery==0 and self.hold_start is None and f.valid and self.mode in ('circle','infinity') and now-self.started_at+5.12<=self.options.timeout_s:
                c=float(np.clip(f.coefficient,-1,1));local=math.degrees(math.acos(c))-90 if self.mode=='circle' else math.degrees(math.asin(c))
                if abs(local)<=20:
                    self.local_recovery+=1;self.stage='await_local'
                    phase=self.signal.phase_deg-local-360*self.correction_rate*(now+self.options.command_latency_s-t)
                    return self._issue(self.signal.frequency_hz,phase,now,'Local near-target phase recovery',360*self.correction_rate)
            self._begin_probe(self.next_protocol)
            return Decision('recapture','Target contour outside tolerance; hold reset and new probe required',capture_after_s=now+.06,telemetry=telemetry)
        if self.last_hold is None or t-self.last_hold>self.options.max_hold_gap_s:self.hold_start=t
        self.last_hold=t;held=t-self.hold_start;telemetry['verified_hold_s']=held;self.last_fit=telemetry
        if held>=self.options.hold_seconds:
            self.done=True;return Decision('complete','Target contour verified using fresh sampled images for >=5s',success=True,telemetry=telemetry)
        return Decision('hold','Keep output and continue fresh image validation',capture_after_s=t+.1,telemetry=telemetry)
