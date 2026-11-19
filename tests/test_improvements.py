import copy
import json
import sys
import time
import unittest
from pathlib import Path
from auditlab.generator import generate,convert,TYPES
from auditlab.policies import reconcile
from auditlab.service import Session
from auditlab.scoring import score,oracle
from auditlab.external import run_external

class ImprovedDataTests(unittest.TestCase):
    def test_valid_unallocated_and_null_links(self):
        e=generate(11,{'errors':dict.fromkeys(TYPES,0)})
        self.assertTrue(any(ref['invoice_id'] is None for p in e['tables']['payments'] for ref in p['invoice_refs']))
        self.assertTrue(any(r['account']=='unallocated' for r in e['tables']['ledger']))
        self.assertEqual(reconcile(e['tables'],e['manifest'])['findings'],[])
    def test_currency_exponents(self):
        self.assertEqual(convert(100,'3',0),30000)
        self.assertEqual(convert(1000,'1550',3),155000)
        self.assertEqual(convert(1,'0.5'),0)
        self.assertEqual(convert(3,'0.5'),2)
        self.assertEqual(convert(-3,'0.5'),-2)
    def test_dates_differ(self):
        e=generate(72);invoices={x['invoice_id']:x for x in e['tables']['invoices']}
        self.assertTrue(any(p['timestamp'][:10]!=invoices[p['invoice_refs'][0]['invoice_id']]['issued_at'][:10] for p in e['tables']['payments']))
        self.assertTrue(any(p['timestamp'][:7]=='2026-11' for p in e['tables']['payments']))
    def test_clean_receivables_settle_exactly(self):
        for seed in range(10):
            e=generate(seed);clean=e['provenance']['clean'];balances={}
            for row in clean['ledger']:
                if row['account']=='receivable':balances[row['invoice_id']]=balances.get(row['invoice_id'],0)+row['debit_minor']-row['credit_minor']
            self.assertTrue(all(v==0 for v in balances.values()),balances)
    def test_wrong_fx_cause_is_recorded(self):
        e=generate(71)
        for change in e['provenance']['changes']:
            if change['type']=='fx_conversion':
                self.assertNotEqual(change['cause']['required_rate'],change['cause']['used_rate'])
                self.assertIn('required_date',change['cause'])
    def test_config_identity_and_transform(self):
        self.assertNotEqual(generate(1)['manifest']['episode_id'],generate(1,{'payment_delay_days':0})['manifest']['episode_id'])
        self.assertEqual(generate(1)['manifest']['episode_id'],generate(1,{'transform':22})['manifest']['episode_id'])
    def test_invalid_config(self):
        for config in ({'payment_delay_days':-1},{'payment_delay_days':8},{'payment_delay_days':True},{'typo':1}):
            with self.assertRaises(ValueError):generate(1,config)
    def test_post_timeout_stays_truncated(self):
        s=Session();s.reset(1,max_steps=1);s.step({})
        result=s.step({});self.assertFalse(result[2]);self.assertTrue(result[3])
    def test_stress_independent_seeds(self):
        for seed in range(100,130):
            e=generate(seed,{'interaction':['fx_cutoff','allocation_cutoff',None][seed%3],
                             'transform':seed,'timezone':['-04:00','+09:00'][seed%2]})
            a=reconcile(e['tables'],e['manifest'])
            self.assertEqual(score(a,e['manifest']['episode_id'],e['truth'],0)['F1'],1)
    def test_evidence_ablation(self):
        e=generate(19);a=oracle(e['truth'],e['manifest']['episode_id'])
        baseline=score(a,e['manifest']['episode_id'],e['truth'],0)['TP']
        for i,f in enumerate(a['findings']):
            for j in range(len(f['evidence_ids'])):
                bad=copy.deepcopy(a);bad['findings'][i]['evidence_ids'].pop(j)
                self.assertLess(score(bad,e['manifest']['episode_id'],e['truth'],0)['TP'],baseline)

class ExternalAgentTests(unittest.TestCase):
    def test_external_reconciliation(self):
        agent=str(Path(__file__).resolve().parent.parent/'auditlab/agent.py')
        result=run_external([sys.executable,'-I',agent,'reconcile'],config={'interaction':'fx_cutoff'})
        self.assertEqual(result['agent_status'],'completed');self.assertEqual(result['report']['F1'],1)
        self.assertTrue(all(e['reward']==0 for e in result['trajectory']))
        for e in result['trajectory']:
            text=json.dumps(e['observation'])
            for key in ('expected_amount_minor','provenance','issue_id'):self.assertNotIn(key,text)
    def test_hanging_agent_watchdog(self):
        start=time.monotonic();result=run_external([sys.executable,'-c','import time; time.sleep(10)'],timeout=.2)
        self.assertLess(time.monotonic()-start,4)
        self.assertEqual(result['agent_status'],'agent_timeout');self.assertTrue(result['report']['truncated'])
        self.assertTrue(result['trajectory'][-1]['truncated'])
        self.assertEqual(result['report']['answer']['findings'],[])
    def test_read_blocking_agent_watchdog(self):
        code='import sys,json,time; json.loads(sys.stdin.readline()); print(json.dumps(dict(action="list_records",table="ledger",limit=50)),flush=True); time.sleep(10)'
        start=time.monotonic();result=run_external([sys.executable,'-c',code],timeout=.3)
        self.assertLess(time.monotonic()-start,4);self.assertTrue(result['report']['truncated'])
    def test_agent_crash(self):
        result=run_external([sys.executable,'-c','raise SystemExit(1)'])
        self.assertTrue(result['report']['truncated']);self.assertEqual(result['agent_status'],'agent_exited')
    def test_oversized_action(self):
        result=run_external([sys.executable,'-c','print("x"*200001,flush=True)'])
        self.assertEqual(result['agent_status'],'oversized_action');self.assertTrue(result['report']['truncated'])
    def test_protocol_cannot_read_evaluator(self):
        code='import sys,json; o=json.loads(sys.stdin.readline())["observation"]; print(json.dumps(dict(op="report")),flush=True); json.loads(sys.stdin.readline()); print(json.dumps(dict(action="submit",findings=dict(episode_id=o["manifest"]["episode_id"],findings=[]))),flush=True)'
        result=run_external([sys.executable,'-c',code])
        self.assertEqual(result['trajectory'][0]['observation']['error'],'invalid_request')
    def test_oversized_submission_oracle_rejected(self):
        e=generate(33);a=oracle(e['truth'],e['manifest']['episode_id']);a['findings']*=10
        self.assertEqual(score(a,e['manifest']['episode_id'],e['truth'],0)['reward'],-1)

if __name__=='__main__':unittest.main()
