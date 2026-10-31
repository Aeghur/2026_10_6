"""Image-to-frequency entry; hardware mode only captures, never sends UART."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from image_frequency import measure_processed


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    source=ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--image',type=Path,help='Already processed grayscale image')
    source.add_argument('--hardware',action='store_true',help='Capture only; preserve current waveform')
    ap.add_argument('--profile',type=Path,default=Path(__file__).with_name('current_capture_profile.json'))
    ap.add_argument('--internal-saw-hz',type=float,help='Inner linear scan frequency, not pulse repetition frequency')
    height=ap.add_mutually_exclusive_group()
    height.add_argument('--full-ramp-height-px',type=float,help='Independently known full ramp height in the input image pixels')
    height.add_argument('--full-ramp-divisions',type=float,help='Full linear ramp height in scope divisions')
    ap.add_argument('--pixels-per-div',type=float,help='For images other than calibrated 960x640 or 384x384')
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    profile=json.loads(args.profile.read_text())
    internal=args.internal_saw_hz if args.internal_saw_hz is not None else profile['internal_saw_hz']
    if not np.isfinite(internal)or internal<=0:ap.error('Internal scan frequency must be positive')
    if args.full_ramp_divisions is not None and (not np.isfinite(args.full_ramp_divisions)or args.full_ramp_divisions<=0):ap.error('Full ramp divisions must be positive')
    if args.pixels_per_div is not None and (not np.isfinite(args.pixels_per_div)or args.pixels_per_div<=0):ap.error('Pixels per division must be positive')
    out=args.output or ROOT/'artifacts/diagnostics'/('slope_frequency_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter()
    if args.hardware:
        from vision_calibrator import Camera
        from strategy_control import StrategyImageAdapter
        cfg=json.loads((ROOT/'config.json').read_text())
        for key in ['full_grid_corners','grid_dimensions_div','manual_exposure','manual_gain']:cfg[key]=profile[key]
        if [cfg['frame_width'],cfg['frame_height']]!=profile['reference_frame_size']:ap.error('Camera frame size differs from calibration profile')
        camera=None
        try:
            camera=Camera(cfg);raw,ts=camera.frame_timed();adapter=StrategyImageAdapter(cfg)
            _,corrected=adapter.convert(raw)
            hsv=cv2.cvtColor(corrected,cv2.COLOR_BGR2HSV)
            image=cv2.inRange(hsv,np.array([0,0,profile['hsv_value_min']]),np.array([179,profile['hsv_saturation_max'],255]))
            cv2.imwrite(str(out/'raw.jpg'),raw);cv2.imwrite(str(out/'corrected.png'),corrected)
            (out/'capture.json').write_text(json.dumps(dict(configuration=cfg,profile=profile,serial_commands_sent=False,estimated_monotonic_timestamp=ts),indent=2))
        finally:
            if camera is not None:camera.close()
    else:
        image=cv2.imread(str(args.image),cv2.IMREAD_GRAYSCALE)
        if image is None:ap.error('Cannot read input image')
    cv2.imwrite(str(out/'processed.png'),image)
    h=args.full_ramp_height_px
    if h is None and args.full_ramp_divisions is not None:
        ppd=args.pixels_per_div
        if ppd is None:
            ppd={(640,960):80.,(384,384):38.4}.get(image.shape)
        if ppd is None:ap.error('Explicit --pixels-per-div required for this input shape')
        h=args.full_ramp_divisions*ppd
    if h is None and args.hardware:
        h=profile.get('full_ramp_height_px')
    try:
        report,overlay=measure_processed(image,internal,h)
        cv2.imwrite(str(out/'slopes.png'),overlay)
    except ValueError as error:
        report=dict(status='measurement_rejected',reason=str(error),frequency_hz=None,confirmed=False)
    report.update(internal_saw_hz=internal,effective_ramp_us=1e6/internal,
                  scan_time_basis='User-specified inner saw period; must equal the full linear ramp duration',
                  elapsed_seconds=time.perf_counter()-start,input=str(args.image.resolve())if args.image else'live camera',
                  serial_commands_sent=False)
    (out/'result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    summary={k:v for k,v in report.items()if k not in ['local_measurements','ignored_dwell_rows','grid_candidates_hz']}
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    print('ARTIFACTS',out.resolve(),flush=True)

if __name__=='__main__':main()
