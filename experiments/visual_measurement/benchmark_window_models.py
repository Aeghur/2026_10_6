"""Sequentially benchmark both independent models on a captured window batch."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('capture_dir',type=Path)
    args=ap.parse_args()
    capture=args.capture_dir.resolve()
    source=json.loads((capture/'summary.json').read_text())
    root=Path(__file__).resolve().parent
    report_path=capture/'both_models_benchmark.json'
    report=json.loads(report_path.read_text()) if report_path.exists() else {
        'capture_dir':str(capture),'mode':'sequential; identical saved inputs; no recapture or hardware access',
        'runs':[],'complete':False}
    existing={(r['stem'],r['model'])for r in report['runs']}
    def save():
        totals={}
        for model in ('landmarks','folded_fit'):
            rows=[r for r in report['runs'] if r['model']==model]
            times=[r['recognition_seconds'] for r in rows if r.get('recognition_seconds') is not None]
            walls=[r['process_wall_seconds'] for r in rows]
            totals[model]={'completed':len(rows),'recognition_count':len(times),'recognition_total_seconds':sum(times),
                           'recognition_mean_seconds':sum(times)/len(times) if times else None,
                           'process_wall_total_seconds':sum(walls)}
        report['totals']=totals
        tmp=report_path.with_suffix('.tmp');tmp.write_text(json.dumps(report,indent=2));tmp.replace(report_path)
    for window in source['windows']:
        for frame in window['frames']:
            stem=frame['stem'];image=capture/f'{stem}_384.png'
            for model,script,extra in (
                ('landmarks','recognize_landmarks.py',['--saw-hz',str(window['ack']['frequency_millihz']/1000)]),
                ('folded_fit','fit_folded_gaps.py',[])):
                if (stem,model) in existing:continue
                out=capture/'both_models'/stem/model;out.mkdir(parents=True,exist_ok=True)
                start=time.perf_counter()
                with (out/'run.log').open('w') as log:
                    result=subprocess.run([sys.executable,str(root/script),str(image),'--output',str(out),*extra],stdout=log,stderr=subprocess.STDOUT)
                elapsed=time.perf_counter()-start
                row={'stem':stem,'scan_hz':window['ack']['frequency_millihz']/1000,'model':model,
                     'process_wall_seconds':elapsed,'exit_code':result.returncode,'recognition_seconds':None,
                     'input':str(image),'confirmed':False}
                f=out/'result.json'
                if f.exists():
                    r=json.loads(f.read_text());row['recognition_seconds']=r.get('recognition_seconds');row['result_path']=str(f)
                    if model=='landmarks':
                        row.update(status='candidate' if r['gap_topology_usable'] else 'unconfirmed_structure',
                                   conditional_candidate=r['candidates'][0],phase_family=r['phase_family_count_from_gaps'])
                    else:
                        row.update(status='image_fit_only',ratio=r['ratio_candidate'],
                                   candidate_hz=r['ratio_candidate']*row['scan_hz'],
                                   visible_ramp_fraction=r['visible_ramp_fraction'],iou=r['iou'],
                                   sweep_count=r['sweep_count'])
                else:
                    row['status']='rejected_or_error';row['error_log_tail']=(out/'run.log').read_text()[-1200:]
                report['runs'].append(row);save()
                print(json.dumps({'finished':len(report['runs']),'of':32,**row}),flush=True)
    report['complete']=True;save();print('BENCHMARK',report_path,flush=True)


if __name__=='__main__':main()
