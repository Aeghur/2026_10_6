"""Explicit experimental multiwindow capture; does not use original controller."""
import json
import sys
import time
from pathlib import Path
from datetime import datetime,timezone
from dataclasses import asdict

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import cv2
from vision_calibrator import Camera
from strategy_control import StrategyImageAdapter
from msp_wave_link import MSPM0WaveLink
from probe_plan import DEFAULT_CONFIG,plan
from recognize_landmarks import recognize


def main():
    cfg=json.loads((ROOT/'config.json').read_text())
    probes=json.loads(DEFAULT_CONFIG.read_text())
    cfg['manual_exposure']=probes['exposure_absolute'];cfg['manual_gain']=probes['gain']
    out=ROOT/'artifacts/diagnostics'/('grid100_windows_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True)
    summary={'experiment':'grid100 geometric window test','confirmed':False,'windows':[],
             'settings':probes,'note':'No independent full-ramp-height calibration; conditional Hz only.'}
    (out/'config.json').write_text(json.dumps(cfg,indent=2))
    camera=None
    try:
        camera=Camera(cfg);images=StrategyImageAdapter(cfg)
        with MSPM0WaveLink(cfg['serial_port']) as link:
            for index,window in enumerate(plan(probes,include_backup=True),1):
                start=time.perf_counter()
                ack=link.set_waveform('sawtooth',window['frequency_hz'],0,probes['amplitude_code'],direction=probes['direction'])
                ack_seconds=time.perf_counter()-start
                row={'window':window,'ack':asdict(ack),'ack_seconds':ack_seconds,'frames':[]}
                summary['windows'].append(row)
                time.sleep(.4)
                for sample in (1,2):
                    stem=f'{index:02d}_{window["frequency_hz"]:g}Hz_{sample}'
                    start=time.perf_counter();raw,timestamp=camera.frame_timed();capture_seconds=time.perf_counter()-start
                    start=time.perf_counter();clean,warped=images.convert(raw);preprocess_seconds=time.perf_counter()-start
                    cv2.imwrite(str(out/f'{stem}_raw.jpg'),raw);cv2.imwrite(str(out/f'{stem}_warped.jpg'),warped);cv2.imwrite(str(out/f'{stem}_384.png'),clean)
                    frame={'stem':stem,'captured_at_monotonic_estimate':timestamp,'capture_seconds':capture_seconds,
                           'preprocess_seconds':preprocess_seconds,'foreground_pixels':int((clean>=128).sum())}
                    start=time.perf_counter()
                    try:
                        report,overlay,skeleton,_=recognize(clean,ack.frequency_millihz/1000)
                        report['recognition_seconds']=time.perf_counter()-start
                        report['input']=str(out/f'{stem}_384.png')
                        cv2.imwrite(str(out/f'{stem}_landmarks.png'),overlay)
                        cv2.imwrite(str(out/f'{stem}_skeleton.png'),skeleton)
                        (out/f'{stem}_recognition.json').write_text(json.dumps(report,indent=2))
                        frame.update(recognition_seconds=report['recognition_seconds'],status='candidate' if report['gap_topology_usable'] else 'unconfirmed_structure',
                                     phase_family_candidate=report['phase_family_count_from_gaps'],
                                     same_edge_spacing_px=report['same_edge_spacing_px'],
                                     left_extrema_count=len(report['left_extrema_y']),right_extrema_count=len(report['right_extrema_y']),
                                     best_candidate=report['candidates'][0])
                    except ValueError as error:
                        frame.update(recognition_seconds=time.perf_counter()-start,status='rejected',reason=str(error))
                    row['frames'].append(frame)
                    (out/'summary.json').write_text(json.dumps(summary,indent=2))
                    print(json.dumps({'window_hz':window['frequency_hz'],**frame},ensure_ascii=False),flush=True)
    finally:
        if camera is not None:camera.close()
        (out/'summary.json').write_text(json.dumps(summary,indent=2))
        print('ARTIFACTS',out,flush=True)


if __name__=='__main__':main()
