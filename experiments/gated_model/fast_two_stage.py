"""Single-session adaptive experiment; original full strategy stays available."""
import time
PROCESS_STARTED=time.perf_counter()
import argparse,json,sys
from pathlib import Path
from datetime import datetime,timezone
from dataclasses import asdict
import cv2
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hardware_test import normalize
from infer import load_ranker,predict
from gated_frequency import Scan,local_scores
from sine_refinement import candidates,score_image,choose
from two_stage import finish_output
from vision_calibrator import Camera
from strategy_control import StrategyImageAdapter
from msp_wave_link import MSPM0WaveLink
import torch
IMPORT_SECONDS=time.perf_counter()-PROCESS_STARTED


def shortlist(rows):
    valid=[r for r in rows if r['frames'] and 'metrics' in r['frames'][0]]
    return sorted(valid,key=lambda r:r['frames'][0]['metrics']['score'])[:2]


def main(*,request_started=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware',required=True,action='store_true')
    parser.add_argument('--serve',action='store_true',help='Keep dependencies loaded; enter run for each fresh measurement, quit to exit')
    args=parser.parse_args()
    torch.set_num_threads(2)
    out=ROOT/'artifacts/diagnostics'/('fast_two_stage_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True)
    report=dict(method='Adaptive single-session gated model plus sine shortlist confirmation',
                ground_truth_used=False,confirmed=False,frames=[],candidates=[],timing_seconds={})
    report['timing_seconds']['imports']=IMPORT_SECONDS
    def save():
        (out/'summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    p=json.loads((ROOT/'experiments/zero_slope/current_capture_profile.json').read_text())
    cfg=json.loads((ROOT/'config.json').read_text())
    for k in ['full_grid_corners','grid_dimensions_div','manual_exposure','manual_gain']:cfg[k]=p[k]
    camera=None
    active_started=time.perf_counter()
    try:
        model,c,cp=load_ranker(ROOT/'gated_ramp_training_v1/fixed_10ms_v1/best.pt')
        camera=Camera(cfg);adapter=StrategyImageAdapter(cfg)
        def capture(stem):
            start=time.perf_counter()
            raw,ts=camera.frame_timed();_,warp=adapter.convert(raw)
            hsv=cv2.cvtColor(warp,cv2.COLOR_BGR2HSV)
            mask=cv2.inRange(hsv,np.array([0,0,p['hsv_value_min']]),np.array([179,p['hsv_saturation_max'],255]))
            cv2.imwrite(str(out/f'{stem}_raw.jpg'),raw)
            cv2.imwrite(str(out/f'{stem}_corrected.png'),warp)
            cv2.imwrite(str(out/f'{stem}_mask.png'),mask)
            return mask,dict(stem=stem,estimated_capture_monotonic=ts,elapsed_seconds=time.perf_counter()-start)
        with MSPM0WaveLink(cfg['serial_port']) as link:
            try:
                coarse_started=time.perf_counter()
                for active in [50,250,2000]:
                    ack=link.set_decay_pulse(active,0,8)
                    if ack.frequency_millihz!=100000 or ack.decay_microseconds!=active:
                        raise ValueError('Pulse acknowledgement mismatch')
                    # Existing frame_timed drains four buffered frames after ACK.
                    # Keep that freshness protection; remove additional .4 s wait.
                    mask,row=capture(f'coarse_{active}us');row['ack']=asdict(ack)
                    report['frames'].append(row)
                    try:
                        normalized,endpoints=normalize(mask,p['full_ramp_height_px'])
                        cv2.imwrite(str(out/f"{row['stem']}_384.png"),normalized)
                        image=Image.fromarray(normalized);scan=Scan(active,0.,0)
                        scores,quality=local_scores(image,scan,c.grid)
                        row.update(local_score=float(scores.min()),valid_row_fraction=float(quality[0]),endpoint_mapping=endpoints)
                        # Stricter than the full runner's <1 scout cutoff.
                        if quality[0]<.9 or scores.min()>=.02:
                            row['rejection']='No sufficiently clean single trace';continue
                        result=predict(model,c,[(image,scan)])
                        row['model_result']=result
                        report['center_hz']=result['model_hz'];break
                    except ValueError as error:
                        row['rejection']=str(error)
                    finally:save()
                if 'center_hz' not in report:raise ValueError('No clean preliminary scan; use full strategy')
                report['timing_seconds']['coarse']=time.perf_counter()-coarse_started
                print('COARSE',report['center_hz'],flush=True)
                fine_started=time.perf_counter()
                def sample(row):
                    f=row['frequency_hz'];ack=link.set_waveform('sine',f,0,8)
                    if ack.frequency_millihz!=f*1000:raise ValueError('Sine acknowledgement mismatch')
                    mask,frame=capture(f"sine_{f}Hz_{len(row['frames'])}")
                    frame['ack']=asdict(ack)
                    try:frame['metrics']=score_image(mask)
                    except ValueError as error:frame['rejection']=str(error)
                    row['frames'].append(frame);save()
                for f in candidates(report['center_hz']):
                    row=dict(frequency_hz=f,frames=[]);report['candidates'].append(row);sample(row)
                selected=shortlist(report['candidates'])
                report['shortlist_hz']=[r['frequency_hz'] for r in selected]
                # Each shortlisted candidate gets three actual captures; no
                # duplication of the initial image to fabricate confirmation.
                for row in selected:
                    for _ in range(2):sample(row)
                report['refinement']=choose(selected)
                report['refinement']['selection_scope']='Top two of five single-frame scouts; three frames each for confirmation'
                report['timing_seconds']['refinement']=time.perf_counter()-fine_started
            finally:
                waveform,ack=finish_output(link,report.get('refinement',{}))
                report.update(final_waveform=waveform,final_ack=asdict(ack))
    except Exception as error:
        report['error']=f'{type(error).__name__}: {error}';raise
    finally:
        if camera is not None:camera.close()
        report['timing_seconds']['after_imports']=time.perf_counter()-active_started
        if request_started is None:
            report['timing_seconds']['process_total']=time.perf_counter()-PROCESS_STARTED
        else:
            report['timing_seconds']['request_total']=time.perf_counter()-request_started
            report['timing_seconds']['startup_imports_excluded']=True
        save();print('ARTIFACTS',out,flush=True)
        print('TIMING',json.dumps(report['timing_seconds']),flush=True)
        print('RESULT',json.dumps(report.get('refinement',{})),flush=True)

if __name__=='__main__':
    if '--serve' in sys.argv:
        if '--hardware' not in sys.argv:
            raise SystemExit('--hardware is required')
        print('READY: enter run for a fresh measurement; quit to exit',flush=True)
        for command in sys.stdin:
            command=command.strip()
            if command=='quit':break
            if command!='run':
                print('Expected run or quit',flush=True);continue
            request_started=time.perf_counter()
            try:main(request_started=request_started)
            except Exception as error:print('ERROR',repr(error),flush=True)
            print('READY: enter run for a fresh measurement; quit to exit',flush=True)
    else:main()
