"""Track a continuous curve among row segments, retaining residual evidence."""
import cv2
import numpy as np
from recognize_landmarks import runs


def track_segments(groups, span, expected_step):
    """Second-order dynamic programming: width support plus velocity continuity."""
    ys = sorted(y for y, g in groups.items() if g)
    if len(ys) < 20:
        raise ValueError('Too few rows containing trace segments')
    nodes = [np.array([((a+b-1)/2, b-a) for a,b in groups[y]]) for y in ys]
    # Keep pair states so acceleration, rather than proximity alone, selects a path.
    costs = np.zeros((1, len(nodes[0]))) - .35*np.log(np.minimum(nodes[0][:,1],12))[None,:]
    previous_velocity = np.zeros_like(costs)
    history = []
    for i in range(1,len(ys)):
        gap = ys[i]-ys[i-1]
        velocity = (nodes[i][:,0][None,:]-nodes[i-1][:,0][:,None])/gap
        next_cost = np.full(velocity.shape,np.inf)
        back = np.zeros(velocity.shape,np.int32)
        for prev in range(len(nodes[i-1])):
            accel = (velocity[prev][None,:]-previous_velocity[:,prev,None])/max(1.,expected_step*.35)
            transition = costs[:,prev,None] + np.minimum(accel**2,100)
            if i == 1:
                transition = costs[:,prev,None] + .03*(velocity[prev][None,:]/expected_step)**2
            index = np.argmin(transition,axis=0)
            next_cost[prev] = transition[index,np.arange(len(nodes[i]))] - .35*np.log(np.minimum(nodes[i][:,1],12))
            next_cost[prev] += 30*(np.abs(velocity[prev])>expected_step*2.5)
            back[prev] = index
        history.append(back)
        costs,previous_velocity=next_cost,velocity
    a,b = np.unravel_index(np.argmin(costs),costs.shape)
    selected=[b,a]
    for i in range(len(history)-1,0,-1):
        c=int(history[i][a,b]);b,a=a,c;selected.append(c)
    selected=selected[::-1]
    return np.array(ys),np.array([nodes[i][j,0] for i,j in enumerate(selected)]),selected


def extract_trace(mask,bounds,expected_cycles):
    x0,x1,y0,y1=bounds
    # End bands contain reset transitions; keep their exclusion explicit.
    margin=max(4,round((y1-y0)*.06))
    groups={y:[(a,b)for a,b in runs(mask[y])if b-a>=2]
            for y in range(y0+margin,y1-margin)}
    single=sum(len(g)==1 for g in groups.values())/max(1,len(groups))
    ys,x,selected=track_segments(groups,x1-x0,max(1, np.pi*expected_cycles*(x1-x0)/(y1-y0)))
    gaps=np.diff(ys)
    coverage=len(ys)/max(1,len(groups))
    # Nearby fragments of the same thick curve are not an independent branch.
    residual=[]
    for y,c,index in zip(ys,x,selected):
        primary=groups[int(y)][index]
        for j,(a,b)in enumerate(groups[int(y)]):
            distance=max(primary[0]-b,a-primary[1],0)
            if j!=index and distance>max(5,.025*(x1-x0)):
                residual.append((float(y),float((a+b-1)/2)))
    residual_rows=len(set(y for y,_ in residual))/max(1,len(groups))
    return_like=False; unexplained=residual_rows
    if len(residual)>=8:
        points=np.array(residual);rng=np.random.default_rng(0)
        best=np.zeros(len(points),bool)
        for _ in range(120):
            a,b=points[rng.choice(len(points),2,replace=False)]
            if abs(a[0]-b[0])<20:continue
            slope=(b[1]-a[1])/(b[0]-a[0]);intercept=a[1]-slope*a[0]
            near=abs(points[:,1]-(slope*points[:,0]+intercept))<4
            if near.sum()>best.sum():best=near
        return_like=bool(best.mean()>=.8)
        if return_like:
            unexplained=len(set(points[~best,0]))/max(1,len(groups))
    diagnostic=dict(single_row_fraction=single,tracked_row_fraction=coverage,
                    max_missing_row_gap=int(gaps.max()-1)if len(gaps)else 0,
                    residual_branch_row_fraction=residual_rows,
                    residual_is_line_like=return_like,
                    unexplained_branch_row_fraction=unexplained,
                    endpoint_margin_rows=margin)
    return ys,x,diagnostic


def verify(image,scan_hz,expected_cycles,geometry):
    mask,(x0,x1,y0,y1),density=geometry(image)
    ys,x,report=extract_trace(mask,(x0,x1,y0,y1),expected_cycles)
    u=(ys-y0)/(y1-y0)
    best=None
    for cycles in np.linspace(expected_cycles*.85,expected_cycles*1.15,361):
        m=np.column_stack((np.ones(len(u)),np.sin(2*np.pi*cycles*u),np.cos(2*np.pi*cycles*u)))
        keep=np.ones(len(u),bool)
        for _ in range(3):
            if keep.sum()<20:break
            p=np.linalg.lstsq(m[keep],x[keep],rcond=None)[0]
            error=abs(m@p-x)
            keep=error<max(2.5,float(np.quantile(error,.88)))
        rms=float(np.sqrt(np.mean((m[keep]@p-x[keep])**2)))
        # Trimmed points remain explicit; trimming cannot hide extra branches.
        if best is None or rms<best[0]:best=(rms,float(cycles),p,keep,m@p)
    rms,cycles,p,keep,pred=best
    amplitude=float(np.hypot(p[1],p[2]));normalized=rms/max(1,amplitude)
    inliers=abs(pred-x)<max(3,.035*amplitude)
    coverage=float(inliers.sum()/max(1,(y1-y0)-2*report['endpoint_margin_rows']))
    reasons=[]
    if coverage<.80:reasons.append('Insufficient continuous main-curve support')
    if report['max_missing_row_gap']>max(8,.06*(y1-y0)):reasons.append('Long missing section')
    if report['unexplained_branch_row_fraction']>.20:reasons.append('Additional extended non-linear branch')
    if normalized>=.035:reasons.append('Main curve does not fit a sine')
    if amplitude<.35*(x1-x0)or amplitude>.70*(x1-x0):reasons.append('Fitted amplitude inconsistent with trace extent')
    # Full height remains uncalibrated: allow a coarse 5% shape check, never
    # claim adjacent 100-Hz grid discrimination from this tolerance.
    if abs(cycles/expected_cycles-1)>=.05:reasons.append('Cycle count differs from expected by at least 5%')
    report.update(accepted=not reasons,rejection_reasons=reasons,
                  method='continuous main-curve tracking with residual-branch checks',
                  main_curve_inlier_row_fraction=coverage,fitted_cycles=cycles,
                  rms_px=rms,normalized_rms=normalized,conditional_input_hz=cycles*scan_hz,
                  cycle_relative_tolerance=.05,confirmed=False,
                  trace_points=[[int(y),float(c)]for y,c in zip(ys,x)])
    return report


def overlay_trace(image,report):
    overlay=cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
    for y,x in report.get('trace_points',[]):
        cv2.circle(overlay,(round(x),y),1,(0,220,0),-1)
    return overlay
