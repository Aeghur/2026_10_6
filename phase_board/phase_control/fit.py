"""Exported drift-aware six-frame phase fit; NumPy only, no training runtime."""
import math
import numpy as np
from .branches import measure_branches

def finite(value,name):
    value=float(value)
    if not math.isfinite(value):raise ValueError(name+" must be finite")
    return value

def phase_track(observations,output_hz,mode='circle',base_phase_deg=0.,max_drift_hz=2.,command_delay_s=.12,source_grid_hz=100):
    """Jointly fit image coefficients with known phase offsets AND timestamps.

    observations: (image, effective_capture_time_s, applied_offset_deg).
    output_hz is the unchanged, acknowledged frequency throughout this batch.
    Returns a phase increment for a continuous accumulator, not a reset phase.
    """
    output=finite(output_hz,'output_hz');base=finite(base_phase_deg,'base_phase_deg')
    drift=finite(max_drift_hz,'max_drift_hz');latency=finite(command_delay_s,'command_delay_s')
    if mode not in ('circle','diagonal','infinity') or not .01<=drift<=5 or not 0<=latency<=1 or output<=0:raise ValueError('无效相位测量参数')
    if not 6<=len(observations)<=32:raise ValueError('需要6..32张带已知相移和时刻的正弦XY图')
    times=[];offsets=[];coeff=[];fits=[]
    for image,t,p in observations:
        t=finite(t,'time');p=finite(p,'phase_offset')
        fitted=measure_branches(image,mode)
        if not fitted.valid:raise ValueError('相位图片不适用：'+fitted.reason)
        times.append(t);offsets.append(math.radians(p));coeff.append(fitted.coefficient)
        fits.append({'coefficient':fitted.coefficient,'residual_div':fitted.residual_div})
    times=np.array(times);offsets=np.array(offsets);coeff=np.array(coeff)
    if abs(np.mean(np.exp(2j*offsets)))>.9:raise ValueError('探测相移缺少正交信息；请交替使用0°和90°')
    if np.any(np.diff(times)<=0) or np.ptp(times)<.3:raise ValueError('相位帧必须按采集时刻递增，跨度至少0.3秒')
    ref=float(times[0]);tau=times-ref
    def solve(ds):
        a=2*np.pi*ds[:,None]*tau[None]+offsets[None]
        b=np.stack((np.cos(a),np.sin(a)),2)
        gram=np.einsum('fna,fnb->fab',b,b)+np.eye(2)[None]*1e-8
        rhs=np.einsum('fna,n->fa',b,coeff)
        beta=np.linalg.solve(gram,rhs[...,None])[...,0]
        err=np.mean((np.einsum('fna,fa->fn',b,beta)-coeff)**2,axis=1)
        # Reject an amplitude collapse rather than explaining every phase by zero.
        norm=np.linalg.norm(beta,axis=1)
        return err+.25*(norm-1)**2,beta
    grid=np.linspace(-drift,drift,1601);e,b=solve(grid);d=float(grid[e.argmin()])
    fine=np.linspace(max(-drift,d-.01),min(drift,d+.01),401);e,b=solve(fine);i=int(e.argmin());d=float(fine[i]);beta=b[i]
    ge,_=solve(grid)
    local=np.flatnonzero((ge<=np.r_[np.inf,ge[:-1]])&(ge<=np.r_[ge[1:],np.inf]))
    others=[float(ge[j]) for j in local if abs(grid[j]-d)>.2]
    other_gap=min(others)-float(e[i]) if others else None
    phi=math.degrees(math.atan2(beta[0],beta[1]) if mode=='infinity' else math.atan2(-beta[1],beta[0]))%360
    command_time=float(times[-1]+latency);pred=(phi+360*d*(command_time-ref))%360
    target=90. if mode=='circle' else 0.
    absolute=(base+target-pred)%360
    # Last image already has its own commanded offset applied.
    increment=(absolute-(base+math.degrees(offsets[-1]))+180)%360-180
    accepted=bool(e[i]<.008 and abs(d)<drift-.002 and (other_gap is None or other_gap>.002))
    ratio=2 if mode=='infinity' else 1
    measured_source=(output-d)/ratio
    if source_grid_hz not in (0,100):raise ValueError('source_grid_hz只能为100或旧版回归用0')
    source=round(measured_source/100)*100 if source_grid_hz else measured_source
    return {'accepted':accepted,'phase_at_reference_deg':phi,'reference_time_s':ref,'residual_output_frequency_hz':d,
            'estimated_source_frequency_hz':source,'measured_source_frequency_hz_diagnostic':measured_source,
            'source_grid_hz':source_grid_hz,'suggested_output_frequency_hz':source*ratio,
            'suggested_phase_increment_deg':increment,'suggested_absolute_offset_deg':absolute,
            'command_time_s':command_time,'predicted_phase_before_correction_deg':pred,'target_phase_deg':target,
            'fit_error':float(e[i]),'other_drift_peak_gap':other_gap,'mode':mode,'branch_fits':fits,
            'message':'在约定生效时刻保持累加器连续，修频并增加建议相位增量' if accepted else '相位/频差联合拟合不可靠；应重新采集',
            'timing_note':'若命令生效时间改变，必须按新时刻重新外推相位；不能稍后原样发送此相移'}

