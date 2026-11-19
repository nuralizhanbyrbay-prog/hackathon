"""Terminal demonstration; no API keys, model or network."""
import argparse
import json
from .client import AuditEnv
from .policies import run_policy

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=int,default=2026)
    parser.add_argument('--policy',choices=['random','reconcile','flag_all'],default='reconcile')
    parser.add_argument('--interaction',choices=['allocation_cutoff','fx_cutoff'],default='fx_cutoff')
    args=parser.parse_args()
    with AuditEnv() as env:
        obs,_=env.reset(args.seed,{'interaction':args.interaction},mode='train')
        print('AUDITLAB / FINTECH / synthetic financial audit')
        print(json.dumps(obs,ensure_ascii=False,indent=2))
        index=0
        with open('demo-trajectory.jsonl','w',encoding='utf-8') as log:
            def step(action):
                nonlocal index
                observation,reward,terminated,truncated,info=env.step(action);index+=1
                event=dict(episode_id=obs['manifest']['episode_id'],step=index,action=action,
                           observation=observation,reward=reward,terminated=terminated,truncated=truncated)
                log.write(json.dumps(event,ensure_ascii=False)+'\n')
                print(f"step={index:02d} action={action['action']} remaining={observation['remaining_steps']} reward={reward:.3f}")
                return observation,reward,terminated,truncated,info
            run_policy(args.policy,obs,step)
        print('EVALUATOR REPORT / after episode')
        print(json.dumps(env.report(),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
