import unittest
from unittest.mock import patch
from auditlab.sandbox import container_command,run_sandbox,docker_ready

class SandboxContractTests(unittest.TestCase):
    def test_no_volumes_network_or_privileges(self):
        c=container_command('docker','auditlab-agent:1.1','auditlab-abcd',['/app/auditlab/agent.py'])
        self.assertEqual(c[c.index('--network')+1],'none')
        self.assertEqual(c[c.index('--user')+1],'65532:65532')
        self.assertEqual(c[c.index('--cap-drop')+1],'ALL')
        self.assertIn('--read-only',c);self.assertIn('no-new-privileges=true',c)
        for forbidden in ('--privileged','--mount','--volume','-v','--pid','--gpus'):self.assertNotIn(forbidden,c)
    def test_resource_caps(self):
        c=container_command('docker','image:1','auditlab-abcd',[])
        for k,v in [('--memory','512m'),('--pids-limit','32'),('--cpus','1'),('--memory-swap','512m')]:self.assertEqual(c[c.index(k)+1],v)
    def test_no_docker_fails_closed(self):
        with patch('auditlab.sandbox.shutil.which',return_value=None):
            with self.assertRaises(RuntimeError):docker_ready(['image:1'])
    def test_image_option_injection_denied(self):
        for image in ('--privileged','x --volume=/etc:/etc','x;rm'):
            with self.assertRaises(ValueError):container_command('docker',image,'auditlab-abcd',[])
    def test_evaluator_and_agent_are_separate_and_cleanup_exact(self):
        with patch('auditlab.sandbox.docker_ready',return_value='docker'),patch('auditlab.sandbox.run_external',return_value={}) as run,patch('auditlab.sandbox.subprocess.run') as cleanup:
            r=run_sandbox()
            agent=run.call_args.args[0];evaluator=run.call_args.kwargs['evaluator_command']
            self.assertNotEqual(agent[agent.index('--name')+1],evaluator[evaluator.index('--name')+1])
            self.assertIn('auditlab-agent:1.1',agent);self.assertIn('auditlab-evaluator:1.1',evaluator)
            self.assertEqual(cleanup.call_count,2);self.assertEqual(r['isolation']['shared_volumes'],False)
            for call in cleanup.call_args_list:self.assertEqual(call.args[0][1:3],['rm','-f'])
    def test_cleanup_on_agent_exception(self):
        with patch('auditlab.sandbox.docker_ready',return_value='docker'),patch('auditlab.sandbox.run_external',side_effect=RuntimeError('test')),patch('auditlab.sandbox.subprocess.run') as cleanup:
            with self.assertRaises(RuntimeError):run_sandbox()
            self.assertEqual(cleanup.call_count,2)

if __name__=='__main__':unittest.main()
