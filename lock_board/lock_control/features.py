import math
import numpy as np

def context(nominal,mode,trim,measurement,iteration):
    """Pixels-derived phase fit plus public register/timing metadata only."""
    r=measurement;phi=math.radians(r.get('phase_at_reference_deg',0));q=r.get('branch_fits',[])
    vals=[nominal/100000,*[float(mode==m) for m in ('circle','diagonal','infinity')],trim/2.25,
          r.get('residual_output_frequency_hz',0)/2.2,float(r.get('accepted',False)),r.get('fit_error',1)*100,
          (r.get('other_drift_peak_gap') or 0)*10,math.sin(phi),math.cos(phi),iteration/5,
          np.mean([f['residual_div'] for f in q]) if q else 1,len(q)/8,
          r.get('batch_span_s',0),r.get('exposure_ms',0)/20,
          r.get('coefficient_std',0),r.get('valid_frames',0)/6,
          r.get('suggested_phase_increment_deg',0)/180,
          r.get('batch_time_s',0)/10,r.get('command_time_s',0)/10,1.]
    return np.clip(np.nan_to_num(vals),-10,10).astype(np.float32)

