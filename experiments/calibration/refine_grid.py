"""Refine current grid geometry from many interior blue grid lines.

Input is a saved camera frame. No camera, UART or model frequency is used.
"""
import argparse,json,sys
from pathlib import Path
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from strategy_control import StrategyImageAdapter


def line_fit(points):
    points=np.asarray(points,float)
    if len(points)<50:raise ValueError('Insufficient grid support')
    keep=np.ones(len(points),bool)
    for _ in range(5):
        a,b=np.polyfit(points[keep,0],points[keep,1],1)
        residual=points[:,1]-(a*points[:,0]+b)
        med=np.median(residual[keep]);mad=np.median(abs(residual[keep]-med))
        keep=abs(residual-med)<max(.65,3*1.4826*mad)
    return float(a),float(b),dict(points=int(keep.sum()),rms_px=float(np.sqrt(np.mean(residual[keep]**2))))


def grid_lines(image):
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    b,g,r=cv2.split(image.astype(float))
    eligible=(hsv[:,:,0]>=87)&(hsv[:,:,0]<=114)&(hsv[:,:,1]>=40)&(hsv[:,:,2]>=65)&(hsv[:,:,2]<=240)
    weight=np.maximum(b-r-12,0)*eligible
    vertical=[];horizontal=[]
    for x in range(80,960,80):
        pts=[]
        for y in range(25,615):
            if min(y%80,80-y%80)<9:continue
            coords=np.arange(x-7,x+8);values=weight[y,coords]
            if values.max()<12 or values.sum()<25:continue
            # Use the brightest local grid ridge rather than all nearby glow.
            selected=values>=values.max()*.55
            point=float(np.average(coords[selected],weights=values[selected]))
            pts.append((y,point))
        a,b,q=line_fit(pts);vertical.append(dict(target=x,a=a,b=b,**q))
    for y in range(80,640,80):
        pts=[]
        for x in range(25,935):
            if min(x%80,80-x%80)<9:continue
            coords=np.arange(y-7,y+8);values=weight[coords,x]
            if values.max()<12 or values.sum()<25:continue
            selected=values>=values.max()*.55
            point=float(np.average(coords[selected],weights=values[selected]))
            pts.append((x,point))
        a,b,q=line_fit(pts);horizontal.append(dict(target=y,a=a,b=b,**q))
    return vertical,horizontal


def metrics(v,h):
    return dict(max_vertical_drift_px=float(max(abs(row['a'])*640 for row in v)),
                max_horizontal_drift_px=float(max(abs(row['a'])*960 for row in h)),
                median_line_fit_rms_px=float(np.median([row['rms_px']for row in v+h])),
                vertical_intercepts_at_midheight=[row['a']*320+row['b']for row in v],
                horizontal_intercepts_at_midwidth=[row['a']*480+row['b']for row in h])


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--image',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--profile',type=Path,default=ROOT/'experiments/zero_slope/current_capture_profile.json')
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    profile=json.loads(args.profile.read_text());cfg=json.loads((ROOT/'config.json').read_text());cfg.update(full_grid_corners=profile['full_grid_corners'],grid_dimensions_div=profile['grid_dimensions_div'])
    adapter=StrategyImageAdapter(cfg);raw=cv2.imread(str(args.image))
    if raw is None:raise ValueError('Cannot load camera image')
    lens=adapter.lens.apply(raw);old=cv2.warpPerspective(lens,adapter.transform,(960,640))
    v,h=grid_lines(old);observed=[];ideal=[]
    for x in v:
        for y in h:
            px=(x['a']*y['b']+x['b'])/(1-x['a']*y['a']);py=y['a']*px+y['b']
            observed.append((px,py));ideal.append((x['target'],y['target']))
    observed=np.array(observed,float);ideal=np.array(ideal,float)
    # Alternate intersections are held out to check interpolation rather than
    # merely report the points used by the homography solver.
    split=np.arange(len(observed))%2==0
    checkH,_=cv2.findHomography(observed[split],ideal[split],0)
    held=cv2.perspectiveTransform(observed[~split].astype(np.float64)[None],checkH)[0]-ideal[~split]
    correction,_=cv2.findHomography(observed,ideal,0)
    combined=correction@adapter.transform;combined/=combined[2,2]
    preview=cv2.warpPerspective(lens,combined,(960,640))
    nv,nh=grid_lines(preview)
    target=np.float64([[[0,0],[959,0],[959,639],[0,639]]])
    corners=cv2.perspectiveTransform(target,np.linalg.inv(combined))[0]
    candidate=dict(profile);candidate['full_grid_corners']=corners.tolist()
    candidate['calibration_source_image']=str(args.image.resolve())
    candidate['calibration_method']='77 intersections from 11 vertical and 7 horizontal interior blue grid lines; fitted homography'
    report=dict(old_corners=profile['full_grid_corners'],new_corners=corners.tolist(),
                before=metrics(v,h),after=metrics(nv,nh),
                held_out_intersection_rms_px=float(np.sqrt(np.mean(np.sum(held**2,axis=1)))),
                fitted_intersection_count=len(observed),old_vertical=v,old_horizontal=h,
                new_vertical=nv,new_horizontal=nh,
                source_to_rectified_homography=combined.tolist())
    cv2.imwrite(str(args.output/'before.png'),old);cv2.imwrite(str(args.output/'after.png'),preview)
    diagnostic=old.copy()
    for x,y in observed:cv2.circle(diagnostic,(round(x),round(y)),4,(0,0,255),1)
    cv2.imwrite(str(args.output/'grid_intersections.png'),diagnostic)
    (args.output/'profile_candidate.json').write_text(json.dumps(candidate,indent=2,ensure_ascii=False))
    (args.output/'calibration_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps({k:report[k]for k in ['before','after','held_out_intersection_rms_px','fitted_intersection_count','new_corners']},indent=2))

if __name__=='__main__':main()
