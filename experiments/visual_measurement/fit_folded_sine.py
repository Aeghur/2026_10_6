"""Continuous sine folded by an ideal sawtooth, no camera integration.

x_k(u)=c+A*sin(2*pi*r*(k+u)+phi), y(u)=y0+H*u.
Phase advances per sweep are constrained by the SAME noninteger ratio r.
Displayed K sweeps are a geometric union, not an exposure intensity model.
"""
import argparse
import json
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
    target=source.copy()
    obs=target>=100
    yy,xx=np.where(obs)
    bounds=[float(xx.min()),float(yy.min()),float(xx.max()),float(yy.max())]
    roi=np.zeros_like(obs);roi[int(bounds[1]):int(bounds[3])+1,int(bounds[0]):int(bounds[2])+1]=True
    def loss(p):
        r,phi,k,width=p
        pred=render(target.shape,bounds,parameters(r,phi,k,width),samples=3000)>=100
        tp=np.count_nonzero(obs&pred);fp=np.count_nonzero(~obs&pred);fn=np.count_nonzero(obs&~pred)
        return (fp+fn)/max(tp+fp+fn,1)
    rng=np.random.default_rng(617)
    candidates=[]
    # Fractional part is finely sampled: small changes move all folded branches.
    for i in range(2500):
        p=np.array([rng.uniform(.5,35),rng.uniform(0,2*np.pi),rng.integers(1,10),rng.uniform(1.,3.)])
        candidates.append((loss(p),p))
    candidates.sort(key=lambda x:x[0])
    starts=[]
    for value,p in candidates:
        if all(abs(p[0]-other[0])>.15 or abs(p[2]-other[2])>1 for _,other in starts):starts.append((value,p))
        if len(starts)==6:break
    fitted=[]
    for value,p in starts:
        for scale in ([.08,.4,1.2,.5],[.025,.15,.8,.25],[.006,.05,.3,.1]):
            for i in range(140):
                q=p+rng.normal(size=4)*scale
                q[0]=np.clip(q[0],.5,35);q[1]%=2*np.pi;q[2]=np.clip(round(q[2]),1,9);q[3]=np.clip(q[3],1.,3.)
                v=loss(q)
                if v<value:value,p=v,q
        fitted.append((value,p.copy()))
    fitted.sort(key=lambda x:x[0])
    _,best=fitted[0]
    full_bounds=bounds
    r,phi,k,width=best
    generated=render(source.shape,full_bounds,parameters(r,phi,k,width),samples=6000)
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
    cv2.imwrite(str(args.output/'generated.png'),generated)
    cv2.imwrite(str(args.output/'comparison.jpg'),np.hstack(panels))
    report={'model':'continuous sine folded into K sawtooth periods; no exposure integration',
            'ratio_candidate':float(r),'sweep_count':int(round(k)),
            'phase_rad':float(phi),'phase_advance_deg_per_sweep':float((r%1)*360),
            'line_width_px':float(width),'iou':tp/max(tp+fp+fn,1),
            'observed_foreground_explained':tp/max(tp+fn,1),
            'predicted_foreground_precision':tp/max(tp+fp,1),
            'measurement_valid':False,
            'assumptions':['Observed vertical extent represents one COMPLETE ramp.',
                           'Linear ramp; instantaneous reset; fixed frequency and amplitude.',
                           'K displayed sweeps are a geometric union; display history is not timed.',
                           'Raster/search approximation and local minima remain; no unique frequency claim.'],
            'candidates':[{'loss':float(v),'ratio':float(p[0]),'sweeps':int(round(p[2]))}for v,p in fitted]}
    (args.output/'result.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
