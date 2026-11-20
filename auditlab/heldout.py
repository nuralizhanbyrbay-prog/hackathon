"""Published generalization suite with new parameter combinations (not organizer-secret data)."""
import json
from pathlib import Path
import random
import time
from .client import AuditEnv
from .generator import generate,TYPES
from .independent import validate_episode
from .policies import run_policy
from .scoring import oracle

def configurations():
    randomizer=random.Random(73921);cases=[]
    for i in range(20):
        counts={t:randomizer.randrange(4) for t in TYPES}
        if i<5:counts=dict.fromkeys(TYPES,0)
        interaction=('fx_cutoff' if i%2 else 'allocation_cutoff') if 5<=i<10 else None
        cases.append({'seed':7000+i,'config':{'invoices':randomizer.randrange(50,81),
            'errors':counts,'payment_delay_days':randomizer.randrange(8),
            'timezone':randomizer.choice(['+05:00','+00:00','-04:00','+09:00']),
            'interaction':interaction,'transform':randomizer.randrange(10000,100000)}})
    return cases

def main():
    output=Path('artifacts');output.mkdir(exist_ok=True);started=time.monotonic();cases=configurations();reports=[];proofs=0
    with (output/'generalization-trajectories.jsonl').open('w',encoding='utf-8') as log, AuditEnv() as env:
        for case in cases:
            episode=generate(**case);check=validate_episode(episode);proofs+=check['evidence_sets_checked']
            originals={}
            for variant in ('original','renamed'):
                config={**case['config']}
                if variant=='renamed':config['transform']+=997
                transformed=generate(case['seed'],config);validate_episode(transformed)
                for policy in ('random','reconcile','flag_all','oracle'):
                    initial,_=env.reset(case['seed'],config,mode='evaluation');step_number=0
                    def advance(action):
                        nonlocal step_number
                        obs,reward,term,trunc,info=env.step(action);step_number+=1
                        log.write(json.dumps({'episode_id':initial['manifest']['episode_id'],'policy':policy,
                            'variant':variant,'step':step_number,'action':action,'observation':obs,
                            'reward':reward,'terminated':term,'truncated':trunc})+'\n')
                        if reward!=0:raise ValueError('evaluation reward exposed')
                        return obs,reward,term,trunc,info
                    if policy=='oracle':advance({'action':'submit','findings':oracle(transformed['truth'],initial['manifest']['episode_id'])})
                    else:run_policy(policy,initial,advance)
                    r=env.report();metrics={k:r[k] for k in ('TP','FP','FN','P','R','F1','reward','steps','truncated')}
                    if variant=='original':originals[policy]=metrics
                    elif metrics!=originals[policy]:raise ValueError('metamorphic mismatch')
                    if policy in ('reconcile','oracle') and r['F1']!=1:raise ValueError('independent reference failed')
                    reports.append({'episode_id':initial['manifest']['episode_id'],'variant':variant,'policy':policy,**metrics,'seconds':r['seconds']})
    summary={'cases':20,'clean_cases':5,'interaction_cases':5,'policy_runs':len(reports),'paired_comparisons':80,
             'independently_proven_evidence_sets':proofs,'seconds':time.monotonic()-started,
             'all_passed':True,'note':'Published development generalization suite; not secret organizer results.'}
    for name,value in [('generalization-configs.json',cases),('generalization-reports.json',reports),('generalization-metrics.json',summary)]:
        (output/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
