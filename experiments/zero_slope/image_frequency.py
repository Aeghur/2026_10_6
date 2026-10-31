"""Measure sine zero-crossing slopes from a processed pulse-scan image.

Only local trace centres are fitted. No bank of generated waveform images.
Absolute Hz requires the full (possibly off-screen) linear ramp height.
"""
import cv2
import numpy as np


def _runs(active):
    edges=np.diff(np.r_[False,active,False].astype(np.int8))
    return list(zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)))


def measure_processed(image,internal_saw_hz,full_ramp_height_px=None,grid_hz=100):
    if image is None or image.ndim!=2:
        raise ValueError('Expected a grayscale processed image')
    if not np.isfinite(internal_saw_hz)or internal_saw_hz<=0:
        raise ValueError('Positive internal saw frequency required')
    if full_ramp_height_px is not None and (not np.isfinite(full_ramp_height_px)or full_ramp_height_px<=0):
        raise ValueError('Positive independently established full-ramp height required')
    if grid_hz<=0:raise ValueError('Positive frequency grid required')
    mask=image>=100
    yy,xx=np.where(mask)
    if len(xx)<100:raise ValueError('Insufficient foreground')
    # Robust amplitude estimate from interior rows, excluding endpoint/UI bands.
    ylo,yhi=np.quantile(yy,[.01,.99])
    interior=mask.copy();interior[:max(0,int(ylo)+3)]=False;interior[min(mask.shape[0],int(yhi)-2):]=False
    _,body_x=np.where(interior)
    if len(body_x)<100:raise ValueError('Insufficient interior trace')
    xlo,xhi=np.quantile(body_x,[.002,.998]);amplitude=(xhi-xlo)/2;center=(xlo+xhi)/2
    if amplitude<20:raise ValueError('Signal too narrow')
    # A long dwell at zero voltage is a horizontal band, not a sine crossing.
    row_counts=mask[:,int(xlo):int(xhi)+1].sum(axis=1)
    dwell=row_counts>max(20,.45*(xhi-xlo))
    dwell=cv2.dilate(dwell.astype(np.uint8)[:,None],np.ones((5,1),np.uint8)).ravel().astype(bool)
    work=mask.copy();work[dwell]=False
    work[:max(0,int(ylo))]=False;work[min(mask.shape[0],int(yhi)+1):]=False
    half=int(amplitude*.60)
    left=max(0,int(np.ceil(center-half)));right=min(mask.shape[1]-1,int(np.floor(center+half)))
    columns={}
    for x in range(left,right+1):
        columns[x]=[(a+b-1)/2 for a,b in _runs(work[:,x])if b-a<=max(8,image.shape[0]*.025)]
    anchor_profile=np.zeros(mask.shape[0],bool)
    for x in range(max(left,round(center)-3),min(right,round(center)+3)+1):
        for y in columns[x]:anchor_profile[round(y)]=True
    raw_anchors=[float((a+b-1)/2)for a,b in _runs(anchor_profile)]
    # Broken bright dots from one crossing can form several nearby runs.
    # Merge those before estimating inter-crossing spacing; otherwise the
    # neighbourhood shrinks to a few pixels and rejects a clear curve.
    groups=[]
    for value in raw_anchors:
        if not groups or value-groups[-1][-1]>max(4,image.shape[0]*.015):
            groups.append([value])
        else:groups[-1].append(value)
    anchors=[float(np.median(group))for group in groups]
    if not anchors:raise ValueError('No locally resolved zero crossings')
    spacing=float(np.median(np.diff(anchors)))if len(anchors)>1 else image.shape[0]/3
    radius=max(8,min(image.shape[0]*.12,spacing*.43))
    data=[]
    for x,values in columns.items():
        z=amplitude*np.arcsin(np.clip((x-center)/amplitude,-.95,.95))
        for y in values:data.append((x,z,y))
    points=np.array(data,dtype=float)
    fits=[];rejected=[]
    for anchor in anchors:
        local=points[abs(points[:,2]-anchor)<radius]
        outer=local[abs(local[:,1])>amplitude*.20]
        if len(outer)<12:continue
        ratios=(outer[:,2]-anchor)/outer[:,1]
        for sign in (-1,1):
            eligible=ratios[ratios*sign>.003]
            if len(eligible)<8:continue
            b=float(np.median(eligible));a=anchor
            selected=None
            for _ in range(5):
                selected=[]
                for x in np.unique(local[:,0]):
                    rows=local[local[:,0]==x]
                    distance=abs(rows[:,2]-(a+b*rows[:,1]))
                    j=int(np.argmin(distance))
                    if distance[j]<2.2:selected.append(rows[j])
                if len(selected)<12:break
                selected=np.array(selected)
                b,a=np.polyfit(selected[:,1],selected[:,2],1)
            if selected is None or len(selected)<max(12,.25*(right-left)):
                continue
            selected=np.array(selected)
            if np.ptp(selected[:,0])<.75*(right-left)or abs(a-anchor)>3 or b*sign<=0:
                continue
            residual=float(np.sqrt(np.mean((selected[:,2]-a-b*selected[:,1])**2)))
            vertical_span=abs(b)*np.ptp(selected[:,1])
            if residual>1.5 or vertical_span<3:
                rejected.append(dict(crossing_y=anchor,reason='Insufficient local slope resolution'))
                continue
            # Resolution floor plus fit scatter. Scale calibration is separate.
            relative=max(.015,1.0/vertical_span,2*residual/max(1,vertical_span))
            coefficient=internal_saw_hz/(2*np.pi*amplitude*abs(b))
            fits.append(dict(crossing_y=float(a),dy_dx=float(b),dx_dy=float(1/b),
                             supporting_columns=len(selected),residual_px=residual,
                             vertical_span_px=float(vertical_span),relative_uncertainty=float(relative),
                             frequency_per_full_ramp_height_hz_per_px=float(coefficient)))
    if not fits:raise ValueError('No zero-crossing slope passed local quality checks')
    # Deduplicate directions at the same crossing; equal crossing weight avoids
    # a thick bright branch dominating through many line proposals.
    unique=[]
    for fit in sorted(fits,key=lambda v:v['residual_px']):
        if not any(abs(fit['crossing_y']-v['crossing_y'])<3 and np.sign(fit['dy_dx'])==np.sign(v['dy_dx'])for v in unique):unique.append(fit)
    fits=sorted(unique,key=lambda v:v['crossing_y'])
    coefficients=np.array([v['frequency_per_full_ramp_height_hz_per_px']for v in fits])
    median=float(np.median(coefficients));mad=float(np.median(abs(coefficients-median))/median)
    tolerance=max(.06,3*mad)
    keep=abs(coefficients/median-1)<=tolerance
    good=coefficients[keep]
    coefficient=float(np.median(good))
    relative=max(float(np.median([v['relative_uncertainty']for v,k in zip(fits,keep)if k])),3*mad)
    independent=len(set(round(v['crossing_y']/4)for v,k in zip(fits,keep)if k))
    for fit,k in zip(fits,keep):fit['agrees_with_consensus']=bool(k)
    reasons=[]
    if independent<2:reasons.append('Fewer than two independent zero crossings')
    if mad>.12:reasons.append('Local slope estimates disagree')
    report=dict(method='local zero-crossing slopes; no generated sine-image matching',
                internal_saw_hz=float(internal_saw_hz),effective_ramp_us=1e6/internal_saw_hz,
                source_shape=list(image.shape),signal_center_x=float(center),signal_amplitude_px=float(amplitude),
                observed_y_extent=[float(ylo),float(yhi)],full_ramp_height_px=full_ramp_height_px,
                height_is_inferred_from_visible_extent=False,zero_crossings_y=anchors,
                ignored_dwell_rows=np.flatnonzero(dwell).tolist(),local_measurements=fits,
                accepted_direction_count=int(keep.sum()),independent_crossing_count=independent,
                coefficient_relative_mad=mad,relative_uncertainty=relative,
                frequency_per_full_ramp_height_hz_per_px=coefficient,
                local_rejections=rejected,quality_rejection_reasons=reasons,
                frequency_hz=None,grid_candidate_hz=None,grid_candidates_hz=[],confirmed=False)
    if full_ramp_height_px is None:
        report['status']='needs_full_ramp_height'
    elif reasons:
        report['status']='insufficient_consistent_slopes'
    else:
        f=coefficient*full_ramp_height_px
        lo,hi=f*(1-relative),f*(1+relative)
        candidates=list(range(max(grid_hz,int(np.ceil(lo/grid_hz))*grid_hz),int(np.floor(hi/grid_hz))*grid_hz+1,grid_hz))
        report.update(status='frequency_candidate',frequency_hz=float(f),
                      grid_candidate_hz=int(np.floor(f/grid_hz+.5))*grid_hz,
                      grid_candidates_hz=sorted(candidates,key=lambda v:abs(v-f)),
                      frequency_interval_hz=[float(lo),float(hi)])
    overlay=cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
    cv2.line(overlay,(round(center),0),(round(center),image.shape[0]-1),(0,200,200),1)
    for item in fits:
        a,b=item['crossing_y'],item['dy_dx']
        xs=np.arange(left,right+1)
        ys=a+b*amplitude*np.arcsin((xs-center)/amplitude)
        points=np.rint(np.column_stack((xs,ys))).astype(np.int32)
        cv2.polylines(overlay,[points],False,(0,220,0)if item['agrees_with_consensus']else(0,0,255),1)
    return report,overlay
