import json
import unittest
from fractions import Fraction
from probe_plan import DEFAULT_CONFIG,window,plan,ideal_phase_family,choose_disambiguator


class ProbeChecks(unittest.TestCase):
    def setUp(self):self.cfg=json.loads(DEFAULT_CONFIG.read_text())

    def test_exact_primary_periods(self):
        rows=plan(self.cfg)
        self.assertEqual([r['rise_time_us']for r in rows[:6]],[2000,1000,500,200,100,50])
        for r in rows:
            self.assertEqual(Fraction(r['rise_time_us_exact'])*Fraction(str(r['frequency_hz'])),1_000_000)

    def test_noninteger_periods_preserved(self):
        self.assertEqual(window(700)['rise_time_us_exact'],'10000/7')
        self.assertEqual(window(1100)['rise_time_us_exact'],'10000/11')
        self.assertFalse(window(700)['integer_microseconds'])
        self.assertEqual(len(plan(self.cfg,True)),8)

    def test_phase_count_alone_cannot_guarantee_uniqueness(self):
        # 3k and 9k have identical phase-family counts in every proposed scan.
        for f in self.cfg['primary_frequencies_hz']:
            self.assertEqual(ideal_phase_family(3000,f)['distinct_phases'],
                             ideal_phase_family(9000,f)['distinct_phases'])
        # Extra scans do not magically resolve every pair either.
        ranking=choose_disambiguator([3000,9000],self.cfg)
        self.assertTrue(all(r['separated_candidate_pairs_by_q']==0 for r in ranking))

    def test_700_and_1100_can_add_independent_information(self):
        ranking=choose_disambiguator([3000,7000],self.cfg)
        self.assertEqual(ranking[0]['frequency_hz'],700)
        self.assertEqual(ranking[0]['separated_candidate_pairs_by_q'],1)

    def test_invalid_frequency_rejected(self):
        with self.assertRaises(ValueError):window(0)


if __name__=='__main__':unittest.main()
