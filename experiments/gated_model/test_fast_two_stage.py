import unittest
from fast_two_stage import shortlist
from sine_refinement import choose


class ShortlistTests(unittest.TestCase):
    def test_invalid_scout_is_excluded_and_two_lowest_selected(self):
        rows=[dict(frequency_hz=45200,frames=[dict(rejection='blank')])]
        rows += [dict(frequency_hz=f,frames=[dict(metrics=dict(score=s))])
                 for f,s in [(45300,1.0),(45400,.9),(45500,.08),(45600,.95)]]
        self.assertEqual([r['frequency_hz'] for r in shortlist(rows)],[45500,45400])

    def test_scouts_do_not_count_as_three_confirmation_frames(self):
        rows=[dict(frequency_hz=f,frames=[dict(metrics=dict(score=s))])
              for f,s in [(45400,1.),(45500,.08)]]
        self.assertEqual(choose(shortlist(rows))['status'],'insufficient_candidates')

    def test_bad_confirmation_overrides_a_good_scout(self):
        rows=[dict(frequency_hz=45500,frames=[dict(metrics=dict(score=s)) for s in [.08,1.,1.]]),
              dict(frequency_hz=45400,frames=[dict(metrics=dict(score=s)) for s in [.9,.95,.92]])]
        self.assertIsNone(choose(rows)['frequency_hz'])

if __name__=='__main__':unittest.main()
