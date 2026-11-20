import copy
import json
import unittest
from auditlab.web import Dashboard
from auditlab.generator import generate
from auditlab.scoring import oracle,score
from auditlab.service import Session

class ComparisonTests(unittest.TestCase):
    def setUp(self):self.d=Dashboard()
    def tearDown(self):self.d.close()
    def reset(self,scenario='fx_cutoff'):
        return self.d.dispatch('/api/reset',{'seed':42,'invoices':50,'scenario':scenario})
    def test_compare_same_episode_preserves_session(self):
        self.reset();before=self.d.dispatch('/api/run',{'policy':'reconcile'})
        after=self.d.dispatch('/api/compare',{})
        self.assertEqual(before['events'],after['events']);self.assertEqual(before['report'],after['report'])
        rows=after['comparisons'];self.assertEqual(len(rows),3)
        self.assertEqual({r['episode_id'] for r in rows},{before['initial']['manifest']['episode_id']})
        self.assertEqual(rows[0]['F1'],1);self.assertEqual(rows[2]['reward'],-1)
    def test_compare_requires_finished_episode(self):
        self.reset()
        with self.assertRaises(ValueError):self.d.dispatch('/api/compare',{})
    def test_clean_comparison(self):
        self.reset('clean');self.d.dispatch('/api/run',{'policy':'reconcile'})
        rows=self.d.dispatch('/api/compare',{})['comparisons']
        self.assertEqual(rows[0]['FP'],0);self.assertEqual(rows[0]['F1'],1)
        self.assertEqual(rows[2]['F1'],0)
    def test_export_real_trajectories(self):
        self.reset();self.d.dispatch('/api/run',{'policy':'reconcile'});self.d.dispatch('/api/compare',{})
        export=self.d.dispatch('/api/export-comparison',{})
        events=[json.loads(line) for line in export['content'].splitlines()]
        self.assertEqual({e['policy'] for e in events},{'reconcile','random','flag_all'})
        self.assertEqual(sum(e['terminated'] for e in events),3)
        for event in events:
            for key in ('expected_amount_minor','issue_id','provenance'):self.assertNotIn(key,json.dumps(event['observation']))
    def test_export_unavailable_before_comparison(self):
        with self.assertRaises(ValueError):self.d.dispatch('/api/export-comparison',{})
    def test_reset_clears_comparison(self):
        self.reset();self.d.dispatch('/api/run',{'policy':'reconcile'});self.d.dispatch('/api/compare',{})
        self.assertEqual(self.reset()['comparisons'],[])
    def test_snapshot_independent_and_countdown(self):
        s=self.reset();s['initial']['manifest']['company']='changed'
        self.assertNotEqual(self.d.snapshot()['initial']['manifest']['company'],'changed')
        self.assertGreater(self.d.snapshot()['remaining_seconds'],55)

class FindingDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_follow_matching_not_order(self):
        e=generate(1);a=oracle(e['truth'],e['manifest']['episode_id']);a['findings']*=2
        r=score(a,e['manifest']['episode_id'],e['truth'],0)
        self.assertEqual(sum(x['matched'] for x in r['finding_results']),r['TP'])
        self.assertEqual(len(r['finding_results']),r['TP']+r['FP'])
    def test_wrong_amount_rejected_in_diagnostics(self):
        e=generate(1);a=oracle(e['truth'],e['manifest']['episode_id']);a['findings'][0]['claimed_amount_minor']+=1
        r=score(a,e['manifest']['episode_id'],e['truth'],0)
        self.assertFalse(r['finding_results'][0]['matched'])
    def test_diagnostics_not_in_agent_observation(self):
        s=Session();initial,_=s.reset(1,mode='evaluation')
        result=s.step({'action':'submit','findings':{'episode_id':initial['manifest']['episode_id'],'findings':[]}})
        self.assertNotIn('finding_results',json.dumps(result));self.assertEqual(result[1],0)

if __name__=='__main__':unittest.main()
