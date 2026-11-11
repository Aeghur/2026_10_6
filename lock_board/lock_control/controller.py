"""v2.1: bounded image-feedback acquisition, tracking and sampled hold."""
from dataclasses import dataclass, asdict
import math
import numpy as np
from PIL import Image
from .branches import measure_branches
from .fit import phase_track
from .geometry import shape_error, target_distance
from .features import context
from .policy import Policy
from .tracking import local_phase, drift_fit

VERSION='2.1.0'
GAINS=(.4,.75,1.)
# Minimum slots only; real measured capture times always enter the fit.
TIMES=((0.,.06,.14,.23,.35,.50,.63,.77),
       (0.,.10,.24,.42,.68,1.,1.2,1.43),
       (0.,.08,.17,.27,.38,.50,.63,.77))
# Nonlinear angle ordering breaks the linear-angle/equal-time mirror alias.
OFFSETS=((0,90,225,45,270,135,315,180),
         (0,120,300,60,240,180,30,210),
         (0,180,45,270,90,315,135,225))

def finite(*xs):
    return all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in xs)

def wrap(x):return (x+180)%360-180

@dataclass(frozen=True)
class Signal:
    frequency_hz:float
    vpp:float
    phase_deg:float=0.
    def __post_init__(self):
        if not finite(self.frequency_hz,self.vpp,self.phase_deg) or self.frequency_hz<=0 or self.vpp<=0 or not 0<=self.phase_deg<360:
            raise ValueError('Invalid actual sine settings')

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
    history_age_s:float=4.
    local_max_drift_hz:float=.03
    local_min_drift_hz:float=.0015
    max_local_corrections:int=4
    def __post_init__(self):
        if any(not finite(v) or v<=0 for v in asdict(self).values()):raise ValueError('Options must be positive and finite')
        if self.hold_seconds<5 or self.timeout_s<=self.hold_seconds or self.shape_tolerance_div>.2:
            raise ValueError('Hold >=5s; timeout > hold; shape tolerance <=.2div')
        if not 2<=self.trim_limit_hz<=3 or not 0<self.frequency_step_hz<=.02:raise ValueError('Invalid frequency trim limits/step')
        if type(self.max_batches) is not int or self.max_batches>10 or self.target_height_div not in (2,4,6,8):
            raise ValueError('Invalid batch count or target height')
        if type(self.max_local_corrections) is not int or self.max_local_corrections>10 or not self.local_min_drift_hz<self.local_max_drift_hz<=.1:
            raise ValueError('Invalid bounded local tracking settings')

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
    """One measurement per instance; caller owns every camera and DAC action."""
    def __init__(self,source_frequency_hz,mode='circle',options=None,use_policy=True,continuous_phase=False):
        if not finite(source_frequency_hz) or not 1000<=source_frequency_hz<=100000 or source_frequency_hz%100:
            raise ValueError('Known nominal source must be a 100Hz multiple, 1k..100k')
        if mode not in ('circle','diagonal','infinity'):raise ValueError('Unknown target')
        if not continuous_phase:raise ValueError('Confirm DAC changes preserve the phase accumulator')
        self.nominal=float(source_frequency_hz);self.mode=mode;self.ratio=2 if mode=='infinity' else 1
        self.output_nominal=self.nominal*self.ratio;self.options=options or Options()
        self.policy=Policy() if use_policy else None;self.started=False;self.pending=None
        # CPU work before start()/the measurement deadline; no device I/O.
        target_distance(mode,self.options.target_height_div,384,384,(-5.,-5.,5.,5.))

    def _signal_ok(self,signal):
        if not isinstance(signal,Signal) or abs(signal.frequency_hz-self.output_nominal)>self.options.trim_limit_hz+self.options.frequency_step_hz:
            raise ValueError('Actual DAC frequency must be target nominal ratio plus bounded trim')

    def start(self,signal,command_id,applied_at_s,now_s=None):
        self._signal_ok(signal);now_s=applied_at_s if now_s is None else now_s
        if not finite(applied_at_s,now_s) or now_s<applied_at_s or not command_id:
            raise ValueError('Actual initial ACK and consistent times required')
        self.signal=signal;self.command_id=command_id;self.applied_at=float(applied_at_s);self.started_at=float(now_s)
        self.pending=None;self.started=True;self.done=False;self.seq=0;self.batch_count=0
        self.last_id=-1;self.last_time=-math.inf;self.calibration=None;self.last_fit=None
        self.hold_start=None;self.last_hold=None;self.correction_rate=0.;self.next_protocol=0
        self.local_samples=[];self.local_corrections=0;self.phase_anchor=None;self.diagonal_ready=False
        self._begin_probe(0)
        return Decision('capture','Capture fresh calibrated sine XY image',capture_after_s=max(now_s,self.applied_at+self.options.settle_s))

    def _begin_probe(self,protocol,preserve=False):
        old=[]
        if preserve and getattr(self,'batch_frequency',None)==self.signal.frequency_hz:
            old=[(im,t,wrap(p+self.base_phase-self.signal.phase_deg)) for im,t,p in self.samples]
            if old:
                oldest=old[-1][1]-self.options.history_age_s
                old=[s for s in old if s[1]>=oldest][-24:]
        self.stage='probe';self.protocol=protocol;self.samples=old;self.carried=len(old)
        self.attempts=0;self.current_valid=0;self.base_phase=self.signal.phase_deg
        self.batch_frequency=self.signal.frequency_hz;self.batch_trim=self.signal.frequency_hz-self.output_nominal
        self.batch_start=None;self.hold_start=None;self.last_hold=None;self.local_samples=[]
        self.diagonal_ready=False

    def _budget(self,now,remaining=None):
        required=self.options.hold_seconds if remaining is None else max(0.,remaining)
        if now+required>self.started_at+self.options.timeout_s:
            return Decision('insufficient_time','Not enough measurement time remains for a fresh verified hold',
                telemetry=dict(self.last_fit or {},required_hold_s=required,
                    remaining_total_s=max(0.,self.started_at+self.options.timeout_s-now),verified_hold_s=0.))
        return None

    def _issue(self,frequency,phase,now,reason,rate=0.):
        self.seq+=1;s=Signal(frequency,self.signal.vpp,phase%360)
        self.pending=Command(f'lock-{self.seq}',s,now+self.options.command_latency_s,reason,self.nominal,self.output_nominal,frequency-self.output_nominal,rate,False)
        return Decision('set_signal',reason,self.pending,telemetry=self.last_fit)

    def retime_pending(self,apply_at_s):
        if not self.pending:raise ValueError('No pending command')
        self.pending=self.pending.at_time(apply_at_s);return self.pending

    def _enter_hold(self,cmd,actual,applied):
        refining=self.mode=='diagonal' and not self.diagonal_ready
        target=90. if self.mode=='circle' or refining else 0.
        error=cmd.phase_rate_deg_s*(applied-cmd.apply_at_s)+wrap(actual.phase_deg-cmd.signal.phase_deg)
        self.phase_anchor=(applied,target+error,self.correction_rate)
        self.stage='refine_diagonal' if refining else 'hold'
        self.hold_start=None;self.last_hold=None;self.local_samples=[]

    def applied(self,command_id,signal,applied_at_s):
        if not self.pending:raise ValueError('No pending command')
        self._signal_ok(signal);cmd=self.pending
        if not finite(applied_at_s) or applied_at_s<self.applied_at:raise ValueError('ACK clock moved backwards')
        if command_id!=cmd.command_id or abs(signal.frequency_hz-cmd.signal.frequency_hz)>self.options.frequency_step_hz/2+1e-8 or abs(wrap(signal.phase_deg-cmd.signal.phase_deg))>self.options.phase_ack_tolerance_deg or abs(signal.vpp-cmd.signal.vpp)>1e-6:
            raise ValueError('ACK differs from the actual requested command')
        old_frequency=self.signal.frequency_hz;late=abs(cmd.phase_rate_deg_s*(applied_at_s-cmd.apply_at_s))
        self.signal=signal;self.command_id=command_id;self.applied_at=float(applied_at_s);self.pending=None
        if self.stage in ('await_correction','await_local','await_diagonal'):
            diagonal_final=self.stage=='await_diagonal'
            measured=(self.last_fit or {}).get('control_drift_hz',(self.last_fit or {}).get('residual_output_frequency_hz',0.))
            self.correction_rate=measured+signal.frequency_hz-old_frequency
            if late>self.options.timing_error_deg:self._begin_probe(self.next_protocol)
            elif self.stage=='await_local' or abs(self.correction_rate)<=self.options.lock_residual_hz:
                if diagonal_final:self.diagonal_ready=True
                self._enter_hold(cmd,signal,applied_at_s)
            else:self._begin_probe(self.next_protocol)
        ready=self.applied_at+self.options.settle_s
        if self.stage=='probe' and self.batch_start is not None:
            ready=max(ready,self.batch_start+TIMES[self.protocol][self.attempts])
        return Decision('capture','ACK recorded; wait for new image',capture_after_s=ready)

    def step(self,frame,now_s):
        if not self.started:raise ValueError('Call start() first')
        if not finite(now_s,frame.captured_at_s):raise ValueError('Finite same-clock acquisition times required')
        if self.done:return Decision('complete','Measurement completed; use a new instance for further monitoring',success=True,telemetry=self.last_fit)
        if now_s-self.started_at>self.options.timeout_s:return Decision('timeout','No verified lock within measurement time',telemetry=self.last_fit)
        if self.pending:return Decision('await_ack','Apply and acknowledge pending command',self.pending)
        if self.stage=='probe' and self.batch_count>=self.options.max_batches and self.attempts==0:
            return Decision('not_converged','Probe batch limit reached',telemetry=self.last_fit)
        if type(frame.frame_id) is not int or frame.frame_id<=self.last_id or frame.captured_at_s<=self.last_time:
            return Decision('recapture','Duplicate/non-monotonic image')
        if frame.captured_at_s>now_s+.001 or now_s-frame.captured_at_s>self.options.max_frame_age_s:
            return Decision('recapture','Stale image or clock mismatch')
        if frame.signal!=self.signal or frame.command_id!=self.command_id:
            return Decision('recapture','Image is not associated with the acknowledged output')
        ready=self.applied_at+self.options.settle_s
        if self.stage=='probe' and self.batch_start is not None:ready=max(ready,self.batch_start+TIMES[self.protocol][self.attempts])
        if frame.captured_at_s<ready:return Decision('wait','Output/display has not settled or next slot has not arrived',capture_after_s=ready)
        if not frame.calibration_id:return Decision('needs_calibration','Calibrated coordinate mapping required')
        if self.calibration and self.calibration!=frame.calibration_id:return Decision('restart_required','Geometry changed during measurement')
        a=np.asarray(frame.image)
        if a.dtype!=np.uint8 or a.shape not in ((384,384),(384,384,3)):
            raise ValueError('Expected corrected uint8 384x384 gray/RGB image')
        image=Image.fromarray(a).convert('L');self.calibration=frame.calibration_id
        self.last_id=frame.frame_id;self.last_time=frame.captured_at_s
        if self.stage=='probe':return self._budget(now_s) or self._probe(image,frame.captured_at_s,now_s)
        if self.stage=='refine_diagonal':return self._diagonal_refine(image,frame.captured_at_s,now_s)
        return self._hold(image,frame.captured_at_s,now_s)

    def _probe(self,image,t,now):
        if self.batch_start is None:self.batch_start=t
        fitted=measure_branches(image,self.mode);self.attempts+=1
        if fitted.valid and abs(fitted.y_height_div-self.options.target_height_div)>.2:
            return Decision('needs_amplitude_adjustment','Set amplitude externally and start a new measurement',telemetry={'height_div':fitted.y_height_div})
        if fitted.valid:
            self.current_valid+=1;self.samples.append((image,t,wrap(self.signal.phase_deg-self.base_phase)))
            self.samples=[s for s in self.samples if t-s[1]<=self.options.history_age_s][-32:]
        r=None
        if fitted.valid and len(self.samples)>=6:
            try:
                r=phase_track(self.samples,self.batch_frequency,self.mode,self.base_phase,2.2,
                    now+self.options.command_latency_s-self.samples[-1][1],source_grid_hz=0)
            except ValueError:pass
        if r is not None:
            coefficients=[measure_branches(im,self.mode).coefficient for im,_,_ in self.samples]
            r.update(valid_frames=len(self.samples),carried_frames=self.carried,
                coefficient_std=float(np.std(coefficients)),batch_span_s=t-self.batch_start,
                exposure_ms=self.options.exposure_ms,batch_time_s=t-self.started_at,
                attempted_frames=self.attempts,rejected_frames=self.attempts-self.current_valid,
                frame_branch_residual_div=fitted.residual_div,controller_version=VERSION)
            self.last_fit=r
            if r['accepted']:
                self.batch_count+=1
                return self._correct(r,now)
        if self.attempts<len(OFFSETS[self.protocol]):
            return self._issue(self.signal.frequency_hz,self.base_phase+OFFSETS[self.protocol][self.attempts],now,
                f'Collect nonlinear probe {self.attempts+1}/{len(OFFSETS[self.protocol])}')
        self.batch_count+=1
        if self.batch_count>=self.options.max_batches:
            return Decision('not_converged','Bounded probe retries exhausted; no reliable phase/drift fit',telemetry=self.last_fit)
        if r is None:reason='Insufficient valid/diverse views; retain same-frequency observations and supplement'
        elif abs(r['residual_output_frequency_hz'])>=2.15:
            return Decision('needs_frequency_check','Best fit is at the frequency acquisition boundary',telemetry=r)
        elif r['fit_error']>=.008:reason='Poor phase-fit quality; retain observations and change probe'
        else:reason='Ambiguous phase/drift candidates; retain observations and change probe'
        self._begin_probe((self.protocol+1)%3,preserve=True)
        return Decision('recapture',reason,capture_after_s=now+.02,telemetry=self.last_fit)

    def _frequency(self,proposed):
        if abs(proposed)>self.options.trim_limit_hz+self.options.frequency_step_hz:return None
        trim=float(np.clip(round(proposed/self.options.frequency_step_hz)*self.options.frequency_step_hz,
                           -self.options.trim_limit_hz,self.options.trim_limit_hz))
        return self.output_nominal+trim

    def _correct(self,r,now):
        budget=self._budget(now+self.options.command_latency_s+self.options.settle_s)
        if budget:return budget
        d=r['residual_output_frequency_hz']
        if abs(d)>2.15:return Decision('needs_frequency_check','Reliable residual exceeds acquisition range',telemetry=r)
        # Keep trained gain selection only inside the original feature domain.
        # The changed nonlinear schedule is deterministic, not newly trained.
        g=2
        if self.policy is not None and len(self.samples)<=8 and not self.carried:
            feature_r=dict(r,command_time_s=r['command_time_s']-self.started_at)
            g=self.policy.choose(context(self.nominal,self.mode,self.batch_trim,feature_r,self.batch_count-1),'frequency')
        if abs(d)>.1 and r['fit_error']<.002:g=2
        r.update(gain=GAINS[g],next_protocol=0,nominal_source_hz=self.nominal,control_drift_hz=d)
        frequency=self._frequency(self.batch_trim-GAINS[g]*d)
        if frequency is None:return Decision('trim_limit','Required compensation exceeds configured bound',telemetry=r)
        phi=r['phase_at_reference_deg']+360*d*(now+self.options.command_latency_s-r['reference_time_s'])
        target=90 if self.mode in ('circle','diagonal') else 0
        r['control_target_phase_deg']=target
        self.stage='await_correction';self.next_protocol=0
        return self._issue(frequency,self.base_phase+target-phi,now,'Compensate relative frequency and phase',360*d)

    def _track(self,image,t,mode=None,max_gap_s=None):
        if self.phase_anchor is None:return None
        fitted=measure_branches(image,self.mode);at,phi,rate=self.phase_anchor
        phase=local_phase(fitted,mode or self.mode,phi+360*rate*(t-at))
        if phase is None:
            self.local_samples=[];return None
        gap_limit=self.options.max_hold_gap_s if max_gap_s is None else max_gap_s
        if self.local_samples and t-self.local_samples[-1][0]>gap_limit:self.local_samples=[]
        self.local_samples.append((t,phase));self.local_samples=self.local_samples[-16:]
        return drift_fit(self.local_samples)

    def _diagonal_refine(self,image,t,now):
        # A narrow line has almost zero phase sensitivity. Refine at quadrature
        # first, then apply the final zero-phase setting and begin the real hold.
        budget=self._budget(now+self.options.command_latency_s+self.options.settle_s)
        if budget:return budget
        # This is acquisition, not the five-second target hold; probe captures
        # with three camera reads normally take about .268s on the real board.
        track=self._track(image,t,mode='circle',max_gap_s=.6)
        if track is None:
            return Decision('capture','Refine drift at quadrature before diagonal target',capture_after_s=t+.1,telemetry=self.last_fit)
        if abs(track['drift_hz'])>self.options.local_max_drift_hz or track['rms_deg']>.5:
            self._begin_probe(0)
            return Decision('recapture','Quadrature refinement inconsistent; reacquire phase',capture_after_s=now+.02,telemetry=self.last_fit)
        if track['drift_sigma_hz']>.001:
            return Decision('capture','Collect more quadrature views to reduce drift uncertainty',capture_after_s=t+.1,telemetry=dict(self.last_fit or {},local_tracking=track))
        d=track['drift_hz'];gain=1. if track['rms_deg']<.25 else .75
        frequency=self._frequency(self.signal.frequency_hz-self.output_nominal-gain*d)
        if frequency is None:return Decision('trim_limit','Diagonal refinement exceeds trim bound',telemetry=self.last_fit)
        phase=track['phase_deg']+360*d*(now+self.options.command_latency_s-track['reference_time_s'])
        self.last_fit=dict(self.last_fit or {},local_tracking=track,control_drift_hz=d,verified_hold_s=0.)
        self.stage='await_diagonal'
        return self._issue(frequency,self.signal.phase_deg+wrap(-phase),now,
            'Finish quadrature refinement and set diagonal; begin fresh hold',360*d)

    def _local_command(self,track,now):
        if self.local_corrections>=self.options.max_local_corrections:return None
        d=track['drift_hz']
        if abs(d)>self.options.local_max_drift_hz or track['rms_deg']>.5:return None
        threshold=max(self.options.local_min_drift_hz,1.5*self.options.frequency_step_hz,3*track['drift_sigma_hz'])
        target=90 if self.mode=='circle' else 0;phase_error=wrap(track['phase_deg']-target)
        frequency_needed=abs(d)>threshold
        phase_needed=abs(phase_error)>max(1.,3*track['phase_sigma_deg'])
        if not (frequency_needed or phase_needed):return None
        budget=self._budget(now+self.options.command_latency_s+self.options.settle_s)
        # A still-valid hold may finish without a discretionary adjustment.
        # If it actually leaves tolerance, _hold reports the budget failure.
        if budget:return None
        gain=1. if track['rms_deg']<.25 else .75
        frequency=self._frequency(self.signal.frequency_hz-self.output_nominal-(gain*d if frequency_needed else 0.))
        if frequency is None:return Decision('trim_limit','Local tracking exceeds trim bound',telemetry=self.last_fit)
        predicted=track['phase_deg']+360*d*(now+self.options.command_latency_s-track['reference_time_s'])
        self.last_fit=dict(self.last_fit or {},local_tracking=track,control_drift_hz=d,verified_hold_s=0.,controller_version=VERSION)
        self.local_corrections+=1;self.stage='await_local'
        return self._issue(frequency,self.signal.phase_deg+wrap(target-predicted),now,
            'Local multi-frame drift/phase correction; restart verified hold',360*d)

    def _hold(self,image,t,now):
        error=shape_error(image,self.mode,self.options.target_height_div)
        telemetry=dict(self.last_fit or {},shape_error_div=error,
            frequency_trim_hz=self.signal.frequency_hz-self.output_nominal,controller_version=VERSION)
        self.last_fit=telemetry;track=self._track(image,t)
        if track is not None:
            telemetry['local_tracking']=track
            local=self._local_command(track,now)
            if local:return local
        if error is None or error>self.options.shape_tolerance_div:
            self.hold_start=None;self.last_hold=None;budget=self._budget(now)
            if budget:return budget
            self._begin_probe(0)
            return Decision('recapture','Target contour outside tolerance; hold reset and new probe required',capture_after_s=now+.02,telemetry=telemetry)
        if self.last_hold is None or t-self.last_hold>self.options.max_hold_gap_s:self.hold_start=t
        self.last_hold=t;held=t-self.hold_start;telemetry['verified_hold_s']=held
        if held>=self.options.hold_seconds:
            self.done=True
            return Decision('complete','Target contour verified using fresh sampled images for >=5s',success=True,telemetry=telemetry)
        return self._budget(now,self.options.hold_seconds-held) or Decision('hold','Keep output and continue fresh image validation',capture_after_s=t+.1,telemetry=telemetry)
