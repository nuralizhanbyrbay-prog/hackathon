"""Adversarial smoke test: checks isolation, then communicates only valid actions."""
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import time

def main():
    initial=json.loads(sys.stdin.readline())['observation']
    if sys.argv[1:]==['--hang']:
        time.sleep(120);return
    checks=[]
    try:Path(sys.argv[1]).read_bytes();checks.append(False)
    except (FileNotFoundError,PermissionError):checks.append(True)
    checks.append(all(not Path('/app/auditlab/'+f).exists() for f in ('service.py','generator.py','scoring.py','client.py')))
    try:importlib.import_module('auditlab.generator');checks.append(False)
    except ModuleNotFoundError:checks.append(True)
    try:Path('/app/write-probe').write_text('blocked');checks.append(False)
    except OSError:checks.append(True)
    with socket.socket() as sock:
        sock.settimeout(.3)
        try:sock.connect(('1.1.1.1',443));checks.append(False)
        except OSError:checks.append(True)
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    checks.append(status.get('NoNewPrivs','').strip()=='1')
    checks.append(int(status.get('CapEff','1').strip(),16)==0)
    checks.append(not Path('/var/run/docker.sock').exists() and not Path('/run/docker.sock').exists())
    checks.append('AUDITLAB_PRIVATE_CANARY' not in os.environ)
    checks.append(os.getuid()==65532)
    for ok in checks:
        print(json.dumps({'action':'calculator','expression':'1' if ok else '0'}),flush=True)
        reply=json.loads(sys.stdin.readline())
        if reply['terminated'] or reply['truncated']:return
    print(json.dumps({'op':'report'}),flush=True)
    reply=json.loads(sys.stdin.readline())
    if reply['observation'].get('error')!='invalid_request':raise RuntimeError('evaluator operation exposed')
    print(json.dumps({'action':'submit','findings':{'episode_id':initial['manifest']['episode_id'],'findings':[]}}),flush=True)

if __name__=='__main__':main()
