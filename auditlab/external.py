"""Trusted runner with an external policy process and hard wall-clock watchdog.

This separates the protocol, environment variables and working directories.
It does not substitute OS/container isolation against arbitrary hostile code.
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from .client import AuditEnv

def run_external(command,seed=2026,config=None,timeout=60):
    if not 0<timeout<=60:raise ValueError('timeout must be >0 and <=60')
    events=[];reason=None
    # No secrets from the authoring process are deliberately inherited.
    allowed={'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','LANG','LC_ALL'}
    child_env={k:v for k,v in os.environ.items() if k.upper() in allowed}
    child_env['PYTHONIOENCODING']='utf-8'
    with AuditEnv() as env, tempfile.TemporaryDirectory(prefix='auditlab-agent-') as work:
        initial,_=env.reset(seed,config,mode='evaluation',timeout=timeout)
        proc=subprocess.Popen(command,cwd=work,env=child_env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1,shell=False)
        output=queue.Queue(maxsize=1);stop=threading.Event()
        def offer(value):
            while not stop.is_set():
                try:output.put(value,timeout=.05);return
                except queue.Full:pass
        def read():
            try:
                while not stop.is_set():
                    line=proc.stdout.readline(200001)
                    if len(line)>200000:offer(('error','oversized_action'));return
                    if not line:offer(('error','agent_exited'));return
                    offer(('line',line))
            except (OSError,ValueError,UnicodeError):offer(('error','agent_io_error'))
        messages=queue.Queue(maxsize=1)
        def write():
            try:
                while not stop.is_set():
                    try:message=messages.get(timeout=.05)
                    except queue.Empty:continue
                    proc.stdin.write(json.dumps(message)+'\n');proc.stdin.flush()
            except (BrokenPipeError,OSError,ValueError):offer(('error','agent_io_error'))
        reader=threading.Thread(target=read,daemon=True);reader.start()
        writer=threading.Thread(target=write,daemon=True);writer.start()
        deadline=time.monotonic()+timeout
        try:
            messages.put({'observation':initial})
            terminated=truncated=False
            while not (terminated or truncated):
                try:
                    kind,value=output.get(timeout=max(.001,deadline-time.monotonic()))
                except queue.Empty:kind,value='error','agent_timeout'
                if time.monotonic()>=deadline:kind,value='error','agent_timeout'
                if kind=='error':
                    reason=value
                    # Trusted control operation, never exposed to the agent.
                    observation,reward,terminated,truncated,info=env.abort(reason)
                    break
                try:action=json.loads(value)
                except (ValueError,RecursionError):action=None
                observation,reward,terminated,truncated,info=env.step(action)
                events.append({'episode_id':initial['manifest']['episode_id'],'step':len(events)+1,
                               'action':action,'observation':observation,'reward':reward,
                               'terminated':terminated,'truncated':truncated})
                if not (terminated or truncated):
                    try:
                        messages.put({'observation':observation,'reward':reward,
                            'terminated':terminated,'truncated':truncated},timeout=max(.001,deadline-time.monotonic()))
                    except queue.Full:
                        reason='agent_timeout';env.abort(reason);report=env.report();break
            report=env.report()
        except (BrokenPipeError,OSError):
            reason='agent_io_error';env.abort(reason);report=env.report()
        finally:
            stop.set()
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=3)
            reader.join(timeout=1);writer.join(timeout=1)
            proc.stdin.close();proc.stdout.close()
        if reason and report['truncated']:
            events.append({'episode_id':initial['manifest']['episode_id'],'step':report['steps'],
                           'action':None,'runner_event':reason,
                           'observation':{'status':'truncated','remaining_steps':initial['remaining_steps']-report['steps']},
                           'reward':0.,'terminated':False,'truncated':True})
        return {'report':report,'trajectory':events,'agent_status':reason or 'completed'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--policy',choices=['random','reconcile','flag_all'],default='reconcile')
    p.add_argument('--seed',type=int,default=2026);p.add_argument('--timeout',type=float,default=60)
    args=p.parse_args()
    command=[sys.executable,'-I',str(Path(__file__).with_name('agent.py').resolve()),args.policy]
    result=run_external(command,args.seed,{'interaction':'fx_cutoff'},args.timeout)
    Path('artifacts').mkdir(exist_ok=True)
    Path('artifacts/external-trajectory.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in result['trajectory']),encoding='utf-8')
    Path('artifacts/external-report.json').write_text(json.dumps(result['report'],indent=2),encoding='utf-8')
    print(json.dumps({'agent_status':result['agent_status'],'report':result['report']},indent=2))

if __name__=='__main__':main()
