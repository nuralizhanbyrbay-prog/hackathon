"""Two-container runner: private evaluator and read-only offline agent, brokered over pipes."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from .external import run_external

AGENT_IMAGE='auditlab-agent:1.1'
EVALUATOR_IMAGE='auditlab-evaluator:1.1'

def container_command(docker,image,name,args):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:/@-]{0,199}',image):raise ValueError('invalid image reference')
    if not re.fullmatch(r'auditlab-[a-f0-9-]+',name):raise ValueError('invalid owned container name')
    return [docker,'run','--name',name,'--rm','-i','--pull=never',
        '--network','none','--ipc','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges=true','--user','65532:65532',
        '--cpus','1','--memory','512m','--memory-swap','512m','--pids-limit','32',
        '--ulimit','nofile=64:64','--ulimit','core=0:0',
        '--tmpfs','/tmp:rw,noexec,nosuid,size=16m,mode=1777',image,*args]

def docker_ready(images):
    docker=shutil.which('docker')
    if not docker:raise RuntimeError('Docker is required for sandbox mode. No insecure fallback is used.')
    for image in images:
        result=subprocess.run([docker,'image','inspect','--format','{{.Id}}',image],capture_output=True,text=True,timeout=15)
        if result.returncode:raise RuntimeError('Docker unavailable or image not built: '+image)
    return docker

def run_sandbox(seed=2026,config=None,policy='reconcile',timeout=60,
                agent_image=AGENT_IMAGE,evaluator_image=EVALUATOR_IMAGE,probe_path=None,hang=False):
    if policy not in ('reconcile','random','flag_all'):raise ValueError('unknown policy')
    docker=docker_ready([agent_image,evaluator_image])
    agent_name='auditlab-'+uuid.uuid4().hex;evaluator_name='auditlab-'+uuid.uuid4().hex
    agent_args=['/app/auditlab/agent.py',policy]
    if probe_path is not None:agent_args=['/app/probe_agent.py',str(probe_path)]
    if hang:agent_args=['/app/probe_agent.py','--hang']
    agent=container_command(docker,agent_image,agent_name,agent_args)
    evaluator=container_command(docker,evaluator_image,evaluator_name,['python','-m','auditlab.service'])
    try:
        result=run_external(agent,seed,config,timeout,evaluator_command=evaluator)
        result['isolation']={'mode':'two-containers','network':'none','read_only':True,
            'shared_volumes':False,'user':'65532:65532','memory_per_container':'512m',
            'pids_per_container':32,'cpus_per_container':1}
        return result
    finally:
        # Exact names created by this invocation only. Never enumerate or prune other containers.
        for name in (agent_name,evaluator_name):
            subprocess.run([docker,'rm','-f',name],capture_output=True,timeout=15)

def self_test():
    cases=[]
    for scenario in ('fx_cutoff','allocation_cutoff'):
        r=run_sandbox(config={'interaction':scenario},timeout=30)
        if r['agent_status']!='completed' or r['report']['F1']!=1:raise RuntimeError('sandbox reconciliation failed')
        if any(e['reward']!=0 for e in r['trajectory']):raise RuntimeError('evaluation reward leak')
        cases.append({'case':scenario,'F1':r['report']['F1'],'steps':r['report']['steps'],'passed':True})
    clean={'errors':dict.fromkeys(('duplicate_invoice','payment_allocation','fx_conversion','period_cutoff'),0)}
    with tempfile.TemporaryDirectory(prefix='auditlab-private-') as private:
        secret=Path(private)/'evaluator-canary';secret.write_text('private-evaluator-data-'+uuid.uuid4().hex)
        previous=os.environ.get('AUDITLAB_PRIVATE_CANARY');os.environ['AUDITLAB_PRIVATE_CANARY']='not-for-agent'
        try:r=run_sandbox(config=clean,probe_path=secret,timeout=30)
        finally:
            if previous is None:os.environ.pop('AUDITLAB_PRIVATE_CANARY',None)
            else:os.environ['AUDITLAB_PRIVATE_CANARY']=previous
        names=('host_file_unreadable','evaluator_sources_absent','generator_import_denied','root_write_denied',
               'network_denied','no_new_privileges','capabilities_dropped','docker_socket_absent',
               'private_env_absent','nonroot_uid')
        actions=[e for e in r['trajectory'] if isinstance(e['action'],dict) and e['action'].get('action')=='calculator']
        if len(actions)!=len(names) or any(e['observation'].get('result',{}).get('value')!='1' for e in actions):
            observed={name:e['observation'].get('result',{}).get('value') for name,e in zip(names,actions)}
            raise RuntimeError('sandbox isolation probe failed: '+json.dumps({'agent_status':r['agent_status'],'checks':observed}))
        if r['report']['F1']!=1 or r['agent_status']!='completed':raise RuntimeError('probe did not finish cleanly')
        for name in names:cases.append({'case':name,'passed':True})
        forbidden=[e for e in r['trajectory'] if isinstance(e['action'],dict) and e['action'].get('op')=='report']
        if len(forbidden)!=1 or forbidden[0]['observation'].get('error')!='invalid_request':raise RuntimeError('report exposed')
        cases.append({'case':'report_operation_denied','passed':True})
    start=time.monotonic();r=run_sandbox(config=clean,hang=True,timeout=2)
    if not r['report']['truncated'] or r['report']['answer']['findings']:raise RuntimeError('hung container not truncated')
    cases.append({'case':'hung_container_terminated','passed':True,'seconds':time.monotonic()-start})
    docker=docker_ready([AGENT_IMAGE,EVALUATOR_IMAGE])
    images={image:subprocess.run([docker,'image','inspect','--format','{{.Id}}',image],capture_output=True,text=True,check=True).stdout.strip() for image in (AGENT_IMAGE,EVALUATOR_IMAGE)}
    return {'checks':cases,'all_passed':True,'image_ids':images,'scope':'Linux Docker isolation smoke tests; not a proof against kernel or Docker exploits'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--self-test',action='store_true')
    p.add_argument('--seed',type=int,default=2026);p.add_argument('--policy',choices=['reconcile','random','flag_all'],default='reconcile')
    args=p.parse_args();Path('artifacts').mkdir(exist_ok=True)
    try:
        if args.self_test:
            result=self_test();path=Path('artifacts/sandbox-checks.json')
        else:
            result=run_sandbox(args.seed,{'interaction':'fx_cutoff'},args.policy);path=Path('artifacts/sandbox-report.json')
            Path('artifacts/sandbox-trajectory.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in result['trajectory']),encoding='utf-8')
            result={k:v for k,v in result.items() if k!='trajectory'}
        path.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
    except (RuntimeError,subprocess.TimeoutExpired) as exc:p.exit(2,str(exc)+'\n')

if __name__=='__main__':main()
