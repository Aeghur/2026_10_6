"""Single-sweep raster fit with bounded width; offline, no hardware access."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from generate_overlap import render


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('image',type=Path)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    source=cv2.imread(str(args.image),0)
    if source is None: raise ValueError('Input image missing')
    obs=source>=100
    yy,xx=np.where(obs)
    bounds=np.array([xx.min(),yy.min(),xx.max(),yy.max()],float)
    # Work at original resolution so thin crossings and gaps remain available.
    rng=np.random.default_rng(937)
    roi=np.zeros_like(obs);roi[int(bounds[1]):int(bounds[3])+1,int(bounds[0]):int(bounds[2])+1]=True
    area=roi.sum()
    best_score=-1.;best=None;leaders=[]
    def score(p):
        prediction=render(source.shape,bounds,p,1800)>=100
        tp=np.count_nonzero(obs&prediction)
        fp=np.count_nonzero(~obs&prediction)
        fn=np.count_nonzero(obs&~prediction)
        # Dice penalizes missed foreground; specificity exposes lines in gaps.
        dice=2*tp/max(2*tp+fp+fn,1)
        specificity=1-fp/max(np.count_nonzero(roi&~obs),1)
        return .7*dice+.3*specificity
    # Keep independent starts, not one local basin; explicit harmonic search.
    for cycles in np.arange(.5,35.001,.125):
        for phase in np.arange(0,2*np.pi,2*np.pi/16):
            p=np.array([cycles,phase,0,1,2.])
            value=score(p)
            leaders.append((value,p))
    leaders=sorted(leaders,key=lambda z:z[0],reverse=True)
    starts=[]
    for value,p in leaders:
        if all(abs(p[0]-q[0])>.2 for _,q in starts): starts.append((value,p))
        if len(starts)==12:break
    fitted=[]
    for value,p in starts:
        for spread in ((.15,.3,0,0,0),(.05,.1,0,0,0),(.015,.04,0,0,0)):
            for i in range(100):
                trial=p+rng.normal(size=5)*spread
                trial[0]=np.clip(trial[0],.5,35)
                trial[1]%=2*np.pi
                result=score(trial)
                if result>value:value,p=result,trial
        # Thickness held to a small physically explicit range, not a free fill.
        for width in (1.,2.,3.):
            trial=p.copy();trial[4]=width
            result=score(trial)
            fitted.append((result,trial.copy()))
    fitted.sort(key=lambda z:z[0],reverse=True)
    best_score,best=fitted[0]
    generated=render(source.shape,bounds,best,6000)
    pred=generated>=100
    tp=int(np.count_nonzero(obs&pred));fp=int(np.count_nonzero(~obs&pred));fn=int(np.count_nonzero(obs&~pred))
    overlay=np.zeros((*source.shape,3),np.uint8)
    overlay[obs&pred]=(220,220,220);overlay[pred&~obs]=(0,220,0);overlay[obs&~pred]=(0,0,220)
    args.output.mkdir(parents=True,exist_ok=True)
    cv2.imwrite(str(args.output/'generated.png'),generated)
    cv2.imwrite(str(args.output/'difference.png'),overlay)
    panels=[]
    for label,im in [('Observed',source),('Single sine',generated),('Difference',overlay)]:
        if im.ndim==2:im=cv2.cvtColor(im,cv2.COLOR_GRAY2BGR)
        panel=cv2.copyMakeBorder(im,30,0,0,0,cv2.BORDER_CONSTANT)
        cv2.putText(panel,label,(10,21),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
        panels.append(panel)
    cv2.imwrite(str(args.output/'comparison.jpg'),np.hstack(panels))
    report={'hypothesis':'one sine sweep, no exposure union','source':str(args.image.resolve()),
            'visible_cycles':float(best[0]),'phase_rad':float(best[1]),'width_px':float(best[4]),
            'bounds_px':bounds.tolist(),'iou':tp/max(tp+fp+fn,1),
            'observed_foreground_explained':tp/max(tp+fn,1),
            'predicted_foreground_precision':tp/max(tp+fp,1),
            'empty_region_preserved':1-fp/max(np.count_nonzero(roi&~obs),1),
            'measurement_valid':False,
            'candidate_minima':[{'score':float(v),'cycles':float(p[0]),'phase_rad':float(p[1]),'width_px':float(p[4])}for v,p in fitted[:12]],
            'notes':['Image-space fit; cycles do not imply Hz without calibrated ramp mapping.',
                     'Global fixed sine, amplitude and center are bounded by observed extent.',
                     'Uniform line widths 1..3 px only; no added sweeps or filled envelope.',
                     'Search raster and final high-sample rendering differ slightly; metrics use final render.']}
    (args.output/'result.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('candidate_minima','notes')},indent=2))


if __name__=='__main__':main()
