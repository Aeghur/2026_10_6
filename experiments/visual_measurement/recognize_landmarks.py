"""Offline geometric candidate recognition from extrema and skeleton junctions.

No generated sine images, learned weights, or prior fit reports are read.
Assumes a complete, near-uniform rational phase family; reports alternatives.
"""
import argparse
import json
import time
from pathlib import Path
import cv2
import numpy as np


def thin(mask):
    a=np.pad(mask.astype(np.uint8),1)
    for _ in range(150):
        changed=False
        for second in (False,True):
            p=[a[:-2,1:-1],a[:-2,2:],a[1:-1,2:],a[2:,2:],a[2:,1:-1],a[2:,:-2],a[1:-1,:-2],a[:-2,:-2]]
            n=sum(p); transitions=sum(((p[i]==0)&(p[(i+1)%8]==1)).astype(np.uint8) for i in range(8))
            if second: gates=(p[0]*p[2]*p[6]==0)&(p[0]*p[4]*p[6]==0)
            else:gates=(p[0]*p[2]*p[4]==0)&(p[2]*p[4]*p[6]==0)
            remove=(a[1:-1,1:-1]==1)&(n>=2)&(n<=6)&(transitions==1)&gates
            if remove.any():a[1:-1,1:-1][remove]=0;changed=True
        if not changed:break
    return a[1:-1,1:-1]


def runs(active):
    edges=np.diff(np.r_[False,active,False].astype(np.int8))
    return [(int(a),int(b))for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1))]


def edge_events(mask,left=True):
    yy,xx=np.where(mask)
    lo,hi=int(xx.min()),int(xx.max())
    # Narrow interior strips catch turns without merging neighboring branches.
    sl=slice(lo+2,lo+7) if left else slice(hi-6,hi-1)
    profile=mask[:,sl].mean(axis=1)
    groups=runs(profile>=.35)
    return [float((a+b-1)/2) for a,b in groups if b-a>=1],profile


def junctions(skeleton):
    a=np.pad(skeleton,1)
    p=[a[:-2,1:-1],a[:-2,2:],a[1:-1,2:],a[2:,2:],a[2:,1:-1],a[2:,:-2],a[1:-1,:-2],a[:-2,:-2]]
    transitions=sum(((p[i]==0)&(p[(i+1)%8]==1)).astype(np.uint8)for i in range(8))
    raw=((skeleton>0)&(transitions>=3)).astype(np.uint8)
    merged=cv2.dilate(raw,np.ones((3,3),np.uint8))
    count,labels,stats,_=cv2.connectedComponentsWithStats(merged)
    points=[]
    for label in range(1,count):
        ys,xs=np.where((labels==label)&(raw>0))
        if len(xs):points.append([float(xs.mean()),float(ys.mean())])
    return np.array(points).reshape(-1,2)


def enclosed_gaps(mask):
    ys,xs=np.where(mask); span=float(xs.max()-xs.min())
    count,labels,stats,centers=cv2.connectedComponentsWithStats((~mask).astype(np.uint8))
    holes=[]
    for (x,y,w,h,area),(cx,cy) in zip(stats[1:],centers[1:]):
        if (x>0 and y>0 and x+w<mask.shape[1] and y+h<mask.shape[0]
                and w>.08*span and h>=2 and area>=20 and w/h>2):
            holes.append([float(cx),float(cy),int(area)])
    groups=[]
    for hole in sorted(holes):
        if not groups or hole[0]-np.mean([a[0] for a in groups[-1]])>.12*span:groups.append([hole])
        else:groups[-1].append(hole)
    columns=[]
    for group in groups:
        if len(group)<3:continue
        ordered=sorted(group,key=lambda a:a[1])
        gaps=np.diff([a[1] for a in ordered])
        median=float(np.median(gaps))
        columns.append({'x':float(np.mean([a[0] for a in ordered])),
                        'count':len(group),'spacing_px':median,
                        'spacing_mad_relative':float(np.median(abs(gaps-median))/median),
                        'centers':[[a[0],a[1]] for a in ordered]})
    return columns


