"""Local tracking requires an accepted multi-angle phase anchor."""
import math
import numpy as np

def wrap(angle):
    return (angle + 180) % 360 - 180

def local_phase(fit, mode, prediction_deg, radius_deg=20.):
    if not fit.valid or mode not in ('circle', 'infinity'):
        return None
    c=float(np.clip(fit.coefficient, -1, 1))
    if mode == 'circle':
        a=math.degrees(math.acos(c));candidates=(a,-a)
    else:
        a=math.degrees(math.asin(c));candidates=(a,180-a)
    errors=[wrap(a-prediction_deg) for a in candidates]
    i=int(np.argmin(np.abs(errors)))
    if abs(errors[i]) > radius_deg or abs(abs(errors[0])-abs(errors[1])) < 5:
        return None
    return float(prediction_deg+errors[i])

def drift_fit(samples):
    """Small-rate regression with an uncertainty floor and outlier rejection."""
    if len(samples)<5:
        return None
    data=np.asarray(samples[-16:],dtype=float)
    t=data[:,0];y=data[:,1];x=t-t[-1]
    if np.ptp(t)<.65:
        return None
    design=np.column_stack((np.ones_like(x),x))
    beta=np.linalg.lstsq(design,y,rcond=None)[0]
    residual=y-design@beta
    mad=1.4826*np.median(np.abs(residual-np.median(residual)))
    keep=np.abs(residual-np.median(residual))<=max(.6,3*mad)
    if np.count_nonzero(keep)<5 or np.ptp(t[keep])<.65:
        return None
    x=x[keep];y=y[keep];design=design[keep]
    beta=np.linalg.lstsq(design,y,rcond=None)[0]
    residual=y-design@beta
    sigma=max(.15,float(np.sqrt(np.sum(residual**2)/max(1,len(y)-2))))
    sx=float(np.sum((x-np.mean(x))**2))
    return dict(drift_hz=float(beta[1]/360),drift_sigma_hz=sigma/math.sqrt(sx)/360,
                phase_deg=float(beta[0]),phase_sigma_deg=sigma,
                reference_time_s=float(t[-1]),samples=int(len(y)),span_s=float(np.ptp(t[keep])),
                rms_deg=float(np.sqrt(np.mean(residual**2))))
