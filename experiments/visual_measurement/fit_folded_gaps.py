"""Continuous sine folded by an ideal sawtooth, no camera integration.

x_k(u)=c+A*sin(2*pi*r*(k+u)+phi), y(u)=y0+H*u.
Phase advances per sweep are constrained by the SAME noninteger ratio r.
Displayed K sweeps are a geometric union, not an exposure intensity model.
"""
import argparse
import json
import time
from pathlib import Path
import cv2
import numpy as np
from generate_overlap import render


def parameters(r,phi,k,width):
    return [r,phi,2*np.pi*(r%1),int(round(k)),width]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('image',type=Path)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    source=cv2.imread(str(args.image),0)
    if source is None:raise ValueError('Missing image')
    started=time.perf_counter()
    target=source.copy()
    obs=target>=100
    yy,xx=np.where(obs)
    bounds=[float(xx.min()),float(yy.min()),float(xx.max()),float(yy.max())]
    roi=np.zeros_like(obs);roi[int(bounds[1]):int(bounds[3])+1,int(bounds[0]):int(bounds[2])+1]=True
    def loss(p):
        r,phi,k,width,q=p
        pred=render(target.shape,bounds,[r*q,phi,2*np.pi*(r%1),int(round(k)),width],samples=2500)>=100
        tp=np.count_nonzero(obs&pred);fp=np.count_nonzero(~obs&pred);fn=np.count_nonzero(obs&~pred)
        foreground_error=fn/max(np.count_nonzero(obs),1)
        gap_error=fp/max(np.count_nonzero(roi&~obs),1)
        # Compare y profiles independently near the two turning edges.
        x0,y0,x1,y1=map(int,bounds)
        profile_error=0.
        for lo,hi in ((x0,x0+22),(x1-21,x1+1)):
            a=obs[y0:y1+1,lo:hi].mean(axis=1)
            b=pred[y0:y1+1,lo:hi].mean(axis=1)
            a=np.convolve(a,[.25,.5,.25],mode='same')
            b=np.convolve(b,[.25,.5,.25],mode='same')
            profile_error+=float(np.mean(np.abs(a-b)))/2
        return .35*foreground_error+.45*gap_error+.20*profile_error+.003*(k-1)
    rng=np.random.default_rng(617)
    candidates=[]
    # Keep a separate starting point for every branch count.
    starts=[]
    for k in range(1,9):
        local=[]
        for i in range(200):
            p=np.array([rng.uniform(.5,35),rng.uniform(0,2*np.pi),k,rng.uniform(1.,4.),rng.uniform(.9,1.)])
            local.append((loss(p),p))
        starts.append(min(local,key=lambda x:x[0]))
    print('Coarse search complete',flush=True)
    fitted=[]
    for value,p in starts:
        for scale in ([.15,.4,0,.7,.015],[.04,.15,0,.3,.005],[.008,.05,0,.1,.001]):
            for i in range(100):
                q=p+rng.normal(size=5)*scale
                q[0]=np.clip(q[0],.5,35);q[1]%=2*np.pi;q[2]=np.clip(round(q[2]),1,9);q[3]=np.clip(q[3],1.,4.);q[4]=np.clip(q[4],.9,1.)
                v=loss(q)
                if v<value:value,p=v,q
        fitted.append((value,p.copy()))
        print("Refined K",int(p[2]),"loss",round(value,4),flush=True)
    fitted.sort(key=lambda x:x[0])
    _,best=fitted[0]
    full_bounds=bounds
    r,phi,k,width,q=best
    generated=render(source.shape,full_bounds,[r*q,phi,2*np.pi*(r%1),int(round(k)),width],samples=6000)
    truth=source>=100;pred=generated>=100
    tp=int((truth&pred).sum());fp=int((~truth&pred).sum());fn=int((truth&~pred).sum())
    overlay=np.zeros((*source.shape,3),np.uint8)
    overlay[truth&pred]=(220,220,220);overlay[pred&~truth]=(0,220,0);overlay[truth&~pred]=(0,0,220)
    args.output.mkdir(parents=True,exist_ok=True)
    panels=[]
    for title,im in [('Observed',source),('Folded continuous sine',generated),('Difference',overlay)]:
        if im.ndim==2:im=cv2.cvtColor(im,cv2.COLOR_GRAY2BGR)
        panel=cv2.copyMakeBorder(im,30,0,0,0,cv2.BORDER_CONSTANT)
        cv2.putText(panel,title,(8,21),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
        panels.append(panel)
    recognition_seconds=time.perf_counter()-started
    cv2.imwrite(str(args.output/'generated.png'),generated)
    cv2.imwrite(str(args.output/'comparison.jpg'),np.hstack(panels))
    report={'recognition_seconds':recognition_seconds,'model':'continuous sine folded into K sawtooth periods; no exposure integration',
            'ratio_candidate':float(r),'visible_ramp_fraction':float(q),'visible_cycles':float(r*q),'sweep_count':int(round(k)),
            'phase_rad':float(phi),'phase_advance_deg_per_sweep':float((r%1)*360),
            'line_width_px':float(width),'iou':tp/max(tp+fp+fn,1),
            'observed_foreground_explained':tp/max(tp+fn,1),
            'predicted_foreground_precision':tp/max(tp+fp,1),
            'measurement_valid':False,
            'empty_region_preserved':1-fp/max(np.count_nonzero(roi&~obs),1),
            'fit_objective':'balanced missing foreground, false foreground in gaps, edge profiles, branch penalty',
            'assumptions':['Observed vertical extent is 90..100 percent of complete ramp; fitted, not calibrated.',
                           'Linear ramp; instantaneous reset; fixed frequency and amplitude.',
                           'K displayed sweeps are a geometric union; display history is not timed.',
                           'Raster/search approximation and local minima remain; no unique frequency claim.'],
            'candidates':[{'loss':float(v),'ratio':float(p[0]),'sweeps':int(round(p[2]))}for v,p in fitted]}
    (args.output/'result.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
