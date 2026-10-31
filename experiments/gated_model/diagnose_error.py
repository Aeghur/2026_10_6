"""Post-hoc analysis of saved images; never updates model/calibration/hardware."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import sys,json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
import torch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/gated_model'))
from infer import load_ranker
from hardware_test import normalize
from gated_frequency import profile,FRACTIONS,fit,extract,Scan


def estimate(image,active,center,keep=None):
    x,w,q=profile(image)
    if keep is not None:w=w*keep
    grid=np.arange(max(1000,center-2500),min(100000,center+2500)+1,5.)
    error,amplitude=fit(grid,FRACTIONS*active*1e-6,x,w)
    i=int(np.argmin(error))
    return dict(frequency_hz=float(grid[i]),mse=float(error[i]),amplitude=float(amplitude[i]))


def main():
    torch.set_num_threads(2)
    out=ROOT/'artifacts/diagnostics/gated_error_analysis_66600';out.mkdir(parents=True,exist_ok=True)
    model,c,cp=load_ranker(ROOT/'gated_ramp_training_v1/fixed_10ms_v1/best.pt')
    cases=[('gated_real_20261031_090956',2000),('gated_real_20261031_091243',5600),('gated_real_20261031_091809',66600)]
    report=dict(posthoc_analysis=True,production_parameters_changed=False,frames=[],threshold_sensitivity=[])
    for name,truth in cases:
        p=ROOT/'artifacts/diagnostics'/name;r=json.loads((p/'summary.json').read_text())
        for row in r['frames']:
            if 'model_result'not in row:continue
            image=Image.open(p/(row['stem']+'_384.png')).convert('L')
            active=row['ack']['decay_microseconds'];pred=row['model_result']['model_hz']
            continuous=estimate(image,active,pred)
            x,w,q=profile(image)
            truth_error,truth_amp=fit([truth],FRACTIONS*active*1e-6,x,w)
            features=extract([(image,Scan(active,0,0))],c.grid,c.clock_ppm)
            idx=int(np.flatnonzero(c.grid==truth)[0]);physical=features[:,1]+.15*features[:,0]
            with torch.inference_mode():scores=model(torch.from_numpy(features)[None])[0].numpy()
            retention=bool(features[idx,4]>.5)
            record=dict(case=name,stem=row['stem'],truth_hz=truth,active_us=active,
                        model_hz=pred,baseline_hz=row['model_result']['baseline_hz'],
                        continuous_fit=continuous,
                        ratio_to_truth=continuous['frequency_hz']/truth,
                        truth_mse=float(truth_error[0]),truth_amplitude=float(truth_amp[0]),
                        truth_candidate_retained=retention,
                        truth_model_rank=int((scores>scores[idx]).sum()+1)if retention else None,
                        retained_candidate_min_hz=int(c.grid[features[:,4]>.5].min()),
                        retained_candidate_max_hz=int(c.grid[features[:,4]>.5].max()))
            if truth==66600:
                record['vertical_subsets']={}
                for label,lo,hi in [('top60',0,.6),('middle60',.2,.8),('bottom60',.4,1.)]:
                    mask=(np.linspace(0,1,len(w))>=lo)&(np.linspace(0,1,len(w))<=hi)
                    record['vertical_subsets'][label]=estimate(image,active,pred,mask)
                raw=cv2.imread(str(p/(row['stem']+'_corrected.png')));hsv=cv2.cvtColor(raw,cv2.COLOR_BGR2HSV)
                for v in [210,225,240,250]:
                    binary=cv2.inRange(hsv,np.array([0,0,v]),np.array([179,120,255]))
                    normalized,endpoints=normalize(binary,640)
                    alt=estimate(Image.fromarray(normalized),active,pred)
                    report['threshold_sensitivity'].append(dict(stem=row['stem'],value_min=v,
                                                               **alt,endpoint_top_y=endpoints['dwell_y_full_resolution']))
            report['frames'].append(record)
            print(json.dumps(record),flush=True)
    latest=[r for r in report['frames']if r['truth_hz']==66600]
    ratio=float(np.median([r['ratio_to_truth']for r in latest]))
    report['latest_scale_equivalences']=dict(median_frequency_ratio=ratio,
        percentage_bias=(ratio-1)*100,
        required_actual_scan_us_if_height_exact=50*ratio,
        required_actual_height_px_if_scan_time_exact=640/ratio,
        required_vpp_if_scale_and_scan_time_exact=4/ratio,
        note='Equivalent explanations of the same spatial/time scale error, not independently measured hardware parameters.')
    report['per_signal_summary']={str(truth):dict(continuous_mean_hz=float(np.mean([r['continuous_fit']['frequency_hz']for r in report['frames']if r['truth_hz']==truth])),
        ratios=[r['ratio_to_truth']for r in report['frames']if r['truth_hz']==truth])for _,truth in cases}
    (out/'analysis.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print('EQUIVALENCES',report['latest_scale_equivalences'],flush=True)
    print('THRESHOLDS',report['threshold_sensitivity'],flush=True)
    print('ARTIFACTS',out,flush=True)

if __name__=='__main__':main()
