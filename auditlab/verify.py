"""One offline command: unit tests, causal validation, benchmark, metamorphic checks, export."""
import copy
import csv
import io
import json
import platform
import sys
import time
import unittest
from pathlib import Path
from .client import AuditEnv
from .generator import generate, TYPES, VERSION
from .policies import run_policy, reconcile
from .scoring import oracle, score

def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def export_episode(e,path):
    path.mkdir(parents=True,exist_ok=True)
    dump(path/'public/manifest.json',e['manifest'])
    for table,rows in e['tables'].items():
        target=path/'public'
        if table in ('ledger','fx'):
            buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            (target/(table+'.csv')).write_text(buf.getvalue(),encoding='utf-8')
        elif table=='policy':dump(target/'policy.json',rows[0])
        else:(target/(table+'.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf-8')
    dump(path/'evaluator/truth.json',e['truth']);dump(path/'evaluator/provenance.json',e['provenance'])

def independent_check(e):
    """Reconcile public documents independently of causal truth construction."""
    clean=e['provenance']['clean']
    assert sum(x['debit_minor']-x['credit_minor'] for x in clean['ledger'])==0
    assert sum(x['debit_minor']-x['credit_minor'] for x in e['tables']['ledger'])==0
    balances={}
    for row in clean['ledger']:
        if row['account']=='receivable':balances[row['invoice_id']]=balances.get(row['invoice_id'],0)+row['debit_minor']-row['credit_minor']
    assert all(x==0 for x in balances.values())
    assert reconcile(clean,e['manifest'])['findings']==[]
    answer=reconcile(e['tables'],e['manifest'])
    report=score(answer,e['manifest']['episode_id'],e['truth'],0)
    assert report['F1']==1,report
    accessible={x.get('row_id',x.get('doc_id')) for rows in e['tables'].values() for x in rows}
    for issue in e['truth']:
        assert all(set(es)<=accessible for es in issue['evidence_sets'])
    assert len(e['truth'])==len(e['provenance']['changes'])
    return True

def configurations():
    result=[]
    for i in range(20):
        clean=i<5;interaction=('allocation_cutoff' if i%2 else 'fx_cutoff') if 5<=i<10 else None
        result.append({'invoices':50+i*3,'errors':dict.fromkeys(TYPES,0 if clean else 1),
                       'interaction':interaction,'timezone':['+05:00','+00:00','-04:00','+09:00'][i%4]})
    return result

def main():
    started=time.perf_counter()
    suite=unittest.defaultTestLoader.discover('tests')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():raise SystemExit(1)
    output=Path('artifacts');output.mkdir(exist_ok=True)
    reports=[];pairs=[];episode_index=[];configs=configurations();trajectories=output/'trajectories.jsonl'
    with trajectories.open('w',encoding='utf-8') as log:
        with AuditEnv() as env:
            for i,c in enumerate(configs):
                seed=1000+i;e=generate(seed,c);independent_check(e)
                split='train' if i<10 else 'dev'
                export_episode(e,output/'episodes'/split/e['manifest']['episode_id'])
                episode_index.append({'split':split,'episode_id':e['manifest']['episode_id'],
                    'version':VERSION,'path':f"episodes/{split}/{e['manifest']['episode_id']}"})
                for policy in ('random','reconcile','flag_all','oracle'):
                    initial,_=env.reset(seed,c,mode='evaluation');counter=0
                    def step(action):
                        nonlocal counter
                        obs,reward,term,trunc,info=env.step(action);counter+=1
                        log.write(json.dumps({'episode_id':initial['manifest']['episode_id'],'policy':policy,
                            'step':counter,'action':action,'observation':obs,'reward':reward,
                            'terminated':term,'truncated':trunc},ensure_ascii=False)+'\n')
                        return obs,reward,term,trunc,info
                    if policy=='oracle':step({'action':'submit','findings':oracle(e['truth'],e['manifest']['episode_id'])})
                    else:run_policy(policy,initial,step)
                    report=env.report();reports.append({'episode_id':e['manifest']['episode_id'],'policy':policy,**report})
                # Same scenario, all IDs renamed and table order changed.
                transformed=generate(seed,{**c,'transform':5000+i});independent_check(transformed)
                original=score(oracle(e['truth'],e['manifest']['episode_id']),e['manifest']['episode_id'],e['truth'],7)
                changed=score(oracle(transformed['truth'],transformed['manifest']['episode_id']),transformed['manifest']['episode_id'],transformed['truth'],7)
                assert original==changed
                assert generate(seed,c)==e
                for reference in reports[-4:]:
                    policy=reference['policy']
                    initial,_=env.reset(seed,{**c,'transform':5000+i},mode='evaluation')
                    if policy=='oracle':env.step({'action':'submit','findings':oracle(transformed['truth'],transformed['manifest']['episode_id'])})
                    else:run_policy(policy,initial,env.step)
                    actual=env.report()
                    keys=('TP','FP','FN','P','R','F1','reward','steps','truncated')
                    assert all(reference[k]==actual[k] for k in keys),(policy,reference,actual)
                    pairs.append({'episode_id':e['manifest']['episode_id'],'policy':policy,
                                  'transform':5000+i,'passed':True,'metrics':{k:actual[k] for k in keys}})
    aggregate={}
    for policy in ('random','reconcile','flag_all','oracle'):
        rows=[r for r in reports if r['policy']==policy]
        aggregate[policy]={key:sum(r[key] for r in rows)/len(rows) for key in ('P','R','F1','reward','steps','seconds')}
    summary={'version':VERSION,'python':platform.python_version(),'platform':platform.platform(),
             'unit_tests':result.testsRun,'episodes':20,'clean_episodes':5,'interaction_episodes':5,
             'metamorphic_pairs':20,'paired_policy_runs':len(pairs),'elapsed_seconds':time.perf_counter()-started,'policies':aggregate,
             'evaluation_rewards_hidden_in_trajectories':True,
             'note':'Development benchmark, not organizer-hidden results; no model training claim.'}
    dump(output/'metrics.json',summary);dump(output/'evaluator-reports.json',reports)
    dump(output/'episode-index.json',episode_index)
    dump(output/'metamorphic-reports.json',pairs)
    dump(output/'configs.json',{'train_seeds':list(range(1000,1010)),'dev_seeds':list(range(1010,1020)),'configs':configs})
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
