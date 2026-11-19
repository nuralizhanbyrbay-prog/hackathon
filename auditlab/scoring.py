"""Public strict matcher. Zero minor-unit tolerance; maximum bipartite matching."""
from .generator import TYPES

def validate_submission(answer, episode_id):
    if not isinstance(answer,dict) or set(answer)!={'episode_id','findings'} or answer['episode_id']!=episode_id:
        raise ValueError('invalid submission envelope')
    findings=answer['findings']
    if not isinstance(findings,list) or len(findings)>30: raise ValueError('findings limit')
    keys={'type','affected_ids','claimed_amount_minor','currency','evidence_ids','explanation'}
    for f in findings:
        if not isinstance(f,dict) or set(f)!=keys or f['type'] not in TYPES:raise ValueError('invalid finding')
        for key,limit in [('affected_ids',10),('evidence_ids',10)]:
            v=f[key]
            if not isinstance(v,list) or not 1<=len(v)<=limit or any(not isinstance(x,str) or not 1<=len(x)<=100 for x in v) or len(set(v))!=len(v):raise ValueError('invalid IDs')
        if type(f['claimed_amount_minor']) is not int or f['claimed_amount_minor']<0 or f['claimed_amount_minor']>10**18:raise ValueError('invalid amount')
        if not isinstance(f['currency'],str) or len(f['currency'])!=3:raise ValueError('invalid currency')
        if not isinstance(f['explanation'],str) or not 1<=len(f['explanation'])<=1000:raise ValueError('invalid explanation')
    return findings

def score(answer, episode_id, truth, inspection_steps):
    try:findings=validate_submission(answer,episode_id)
    except (ValueError,TypeError,KeyError):
        return {'reward':-1.0,'P':0.0,'R':0.0,'F1':0.0,'TP':0,'FP':0,'FN':len(truth),'invalid':True}
    graph=[]
    for f in findings:
        graph.append([j for j,t in enumerate(truth) if f['type']==t['type']
          and sorted(f['affected_ids'])==sorted(t['affected_ids'])
          and f['currency']==t['currency'] and f['claimed_amount_minor']==t['expected_amount_minor']
          and any(set(es)<=set(f['evidence_ids']) for es in t['evidence_sets'])])
    matched={}
    def augment(i,seen):
        for j in graph[i]:
            if j in seen:continue
            seen.add(j)
            if j not in matched or augment(matched[j],seen):matched[j]=i;return True
        return False
    for i in range(len(findings)):augment(i,set())
    tp=len(matched);fp=len(findings)-tp;fn=len(truth)-tp
    p=tp/(tp+fp) if tp+fp else 0.;rec=tp/(tp+fn) if tp+fn else 0.
    f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 1.
    return {'P':p,'R':rec,'F1':f1,'TP':tp,'FP':fp,'FN':fn,'invalid':False,
            'reward':max(-1.,min(1.,2*f1-1-.002*inspection_steps))}

def oracle(truth,episode_id):
    return {'episode_id':episode_id,'findings':[{'type':t['type'],'affected_ids':t['affected_ids'],
       'claimed_amount_minor':t['expected_amount_minor'],'currency':t['currency'],
       'evidence_ids':t['evidence_sets'][0],'explanation':'Evaluator-only oracle.'} for t in truth]}