def recognize(image,saw_hz,full_height_px=None):
    mask=image>=100
    yy,xx=np.where(mask)
    if len(xx)<100:raise ValueError('Insufficient foreground')
    x0,x1,y0,y1=map(int,(xx.min(),xx.max(),yy.min(),yy.max()))
    center=(x0+x1)/2;amplitude=(x1-x0)/2
    skeleton=thin(mask)
    nodes=junctions(skeleton)
    if len(nodes):nodes=nodes[(nodes[:,0]>x0+.12*(x1-x0))&(nodes[:,0]<x1-.12*(x1-x0))&(nodes[:,1]>y0+5)&(nodes[:,1]<y1-5)]
    left,lp=edge_events(mask,True);right,rp=edge_events(mask,False)
    all_gaps=np.r_[np.diff(left),np.diff(right)]
    all_gaps=all_gaps[all_gaps>=4]
    if len(all_gaps)<5:raise ValueError('Too few separated extrema')
    gap=float(np.median(all_gaps))
    good=all_gaps[(all_gaps>.65*gap)&(all_gaps<1.35*gap)]
    gap=float(np.median(good))
    relative_mad=float(np.median(abs(good-gap))/gap)
    # Cluster intersection columns by 1D histogram with modest smoothing.
    hist=np.zeros(image.shape[1],float)
    for x,y in nodes:hist[int(round(x))]+=1
    hist=np.convolve(hist,np.array([1,2,3,2,1])/9,mode='same')
    peaks=[]
    for x in np.argsort(hist)[::-1]:
        if hist[x]<max(1.2,.22*hist.max()):break
        if all(abs(int(x)-j)>10 for j in peaks):peaks.append(int(x))
    peaks.sort()
    normalized=np.array([(x-center)/amplitude for x in peaks])
    gap_columns=enclosed_gaps(mask)
    extrema_counts_balanced=(min(len(left),len(right))>=3 and
        abs(len(left)-len(right))<=max(2,.2*max(len(left),len(right))))
    gap_topology_usable=extrema_counts_balanced and bool(gap_columns) and all(
        abs(c['spacing_px']/gap-1)<.15 and c['spacing_mad_relative']<.15 for c in gap_columns)
    candidates=[]
    for q in range(2,9):
        expected=np.cos(np.pi*np.arange(1,q)/q)
        if len(normalized):
            dist=np.abs(normalized[:,None]-expected[None,:])
            geometric_error=float(.5*(dist.min(axis=1).mean()+dist.min(axis=0).mean()))
        else:geometric_error=1.
        if gap_topology_usable:
            structural_score=.15*abs(len(gap_columns)-(q-1))+.05*geometric_error
        else:
            structural_score=geometric_error+.025*abs(len(peaks)-(q-1))
        row={'phase_family_size_candidate':q,'intersection_column_error':geometric_error,
             'structural_score':structural_score,'expected_normalized_columns':expected.tolist(),
             'frequency_per_full_height_hz_per_px':saw_hz/(q*gap)}
        if full_height_px is not None:row['frequency_hz']=saw_hz*full_height_px/(q*gap)
        else:
            row['frequency_hz_if_visible_height_is_full']=saw_hz*(y1-y0)/(q*gap)
        candidates.append(row)
    candidates.sort(key=lambda d:d['structural_score'])
    report={'method':'extrema spacing + skeleton intersection columns; no waveform image fitting',
            'source_shape':list(image.shape),'saw_hz':saw_hz,'full_ramp_height_px':full_height_px,
            'visible_bounds':[x0,y0,x1,y1], 'left_extrema_y':left,'right_extrema_y':right,
            'same_edge_spacing_px':gap,'spacing_relative_mad':relative_mad,
            'gap_columns':gap_columns,'gap_topology_usable':gap_topology_usable,
            'extrema_counts_balanced':extrema_counts_balanced,
            'quality_rejection_reasons':[] if extrema_counts_balanced else ['Left/right extrema counts are too few or strongly imbalanced; merged/missing edge events.'],
            'phase_family_count_from_gaps':len(gap_columns)+1 if gap_topology_usable else None,
            'skeleton_crossing_count_is_reliable':False,
            'junction_count':len(nodes),'intersection_columns_px':peaks,
            'normalized_intersection_columns':normalized.tolist(),
            'candidates':candidates,'confirmed':False,
            'assumptions':['Ideal folded sine; approximately uniformly spaced complete phase family.',
                           'Skeleton junctions may include thick-line contacts and missing crossings.',
                           'Family size is a hypothesis, not an established number of exposures.',
                           'Spacing may alias through missing/merged extrema.',
                           'q-1 periodic enclosed-gap columns imply q only under a complete regular phase family.',
                           'Skeleton crossings are diagnostic only when thick-line contacts distort them.',
                           'Second probe and independent ramp-height calibration required for confirmation.']}
    overlay=cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
    for y in left:cv2.circle(overlay,(x0+4,int(round(y))),3,(0,255,255),1)
    for y in right:cv2.circle(overlay,(x1-4,int(round(y))),3,(0,255,255),1)
    for group in gap_columns:
        for x,y in group['centers']:cv2.circle(overlay,(int(round(x)),int(round(y))),2,(0,220,0),1)
    return report,overlay,skeleton*255,hist


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('image',type=Path)
    ap.add_argument('--saw-hz',type=float,required=True)
    ap.add_argument('--full-height-px',type=float)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.saw_hz<=0 or (args.full_height_px is not None and args.full_height_px<=0):ap.error('positive dimensions required')
    image=cv2.imread(str(args.image),0)
    if image is None:raise ValueError('Missing image')
    started=time.perf_counter()
    try:
        report,overlay,skeleton,hist=recognize(image,args.saw_hz,args.full_height_px)
    except ValueError:
        print("RECOGNITION_SECONDS_ON_REJECTION",time.perf_counter()-started,flush=True)
        raise
    report["recognition_seconds"]=time.perf_counter()-started
    report['input']=str(args.image.resolve())
    args.output.mkdir(parents=True,exist_ok=True)
    cv2.imwrite(str(args.output/'landmarks.png'),overlay)
    cv2.imwrite(str(args.output/'skeleton.png'),skeleton)
    (args.output/'result.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
