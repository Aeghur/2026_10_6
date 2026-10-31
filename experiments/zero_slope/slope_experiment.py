"""Independent zero-crossing slope experiment; never changes the old strategy."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'experiments/visual_measurement'))
from recognize_landmarks import runs


def geometry(image, allow_dense=False):
    if image is None or image.ndim != 2:
        raise ValueError('Expected a grayscale image')
    mask = image >= 100
    yy, xx = np.where(mask)
    if len(xx) < 150:
        raise ValueError('Insufficient trace')
    y0, y1 = int(yy.min()), int(yy.max())
    if y1-y0 < 50:
        raise ValueError('Insufficient signal span')
    # Endpoint reset lines must not determine the sine amplitude or centre.
    margin = max(5, round((y1-y0)*.08))
    _, body_x = np.where(mask[y0+margin:y1-margin])
    if len(body_x) < 100:
        raise ValueError('Insufficient interior trace support')
    x0 = int(np.floor(np.quantile(body_x,.002)))
    x1 = int(np.ceil(np.quantile(body_x,.998)))
    if x1-x0 < 50:
        raise ValueError('Insufficient interior signal span')
    density = float(mask[y0:y1+1,x0:x1+1].mean())
    if not allow_dense and density > .62:
        raise ValueError('Plot filled: occupancy exceeds 62%')
    # Bounds are a scale hypothesis, never an all-or-nothing clipping veto.
    # Restrict remote UI pixels without deleting interior residual branches.
    mask[:,:max(0,x0-3)] = False
    mask[:,min(mask.shape[1],x1+4):] = False
    return mask,(x0,x1,y0,y1),density


def slopes(image, scan_hz):
    mask, (x0, x1, y0, y1), density = geometry(image, allow_dense=True)
    center = (x0+x1)/2
    amplitude = (x1-x0)/2
    height = y1-y0
    half = max(20, min(80, int(amplitude*.48)))
    left, right = int(center-half), int(center+half)
    local_mask = mask.copy()
    endpoint_margin = max(5,round(height*.06))
    local_mask[:y0+endpoint_margin] = False
    local_mask[y1-endpoint_margin+1:] = False
    # Column-run centres preserve two directions near an X; thinning can turn
    # a thick junction into a false horizontal segment. Omit its merged core.
    strip = np.zeros((image.shape[0],right-left+1),np.uint8)
    for column in range(left,right+1):
        if abs(column-center)<amplitude*.24:
            continue
        for top,bottom in runs(local_mask[:,column]):
            if 2 <= bottom-top <= max(12,height*.04):
                strip[round((top+bottom-1)/2),column-left] = 255
    lines = cv2.HoughLinesP(strip, 1, np.pi/1800, threshold=12,
                            minLineLength=half, maxLineGap=max(8,round(amplitude*.55)))
    if lines is None:
        raise ValueError('No resolved central-zero slopes')
    sy, sx = np.where(strip > 0)
    values = []
    rejected_points = []
    overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    cv2.line(overlay, (round(center), y0), (round(center), y1), (0,255,255), 1)
    for lx0, ly0, lx1, ly1 in lines[:, 0]:
        if abs(lx1-lx0) < half:
            continue
        b = (ly1-ly0)/(lx1-lx0)
        intercept = ly0-b*lx0
        near = abs(sy-(intercept+b*sx)) < 1.4
        px, py = sx[near], sy[near]
        if len(px) < half*.7 or np.ptp(px) < half*1.4:
            continue
        # asin coordinate corrects finite-width curvature about the zero line.
        z = amplitude*np.arcsin(np.clip((px+left-center)/amplitude, -.99,.99))
        b, a = np.polyfit(z, py, 1)
        residual = float(np.sqrt(np.mean((py-a-b*z)**2)))
        if abs(b) < .035 or abs(b)*2*half < 3 or residual > 1.1:
            continue
        local_radius = max(5,int(np.ceil(abs(b)*half+4)))
        ya,yb = max(y0,round(a)-local_radius),min(y1,round(a)+local_radius+1)
        patch = local_mask[ya:yb,left:right+1]
        if patch.size == 0 or patch.mean() > .70:
            rejected_points.append(dict(crossing_y=float(a),reason='Local branches merged or filled'))
            continue
        if any(abs(v['crossing_y']-a)<3 and np.sign(v['dy_dx'])==np.sign(b) for v in values):
            continue
        estimate = height*scan_hz/(2*np.pi*amplitude*abs(b))
        # Include raster resolution and uncalibrated visible-bound uncertainty.
        relative = max(.035, 1.0/(2*half*abs(b)))
        if not 900 <= estimate <= 110000:
            continue
        values.append(dict(dy_dx=float(b), crossing_y=float(a), frequency_hz=float(estimate),
                           relative_uncertainty=float(relative), residual_px=residual))
        cv2.line(overlay, (left, round(a-b*half)), (right, round(a+b*half)), (0,220,0), 1)
    if not values:
        raise ValueError('Central slopes too merged, flat, or poorly resolved')
    # Different signs and crossings should share an absolute slope; retain all
    # frequency clusters instead of silently averaging unrelated branches.
    clusters = []
    for item in sorted(values, key=lambda v: v['frequency_hz']):
        if not clusters or item['frequency_hz']/np.median([v['frequency_hz'] for v in clusters[-1]]) > 1.12:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    candidates = {}
    for group in clusters:
        f = float(np.median([v['frequency_hz'] for v in group]))
        error = float(np.median([v['relative_uncertainty'] for v in group]))
        for grid in range(max(1000, int(np.ceil(f*(1-error)/100))*100),
                          min(100000, int(np.floor(f*(1+error)/100))*100)+1, 100):
            score = abs(grid/f-1)/error + .15/max(1, len(group))
            candidates[grid] = min(score, candidates.get(grid, float('inf')))
    ranked = sorted(candidates, key=candidates.get)
    return dict(scan_hz=scan_hz, bounds=[x0,y0,x1,y1], occupancy=density,
                amplitude_px=amplitude, visible_ramp_height_px=height,
                slopes=values, candidates_hz=ranked, confirmed=False,
                independent_crossing_count=len(set(round(v['crossing_y']/3) for v in values)),
                local_rejections=rejected_points,
                slope_frequency_clusters=[dict(median_hz=float(np.median([v['frequency_hz'] for v in g])),
                    independent_points=len(g)) for g in clusters],
                measurement_region_y=[y0+endpoint_margin,y1-endpoint_margin],
                bounds_method='Interior-row robust horizontal span; endpoints excluded from slope extraction',
                local_method='Column-run centres outside merged zero-crossing core; asin-corrected directional fits',
                slope_extraction_uses_global_density_veto=False,
                possible_horizontal_clipping=bool(x0<4 or x1>=image.shape[1]-4),
                possible_vertical_clipping=bool(y0 <= 39 or y1 >= 343),
                height_calibration='conditional: observed extent; not independently calibrated'), overlay


def verify_single_trace(image, scan_hz, expected_cycles):
    from trace_validation import verify
    return verify(image, scan_hz, expected_cycles, geometry)


def legacy_main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--hardware',action='store_true')
    ap.add_argument('--image',type=Path)
    ap.add_argument('--scan-hz',type=float,default=5000)
    ap.add_argument('--scan-windows',type=float,nargs='+',default=[500,1000,2000,5000,10000,20000])
    ap.add_argument('--max-search',type=int,default=12)
    ap.add_argument('--max-candidates',type=int,default=5)
    args=ap.parse_args()
    if any(not 250 <= f <= 210000 for f in args.scan_windows):
        ap.error('Scan windows must be within 250–210000 Hz')
    out=ROOT/'artifacts/diagnostics'/('zero_slope_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True)
    report=dict(method='central zero-line local slope followed by candidate probes', confirmed=False,
                settings=dict(exposure_ms=8,gain=0,amplitude_code=8,scan_windows_hz=args.scan_windows,validator_version=2,slope_version=2), observations=[], validations=[])
    def save():
        (out/'summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
    if not args.hardware:
        if args.image is None: ap.error('Select --hardware or --image')
        r,overlay=slopes(cv2.imread(str(args.image),0),args.scan_hz)
        report['observations'].append(r);cv2.imwrite(str(out/'slopes.png'),overlay);save()
        print('ARTIFACTS',out);return
    from vision_calibrator import Camera
    from strategy_control import StrategyImageAdapter
    from msp_wave_link import MSPM0WaveLink
    cfg=json.loads((ROOT/'config.json').read_text());cfg.update(manual_exposure=80,manual_gain=0)
    camera=None
    try:
        camera=Camera(cfg); adapter=StrategyImageAdapter(cfg)
        with MSPM0WaveLink(cfg['serial_port']) as link:
            def capture(f,stem):
                start=time.perf_counter()
                ack=link.set_waveform('sawtooth',float(f),0,8,direction='rising')
                report['last_ack']=asdict(ack)
                time.sleep(.4)
                raw,_=camera.frame_timed();clean,warped=adapter.convert(raw)
                cv2.imwrite(str(out/f'{stem}_raw.jpg'),raw);cv2.imwrite(str(out/f'{stem}_384.png'),clean)
                report['last_scan_hz']=ack.frequency_millihz/1000
                return clean,ack.frequency_millihz/1000,time.perf_counter()-start
            windows=args.scan_windows
            for i,f in enumerate(windows[:args.max_search]):
                candidates=[]
                stem=f'search_{i:02d}_{f}Hz'
                clean,actual,elapsed=capture(f,stem)
                start=time.perf_counter()
                row=dict(scan_hz=actual,stem=stem,capture_seconds=elapsed)
                try:
                    r,overlay=slopes(clean,actual);row.update(r)
                    cv2.imwrite(str(out/f'{stem}_slopes.png'),overlay)
                    candidates=r['candidates_hz'][:args.max_candidates]
                except ValueError as e:
                    row['rejection']=str(e)
                row['analysis_seconds']=time.perf_counter()-start
                report['observations'].append(row);save();print(json.dumps(row),flush=True)
                for index,f in enumerate(candidates):
                    row=dict(candidate_hz=f,probes=[]);report['validations'].append(row)
                    for divisor in (1,2):
                        for repeat in (1,2):
                            stem=f'verify_search{i}_{index}_{f}Hz_div{divisor}_{repeat}'
                            clean,actual,elapsed=capture(f/divisor,stem)
                            try:r=verify_single_trace(clean,actual,divisor)
                            except ValueError as e:r=dict(accepted=False,reason=str(e))
                            from trace_validation import overlay_trace
                            cv2.imwrite(str(out/f'{stem}_tracked.png'),overlay_trace(clean,r))
                            row['probes'].append(dict(stem=stem,scan_hz=actual,capture_seconds=elapsed,**r));save()
                            print(json.dumps({k:v for k,v in row['probes'][-1].items() if k!='trace_points'}),flush=True)
                        if not all(p['accepted'] for p in row['probes']):break
                    row['passed_shape_checks']=len(row['probes'])==4 and all(p['accepted'] for p in row['probes'])
                    if row['passed_shape_checks']:
                        report.setdefault('supported_grid_candidates_hz', []).append(f)
                        report['result']='Shape-supported candidate set; independent height/clock calibration needed to distinguish nearby 100 Hz steps'
                        save()
                if report.get('supported_grid_candidates_hz'):break
            if 'result' not in report:report['result']='No candidate passed both single-trace probe checks'
    except Exception as e:
        report['error']=f'{type(e).__name__}: {e}'
        raise
    finally:
        if camera is not None:camera.close()
        save();print('ARTIFACTS',out,flush=True)

def main():
    if '--legacy-scan' in sys.argv:
        sys.argv.remove('--legacy-scan')
        legacy_main()
    else:
        from slope_image_cli import main as image_main
        image_main()


if __name__=='__main__':main()
