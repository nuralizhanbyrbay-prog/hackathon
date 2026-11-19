import http.client
import json
import threading
import unittest
from auditlab.web import Server

class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=Server(('127.0.0.1',0));cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join();cls.server.server_close()
    def request(self,path,data=None,token=True,host=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        headers={}
        if token:headers['X-AuditLab-Token']=self.server.dashboard.token
        if host:headers['Host']=host
        body=json.dumps(data) if data is not None else None
        c.request('POST' if data is not None else 'GET',path,body=body,headers=headers)
        response=c.getresponse();status=response.status;raw=response.read().decode();c.close()
        return status,json.loads(raw) if raw.startswith('{') else raw
    def reset(self,scenario='fx_cutoff'):
        status,state=self.request('/api/reset',{'seed':2026,'invoices':50,'scenario':scenario})
        self.assertEqual(status,200);return state
    def test_static_dashboard(self):
        status,html=self.request('/')
        self.assertEqual(status,200);self.assertIn('Создать компанию',html);self.assertNotIn('__API_TOKEN__',html)
    def test_token_required(self):self.assertEqual(self.request('/api/reset',{},token=False)[0],403)
    def test_host_rebinding_rejected(self):self.assertEqual(self.request('/',host='evil.example')[0],403)
    def test_real_audit_and_evidence(self):
        self.reset();status,s=self.request('/api/run',{'policy':'reconcile'})
        self.assertEqual(status,200);self.assertTrue(s['done']);self.assertEqual(s['report']['F1'],1)
        self.assertGreater(s['report']['TP'],0);self.assertTrue(s['report']['answer']['findings'][0]['evidence_ids'])
    def test_clean_company(self):
        self.reset('clean');_,s=self.request('/api/run',{'policy':'reconcile'})
        self.assertEqual(s['report']['FP'],0);self.assertEqual(s['report']['answer']['findings'],[])
    def test_read_costs_step(self):
        self.reset();status,s=self.request('/api/step',{'action':{'action':'list_records','table':'ledger','limit':50}})
        self.assertEqual(status,200);self.assertEqual(s['remaining_steps'],99);self.assertEqual(len(s['events'][-1]['observation']['result']['records']),50)
        self.assertIsNone(s['report']);self.assertNotIn('truth',s)
    def test_replay_and_no_double_submit(self):
        a=self.reset()['initial'];self.request('/api/run',{'policy':'reconcile'})
        self.assertEqual(self.request('/api/run',{'policy':'reconcile'})[0],400)
        self.assertEqual(a,self.reset()['initial'])
    def test_manual_submit(self):
        state=self.reset('clean');ep=state['initial']['manifest']['episode_id']
        _,s=self.request('/api/step',{'action':{'action':'submit','findings':{'episode_id':ep,'findings':[]}}})
        self.assertTrue(s['done']);self.assertEqual(s['report']['reward'],1)
    def test_bad_seed(self):self.assertEqual(self.request('/api/reset',{'seed':'secret'})[0],400)

if __name__=='__main__':unittest.main()
