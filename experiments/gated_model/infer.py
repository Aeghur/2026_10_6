"""Frozen inference for the supplied gated-ramp candidate ranker.

No training, camera or UART access. Scan timing and calibrated bitmaps are
supplied in a JSON observation manifest rather than guessed from screenshots.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
from PIL import Image
import torch

ROOT=Path(__file__).resolve().parents[2]
PACKAGE=ROOT/'gated_ramp_training_v1'
sys.path.insert(0,str(PACKAGE))
from gated_frequency import VERSION,Config,Ranker,Scan,extract,baseline,profile


def load_ranker(checkpoint):
    cp=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if cp.get('version')!=VERSION:raise ValueError('Checkpoint version mismatch')
    config=Config(**cp['config']).validate()
    model=Ranker();model.load_state_dict(cp['model']);model.eval()
    return model,config,cp


def predict(model,config,observations,top_k=5):
    if not observations:raise ValueError('No observations')
    quality=[]
    for image,scan in observations:
        scan.validate()
        _,_,q=profile(image)
        quality.append(dict(active_us=scan.active_us,delay_us=scan.delay_us,
                            frame_index=scan.frame_index,valid_row_fraction=float(q[0]),
                            foreground_row_density=float(q[1]),profile_std=float(q[2])))
    if any(q['valid_row_fraction']<.9 for q in quality):
        raise ValueError('Some scans lack 90% valid rows; verify full-ramp endpoint mapping and extraction')
    start=time.perf_counter();features=extract(observations,config.grid,config.clock_ppm)
    extraction=time.perf_counter()-start
    candidate_mask=features[:,4]>.5
    if not candidate_mask.any():raise ValueError('No retained candidates')
    start=time.perf_counter()
    with torch.inference_mode():scores=model(torch.from_numpy(features)[None])[0].cpu().numpy()
    rank_time=time.perf_counter()-start
    ids=np.flatnonzero(candidate_mask)
    ids=ids[np.argsort(-scores[ids])[:top_k]]
    physical=baseline(features)
    return dict(model_hz=int(config.grid[ids[0]]),baseline_hz=int(config.grid[np.argmin(physical)]),
                top_candidates=[dict(frequency_hz=int(config.grid[i]),rank_score=float(scores[i]),
                                     local_fit_score=float(features[i,0]),coherent_fit_score=float(features[i,1]),
                                     fitted_amplitude=float(features[i,2]),physical_score=float(physical[i]))for i in ids],
                retained_candidate_count=int(candidate_mask.sum()),quality=quality,
                feature_extraction_seconds=extraction,ranker_seconds=rank_time,
                confirmed=False,confidence_is_calibrated=False,
                note='Scores are rankings, not calibrated success probabilities. Timing and geometry must match training.')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--observations',type=Path,required=True)
    ap.add_argument('--checkpoint',type=Path,default=PACKAGE/'fixed_10ms_v1/best.pt')
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();torch.set_num_threads(2)
    start=time.perf_counter();model,config,cp=load_ranker(args.checkpoint);load_seconds=time.perf_counter()-start
    manifest=json.loads(args.observations.read_text(encoding='utf-8-sig'))
    if manifest.get('repeat_us')!=config.repeat_us:raise ValueError('Manifest must declare matching repeat_us=10000')
    observations=[]
    for row in manifest['observations']:
        p=Path(row['image'])
        if not p.is_absolute():p=args.observations.parent/p
        with Image.open(p)as image:image=image.convert('L').copy()
        observations.append((image,Scan(row['active_us'],row['delay_us'],row['frame_index'])))
    if len(observations)!=3:raise ValueError('Supply the three fine scans selected after coarse measurement')
    report=predict(model,config,observations)
    report.update(checkpoint=str(args.checkpoint.resolve()),checkpoint_epoch=cp['epoch'],
                  model_load_seconds=load_seconds,timing_epoch_verified=bool(manifest.get('timing_epoch_verified',False)),
                  endpoint_mapping=manifest.get('endpoint_mapping'),
                  trained_exposure_ms=config.exposure_ms,actual_exposure_ms=manifest.get('exposure_ms'),
                  exposure_matches_training=manifest.get('exposure_ms')==config.exposure_ms)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
