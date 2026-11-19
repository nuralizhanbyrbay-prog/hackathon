"""Example external agent. Receives observations only; emits JSON actions only."""
import json
import sys
from pathlib import Path

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from auditlab.policies import run_policy

def main():
    name=sys.argv[1] if len(sys.argv)>1 else 'reconcile'
    initial=json.loads(sys.stdin.readline())['observation']
    def step(action):
        print(json.dumps(action),flush=True)
        line=sys.stdin.readline()
        if not line:raise SystemExit(0)
        result=json.loads(line)
        return result['observation'],result['reward'],result['terminated'],result['truncated'],{}
    run_policy(name,initial,step)

if __name__=='__main__':main()
