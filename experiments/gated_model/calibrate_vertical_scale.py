"""Empirical vertical scale calibration from a declared reference frequency.

Assumes active scan duration is correct. No voltage measurement or UART access.
Reference and holdout image replays are reported separately.
"""
import argparse,json,sys,time
from datetime import datetime,timezone
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
import torch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/gated_model'))
from infer import load_ranker,predict
from hardware_test import normalize
from diagnose_error import estimate
from gated_frequency import Scan,profile as image_profile,FRACTIONS,fit


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply',action='store_true')
    args=ap.parse_args();torch.set_num_threads(2)
    out=ROOT/'artifacts/diagnostics'/('vertical_scale_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'));out.mkdir(parents=True)
    cfg_path=ROOT/'experiments/zero_slope/current_capture_profile.json'
    original=json.loads(cfg_path.read_text())
    reference=ROOT/'artifacts/diagnostics/gated_real_20261031_091809'
    reference_truth=66600
    old_report=json.loads((reference/'summary.json').read_text())
    old_height=float(old_report['full_ramp_height_px'])
    fine=[row for row in old_report['frames']if'model_result'in row]
    reference_estimates=[]
    for row in fine:
        image=Image.open(reference/(row['stem']+'_384.png')).convert('L')
        value=estimate(image,row['ack']['decay_microseconds'],row['model_result']['model_hz'])['frequency_hz']
        reference_estimates.append(value)
    center=float(np.median(reference_estimates))
    factor=reference_truth/center
    analytic_height=old_height*factor
    # Calibrate through the actual resampling/profile path, not only the
    # analytic scale formula. Fit one shared height, with independent nuisance
    # sine phase/amplitude per reference image. The model is never trained.
    masks=[cv2.imread(str(reference/(row['stem']+'_mask.png')),0)for row in fine]
    search=[]
    for candidate in np.arange(analytic_height-2,analytic_height+2.001,.05):
        errors=[]
        for mask,row in zip(masks,fine):
            normalized,_=normalize(mask,float(candidate))
            values,weights,quality=image_profile(Image.fromarray(normalized))
            if quality[0]<.9:errors.append(10.);continue
            error,_=fit([reference_truth],FRACTIONS*row['ack']['decay_microseconds']*1e-6,values,weights)
            errors.append(float(error[0]))
        search.append(dict(height_px=float(candidate),mean_reference_mse=float(np.mean(errors))))
    best=min(search,key=lambda v:v['mean_reference_mse'])
    height=best['height_px']
    factor=height/old_height
    equivalent_vpp=height/80*original['y_volts_per_division']
    updated=dict(original)
    updated['full_ramp_height_px']=height
    updated['nominal_ramp_span_volts']=4.
    updated['effective_ramp_span_volts']=equivalent_vpp
    updated['full_ramp_height_note']=f'Empirical calibration from declared 66600 Hz reference, assuming scan durations accurate: {old_height:g} px × {factor:.10f} = {height:.6f} px. Equivalent {equivalent_vpp:.6f} Vpp is not a voltage measurement; hardware remains nominal 4 Vpp.'
    updated['vertical_scale_calibration']=dict(reference_frequency_hz=reference_truth,
        source_case=str(reference.resolve()),reference_fit_frequencies_hz=reference_estimates,
        median_reference_fit_hz=center,original_height_px=old_height,correction_factor=factor,
        analytic_initial_height_px=analytic_height,
        selection_method='Minimize mean known-frequency fit error across three reference phases through actual preprocessing',
        scan_duration_assumed_correct=True,independent_voltage_measurement=False,
        camera_or_y_scale_changes_require_recalibration=True)
    (out/'profile_before.json').write_text(json.dumps(original,indent=2,ensure_ascii=False))
    (out/'profile_after.json').write_text(json.dumps(updated,indent=2,ensure_ascii=False))
    model,c,cp=load_ranker(ROOT/'gated_ramp_training_v1/fixed_10ms_v1/best.pt')
    report=dict(assumption='Scan duration correct; adjust vertical spatial scale only',
                reference_truth_hz=reference_truth,reference_fit_frequencies_hz=reference_estimates,
                correction_factor=factor,old_height_px=old_height,new_height_px=height,
                nominal_hardware_vpp=4.,estimated_equivalent_vpp=equivalent_vpp,
                voltage_was_measured=False,model_was_retrained=False,hardware_changed=False,
                profile_applied=False,analytic_initial_height_px=analytic_height,reference_height_search=search,replays=[])
    for name,truth,role in [('gated_real_20261031_091809',66600,'calibration_reference'),
                            ('gated_real_20261031_090956',2000,'held_out_frequency'),
                            ('gated_real_20261031_091243',5600,'held_out_frequency'),
                            ('gated_real_20261031_091455',None,'unknown_truth')]:
        case=ROOT/'artifacts/diagnostics'/name
        rows=json.loads((case/'summary.json').read_text())['frames']
        for row in rows:
            if'model_result'not in row:continue
            normalized,endpoints=normalize(cv2.imread(str(case/(row['stem']+'_mask.png')),0),height)
            cv2.imwrite(str(out/(name+'_'+row['stem']+'_384.png')),normalized)
            predicted=predict(model,c,[(Image.fromarray(normalized),Scan(row['ack']['decay_microseconds'],0,0))])
            record=dict(source_case=name,stem=row['stem'],role=role,truth_hz=truth,
                        before_hz=row['model_result']['model_hz'],after_hz=predicted['model_hz'],
                        baseline_after_hz=predicted['baseline_hz'],endpoint_mapping=endpoints,
                        top_candidates=predicted['top_candidates'])
            if truth is not None:record['correct']=record['after_hz']==truth
            report['replays'].append(record)
            print(json.dumps({k:v for k,v in record.items()if k not in ['top_candidates','endpoint_mapping']}),flush=True)
    # A reference-specific correction must also be checked at other frequencies.
    held=[r for r in report['replays']if r['role']=='held_out_frequency']
    report['held_out_frequency_accuracy']=sum(r['correct']for r in held)/len(held)
    if args.apply:
        if report['held_out_frequency_accuracy']<1:
            raise ValueError('Holdout frequencies did not all pass; inspect candidate profile before applying')
        cfg_path.write_text(json.dumps(updated,indent=2,ensure_ascii=False)+'\n')
        report['profile_applied']=True
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print('CALIBRATION',json.dumps({k:report[k]for k in ['correction_factor','new_height_px','estimated_equivalent_vpp','profile_applied','held_out_frequency_accuracy']}),flush=True)
    print('ARTIFACTS',out,flush=True)

if __name__=='__main__':main()
