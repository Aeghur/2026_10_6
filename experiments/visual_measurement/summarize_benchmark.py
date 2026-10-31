"""Complete rejection timings and write per-window benchmark tables."""
import argparse
import csv
import json
import re
import time
from pathlib import Path
import cv2
from recognize_landmarks import recognize


def main():
    ap=argparse.ArgumentParser();ap.add_argument('capture_dir',type=Path);args=ap.parse_args()
    p=args.capture_dir.resolve();f=p/'both_models_benchmark.json';r=json.loads(f.read_text())
    if not r['complete']:raise ValueError('Wait until the sequential batch is complete')
    for row in r['runs']:
        if row['model']!='landmarks' or row.get('recognition_seconds') is not None:continue
        log=(p/'both_models'/row['stem']/'landmarks/run.log').read_text()
        match=re.search(r'RECOGNITION_SECONDS_ON_REJECTION\s+([\d.eE+-]+)',log)
        if match:
            row['recognition_seconds']=float(match.group(1));row['timing_source']='original rejected run log'
        else:
            image=cv2.imread(row['input'],0)
            started=time.perf_counter()
            try:recognize(image,row['scan_hz'])
            except ValueError as error:row['rejection_reason']=str(error)
            row['recognition_seconds']=time.perf_counter()-started
            row['timing_source']='repeated core-only rejection timing after batch; original process wall retained'
        row['status']='rejected'
    totals={}
    for model in ('landmarks','folded_fit'):
        rows=[x for x in r['runs'] if x['model']==model]
        core=[x['recognition_seconds'] for x in rows]
        totals[model]={'count':len(rows),'recognition_total_seconds':sum(core),
                       'recognition_mean_seconds':sum(core)/len(core),
                       'process_wall_total_seconds':sum(x['process_wall_seconds']for x in rows),
                       'confirmed_frequency_count':0}
    r['totals']=totals
    r['timing_notes']=['Core measurements exclude process imports, source-image loading and final result file saving.',
                       'Means include quality-rejection cases; they do not compare equal numbers of successful recognitions.',
                       'One rejection was re-timed after batch because the earliest CLI lacked rejection timing.',
                       'Fits below a 0.5 frequency ratio are outside the tested optimizer search range.',
                       'No timing number establishes measurement accuracy or a confirmed physical frequency.']
    f.write_text(json.dumps(r,indent=2))
    windows=[]
    for scan in dict.fromkeys(x['scan_hz']for x in r['runs']):
        rows=[x for x in r['runs'] if x['scan_hz']==scan]
        a=[x for x in rows if x['model']=='landmarks'];b=[x for x in rows if x['model']=='folded_fit']
        windows.append({'scan_hz':scan,'landmark_mean_seconds':sum(x['recognition_seconds']for x in a)/len(a),
                        'fit_mean_seconds':sum(x['recognition_seconds']for x in b)/len(b),
                        'fit_candidates_hz':'; '.join(f"{x.get('candidate_hz',0):.3f}"for x in b),
                        'fit_iou':'; '.join(f"{x.get('iou',0):.3f}"for x in b),
                        'landmark_status':'; '.join(x['status']for x in a)})
    with (p/'both_models_table.csv').open('w',newline='')as stream:
        writer=csv.DictWriter(stream,fieldnames=list(windows[0]));writer.writeheader();writer.writerows(windows)
    print(json.dumps({'totals':totals,'windows':windows},indent=2))


if __name__=='__main__':main()
