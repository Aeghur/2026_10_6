"""Gated-model preliminary estimate followed by +/-200Hz sine refinement."""
import argparse,json,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from dataclasses import asdict
import cv2
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from sine_refinement import candidates,score_image,choose
from vision_calibrator import Camera
from strategy_control import StrategyImageAdapter
from msp_wave_link import MSPM0WaveLink


def finish_output(link,refinement):
    frequency=refinement.get('frequency_hz')
    if refinement.get('status')=='frequency_candidate' and frequency is not None:
        return 'sine',link.set_waveform('sine',frequency,0,8)
    return 'decay_pulse',link.set_decay_pulse(100,0,8)


def main():
    started=time.perf_counter()
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--hardware',action='store_true',required=True)
    ap.add_argument('--coarse-summary',type=Path,help='Explicit reuse of a saved coarse test, only if source unchanged')
    args=ap.parse_args()
    out=ROOT/'artifacts/diagnostics'/('two_stage_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'));out.mkdir(parents=True)
    report=dict(method='gated coarse plus five sine candidates',phase_speed_not_a_rejection_criterion=True,
                ground_truth_used=False,candidates=[],confirmed=False,timing_seconds={})
    def save():(out/'summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    coarse_started=time.perf_counter()
    if args.coarse_summary:
        coarse_file=args.coarse_summary
    else:
        with (out/'coarse.log').open('w')as log:
            subprocess.run([sys.executable,str(ROOT/'experiments/gated_model/hardware_test.py')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        lines=(out/'coarse.log').read_text().splitlines()
        location=next((v.removeprefix('ARTIFACTS ')for v in reversed(lines)if v.startswith('ARTIFACTS ')),None)
        if not location:raise ValueError('Coarse stage did not report artifacts')
        coarse_file=Path(location)/'summary.json'
    coarse=json.loads(coarse_file.read_text())
    values=coarse.get('model_predictions_hz',[])
    if not values:raise ValueError('No usable coarse frequency predictions')
    center=int(np.median(values))
    report.update(coarse_summary=str(coarse_file.resolve()),coarse_predictions_hz=values,
                  center_hz=center,scan_candidates_hz=candidates(center))
    report['timing_seconds']['coarse']=time.perf_counter()-coarse_started
    save();print('COARSE',center,report['scan_candidates_hz'],flush=True)
    p=json.loads((ROOT/'experiments/zero_slope/current_capture_profile.json').read_text())
    cfg=json.loads((ROOT/'config.json').read_text())
    for k in ['full_grid_corners','grid_dimensions_div','manual_exposure','manual_gain']:cfg[k]=p[k]
    camera=None
    fine_started=time.perf_counter()
    try:
        camera=Camera(cfg);adapter=StrategyImageAdapter(cfg)
        with MSPM0WaveLink(cfg['serial_port'])as link:
            try:
                for index,f in enumerate(report['scan_candidates_hz']):
                    candidate_started=time.perf_counter()
                    ack=link.set_waveform('sine',f,0,8)
                    if ack.frequency_millihz!=f*1000:raise ValueError('Sine ACK frequency mismatch')
                    row=dict(frequency_hz=f,ack=asdict(ack),frames=[]);report['candidates'].append(row)
                    time.sleep(.5)
                    for sample in range(3):
                        frame_started=time.perf_counter()
                        stem=f'{index:02d}_{f}Hz_{sample}'
                        raw,ts=camera.frame_timed();_,warp=adapter.convert(raw)
                        hsv=cv2.cvtColor(warp,cv2.COLOR_BGR2HSV)
                        mask=cv2.inRange(hsv,np.array([0,0,p['hsv_value_min']]),np.array([179,p['hsv_saturation_max'],255]))
                        cv2.imwrite(str(out/f'{stem}_raw.jpg'),raw);cv2.imwrite(str(out/f'{stem}_corrected.png'),warp);cv2.imwrite(str(out/f'{stem}_mask.png'),mask)
                        frame=dict(stem=stem,estimated_capture_monotonic=ts)
                        try:frame['metrics']=score_image(mask)
                        except ValueError as error:frame['rejection']=str(error)
                        frame['elapsed_seconds']=time.perf_counter()-frame_started
                        row['frames'].append(frame);save()
                    row['elapsed_seconds']=time.perf_counter()-candidate_started
                    print(json.dumps(row),flush=True)
                report['refinement']=choose(report['candidates']);save()
                print('REFINEMENT',json.dumps(report['refinement']),flush=True)
            finally:
                waveform,ack=finish_output(link,report.get('refinement',{}))
                report['final_waveform']=waveform;report['final_ack']=asdict(ack);save()
    finally:
        if camera is not None:camera.close()
        report['timing_seconds']['refinement']=time.perf_counter()-fine_started
        report['timing_seconds']['main_total']=time.perf_counter()-started
        save();print('ARTIFACTS',out,flush=True)

if __name__=='__main__':main()
