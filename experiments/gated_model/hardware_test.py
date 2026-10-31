"""8-ms real-camera validation of frozen gated ranker, one frame at a time.

The current UART has no hardware superframe index. Each image therefore uses
its own arbitrary t=0 (absorbed into its fitted sine phase); no coherent
cross-image result is claimed. No source ground truth is accepted as input.
"""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import sys
import time
import cv2
import numpy as np
from PIL import Image
import torch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from infer import load_ranker,predict
from gated_frequency import BANK,Scan,local_scores,profile as image_profile
from vision_calibrator import Camera
from strategy_control import StrategyImageAdapter
from msp_wave_link import MSPM0WaveLink


def normalize(mask,full_height):
    if not np.isfinite(full_height)or full_height<=0:raise ValueError('Full ramp height must be calibrated')
    if mask.shape!=(640,960):raise ValueError('Expected calibrated 960x640 mask')
    counts=(mask>=100)[:,80:880].sum(axis=1).astype(float)
    counts[-15:]=0
    maximum=counts.max()
    if maximum<300:raise ValueError('No broad dwell line for endpoint calibration')
    selected=counts>=maximum*.60
    edges=np.diff(np.r_[False,selected,False].astype(np.int8))
    groups=[(a,b)for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1))if b-a>=2]
    if not groups:raise ValueError('No multi-row dwell band; isolated frame line is not an endpoint')
    a,b=max(groups,key=lambda v:counts[v[0]:v[1]].sum())
    rows=np.arange(a,b);top=float(np.average(rows,weights=counts[a:b]))
    bottom=top+full_height
    # The current falling pulse has its dwell at the upper screen endpoint;
    # the active curve returns from bottom to top, matching model time order.
    source=mask.copy()
    transform=np.float32([[383/800,0,-80*383/800],[0,307/full_height,38-top*307/full_height]])
    result=cv2.warpAffine(source,transform,(384,384),flags=cv2.INTER_LINEAR)
    result[:38]=0;result[346:]=0
    endpoint=dict(dwell_y_full_resolution=top,other_endpoint_y_full_resolution=bottom,
                  calibrated_height_px=full_height,model_top_y=38,model_bottom_y=345,
                  direction_assumption='Pulse dwell is top; active decay proceeds bottom to top')
    return result,endpoint


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--full-ramp-height-px',type=float)
    args=ap.parse_args()
    torch.set_num_threads(2)
    out=ROOT/'artifacts/diagnostics'/('gated_real_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'));out.mkdir(parents=True)
    report=dict(test='Independent-frame validation of frozen gated-ramp ranker',
                confirmed=False,coherent_multiframe_tested=False,hardware_epoch_available=False,
                ground_truth_used_for_acquisition=False,frames=[],exposure_ms=8,
                caveats=['Each frame has its own arbitrary phase origin, not the three-frame training protocol.',
                         'Vertical scale uses the current capture profile; empirical effective height may differ from nominal Vpp.',
                         'Pulse decay direction is adapted by endpoint mapping.'])
    camera=None
    def save(): (out/'summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    try:
        p=json.loads((ROOT/'experiments/zero_slope/current_capture_profile.json').read_text())
        if args.full_ramp_height_px is not None:p['full_ramp_height_px']=args.full_ramp_height_px
        report['full_ramp_height_px']=p['full_ramp_height_px']
        cfg=json.loads((ROOT/'config.json').read_text())
        for k in ['full_grid_corners','grid_dimensions_div','manual_exposure','manual_gain']:cfg[k]=p[k]
        start=time.perf_counter();model,c,cp=load_ranker(ROOT/'gated_ramp_training_v1/fixed_10ms_v1/best.pt')
        report.update(model_load_seconds=time.perf_counter()-start,checkpoint_epoch=cp['epoch'],trained_exposure_ms=c.exposure_ms,
                      height_calibration=(f'Explicit test height {args.full_ramp_height_px:g} px'if args.full_ramp_height_px is not None else p['full_ramp_height_note']))
        camera=Camera(cfg);adapter=StrategyImageAdapter(cfg)
        with MSPM0WaveLink(cfg['serial_port'])as link:
            def capture(stage,active,delay,index):
                phase=round(delay/10000*360,2)
                start=time.perf_counter()
                ack=link.set_decay_pulse(int(active),phase,8)
                if ack.frequency_millihz!=100000 or ack.phase_centidegrees!=round(phase*100):raise ValueError('Pulse ACK differs from request')
                time.sleep(.4)
                rows=[]
                for attempt in range(2):
                    stem=f'{stage}_{index}_{active:g}us_{delay:g}delay_{attempt}'
                    raw,ts=camera.frame_timed();_,warp=adapter.convert(raw)
                    hsv=cv2.cvtColor(warp,cv2.COLOR_BGR2HSV)
                    mask=cv2.inRange(hsv,np.array([0,0,p['hsv_value_min']]),np.array([179,p['hsv_saturation_max'],255]))
                    cv2.imwrite(str(out/f'{stem}_raw.jpg'),raw);cv2.imwrite(str(out/f'{stem}_corrected.png'),warp);cv2.imwrite(str(out/f'{stem}_mask.png'),mask)
                    row=dict(stage=stage,stem=stem,requested_active_us=active,requested_delay_us=delay,
                             requested_phase_degrees=phase,ack=asdict(ack),estimated_capture_monotonic=ts,
                             capture_and_preprocess_seconds=time.perf_counter()-start)
                    report['frames'].append(row)
                    try:
                        normalized,endpoints=normalize(mask,p['full_ramp_height_px'])
                        cv2.imwrite(str(out/f'{stem}_384.png'),normalized)
                        image=Image.fromarray(normalized)
                        quality=image_profile(image)[2]
                        row.update(endpoint_mapping=endpoints,valid_row_fraction=float(quality[0]),profile_std=float(quality[2]))
                        # A single-image phase origin is arbitrary and fitted;
                        # zero here is not a fabricated hardware frame index.
                        scan=Scan(float(ack.decay_microseconds),0.,0)
                        scores,q=local_scores(image,scan,c.grid)
                        row['local_best_hz']=int(c.grid[scores.argmin()]);row['local_best_score']=float(scores.min())
                        row['scout_usable']=bool(quality[0]>=.9 and scores.min()<1.)
                        if stage=='fine'and row['scout_usable']:
                            row['model_result']=predict(model,c,[(image,scan)])
                            row['inference_mode']='single image; phase origin free, no interframe timing assumption'
                        rows.append((row,image,scan,scores))
                    except ValueError as error:
                        row['rejection']=str(error)
                    save();print(json.dumps({k:v for k,v in row.items()if k!='model_result'}),flush=True)
                    if row.get('scout_usable'):break
                return rows
            coarse=[]
            try:
                for index,active in enumerate([50,250,2000]):
                    items=capture('coarse',active,0,index)
                    coarse.extend(item for item in items if item[0].get('scout_usable'))
                if not coarse:
                    report['result']='No coarse image passed profile quality';save();return
                scout=np.min([item[3]for item in coarse],axis=0)
                coarse_hz=int(c.grid[scout.argmin()])
                active=min(BANK,key=lambda t:abs(math.log(max(1e-6,coarse_hz*t*1e-6/4))))
                report.update(coarse_hz=coarse_hz,selected_active_us=active)
                for index,delay in enumerate([0,1700,4300]):capture('fine',active,delay,index)
                valid=[r['model_result']for r in report['frames']if'model_result'in r]
                report['model_predictions_hz']=[r['model_hz']for r in valid]
                report['baseline_predictions_hz']=[r['baseline_hz']for r in valid]
                report['result']='Independent-frame predictions recorded'if valid else'No fine image passed profile quality'
                if valid:
                    values,counts=np.unique(report['model_predictions_hz'],return_counts=True)
                    order=np.argsort(-counts)
                    report['candidate_vote_counts']=[dict(frequency_hz=int(values[i]),count=int(counts[i]))for i in order]
                    report['all_model_predictions_agree']=bool(len(values)==1)
            finally:
                # Restore the previously established inner 10kHz pulse setting.
                report['restored_ack']=asdict(link.set_decay_pulse(100,0,8));save()
    except Exception as error:
        report['error']=f'{type(error).__name__}: {error}';raise
    finally:
        if camera is not None:camera.close()
        save();print('ARTIFACTS',out,flush=True)

if __name__=='__main__':main()
