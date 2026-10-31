"""Exact nominal probe planning for a 100 Hz input grid; no hardware access."""
from fractions import Fraction
from pathlib import Path
import argparse
import json

DEFAULT_CONFIG=Path(__file__).with_name('probe_windows.json')


def window(frequency_hz, grid_hz=100, role='primary'):
    if frequency_hz<=0 or grid_hz<=0:raise ValueError('Positive frequencies required')
    f=Fraction(str(frequency_hz))
    rise=Fraction(1_000_000,1)/f
    nominal_phase_denominator=(Fraction(grid_hz,1)/f).denominator
    return {'role':role,'frequency_hz':float(f),'rise_time_us':float(rise),
            'rise_time_us_exact':str(rise),'reset_ratio':0.,
            'integer_microseconds':rise.denominator==1,
            'max_nominal_phase_family_on_grid':nominal_phase_denominator}


def plan(config, include_backup=False):
    if config['reset_ratio']!=0:raise ValueError('Plan is for zero-return sawtooth')
    rows=[window(f,config['input_grid_hz']) for f in config['primary_frequencies_hz']]
    rows.append(window(config['disambiguation_frequency_hz'],config['input_grid_hz'],'disambiguation'))
    if include_backup:rows.append(window(config['backup_frequency_hz'],config['input_grid_hz'],'backup'))
    if len({r['frequency_hz'] for r in rows})!=len(rows):raise ValueError('Duplicate windows')
    return rows


def ideal_phase_family(input_hz, scan_hz):
    """Ideal rational input/scan ratio; real independent clocks may not close."""
    if input_hz<=0 or scan_hz<=0:raise ValueError('Positive frequencies required')
    ratio=Fraction(str(input_hz))/Fraction(str(scan_hz))
    return {'ratio_exact':str(ratio),'distinct_phases':ratio.denominator,
            'same_edge_events_per_full_ramp_if_complete':ratio.numerator}


def choose_disambiguator(candidates_hz, config):
    """Rank 700/1100 by ideal q discrimination, preferring fewer phases on ties.

    This uses q only. It neither predicts actual image quality nor guarantees
    rejection of aliases; a real observation must agree with other evidence.
    """
    candidates=sorted(set(candidates_hz))
    if len(candidates)<2:return []
    scores=[]
    for f in (config['disambiguation_frequency_hz'],config['backup_frequency_hz']):
        families=[ideal_phase_family(x,f)['distinct_phases'] for x in candidates]
        pairs=sum(families[i]!=families[j] for i in range(len(families)) for j in range(i))
        scores.append({'frequency_hz':f,'q_by_candidate':dict(zip(map(str,candidates),families)),
                       'separated_candidate_pairs_by_q':pairs,'largest_q':max(families)})
    return sorted(scores,key=lambda s:(-s['separated_candidate_pairs_by_q'],s['largest_q'],s['frequency_hz']))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    ap.add_argument('--include-backup',action='store_true')
    ap.add_argument('--candidates',type=float,nargs='*')
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    cfg=json.loads(args.config.read_text())
    result={'config_version':cfg['version'],'windows':plan(cfg,args.include_backup),
            'hardware_commands_sent':False}
    if args.candidates:result['disambiguation_ranking']=choose_disambiguator(args.candidates,cfg)
    content=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(content)
    print(content)


if __name__=='__main__':main()
