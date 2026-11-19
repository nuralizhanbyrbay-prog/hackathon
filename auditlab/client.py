import json
import subprocess
import sys

class AuditEnv:
    """Trusted runner's proxy. Agent adapter gets reset observation and step only.

    Evaluation reports must remain with the runner until the complete benchmark ends.
    This is a process boundary, not a sandbox for hostile Python programs.
    """
    def __init__(self):
        self._proc=subprocess.Popen([sys.executable,'-m','auditlab.service'],stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8')
    def _rpc(self,request):
        self._proc.stdin.write(json.dumps(request)+'\n');self._proc.stdin.flush()
        line=self._proc.stdout.readline()
        if not line:raise RuntimeError('evaluator unavailable')
        reply=json.loads(line)
        if not reply['ok']:raise ValueError('invalid_request')
        return reply['value']
    def reset(self,seed,config=None,mode='train',max_steps=100,timeout=60):
        return tuple(self._rpc({'op':'reset','args':dict(seed=seed,config=config,mode=mode,max_steps=max_steps,timeout=timeout)}))
    def step(self,action):return tuple(self._rpc({'op':'step','action':action}))
    def report(self):return self._rpc({'op':'report'})
    def close(self):
        if self._proc.poll() is None:
            self._proc.stdin.close()
            try:self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:self._proc.kill();self._proc.wait()
        self._proc.stdout.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
