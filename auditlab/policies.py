"""Policies use public tables; the oracle is separately evaluator-only."""
import random
from datetime import datetime
from .generator import TYPES, convert

def reconcile(tables,manifest):
    findings=[];ledger=tables['ledger'];policy=tables['policy'][0]
    def finding(kind,ids,amount,evidence):
        findings.append({'type':kind,'affected_ids':sorted(ids),'claimed_amount_minor':abs(amount),
                         'currency':'KZT','evidence_ids':sorted(evidence),'explanation':'Reconciled source documents, policy and ledger postings.'})
    start=datetime.fromisoformat(manifest['period_start']);end=datetime.fromisoformat(manifest['period_end'])
    for inv in tables['invoices']:
        rows=[x for x in ledger if x['invoice_id']==inv['invoice_id'] and x['payment_id'] is None]
        ar=sorted([x for x in rows if x['account']=='receivable'],key=lambda x:x['row_id'])
        revenue=sorted([x for x in rows if x['account']=='revenue'],key=lambda x:x['row_id'])
        if len(ar)>1:
            finding('duplicate_invoice',[inv['invoice_id']],sum(x['debit_minor'] for x in ar[1:]),
                    [inv['doc_id']]+[x['row_id'] for x in ar+revenue])
        # One affected invoice, even if multiple rows record the same timing discrepancy.
        outside=[x for x in ar if not start<=datetime.fromisoformat(x['timestamp'])<end]
        if outside and start<=datetime.fromisoformat(inv['service_at'])<end:
            row=outside[0]
            finding('period_cutoff',[inv['invoice_id']],row['debit_minor'],[inv['doc_id'],row['row_id'],'policy'])
    for p in tables['payments']:
        rate=next(x for x in tables['fx'] if x['source_currency']==p['currency'] and x['target_currency']=='KZT' and x['date']==p['timestamp'][:10])
        rows=[x for x in ledger if x['payment_id']==p['payment_id']]
        bank=[x for x in rows if x['account']=='bank']
        expected=convert(p['amount_minor'],rate['rate']);actual=sum(x['debit_minor'] for x in bank)
        if expected!=actual:
            finding('fx_conversion',[p['payment_id']],actual-expected,[p['doc_id'],bank[0]['row_id'],rate['row_id'],'policy'])
        for ref in p['invoice_refs']:
            if ref['invoice_id'] is None:continue
            allocated=[x for x in rows if x['invoice_id']==ref['invoice_id'] and x['account']=='receivable']
            suspense=[x for x in rows if x['invoice_id']==ref['invoice_id'] and x['account']=='unallocated']
            expected=convert(ref['amount_minor'],rate['rate']);actual=sum(x['credit_minor'] for x in allocated)
            if actual!=expected:
                finding('payment_allocation',[p['payment_id'],ref['invoice_id']],expected-actual,
                        [p['doc_id'],rate['row_id'],'policy']+[x['row_id'] for x in allocated+suspense])
    return {'episode_id':manifest['episode_id'],'findings':findings}

def run_policy(name,initial,step):
    manifest=initial['manifest'];ep=manifest['episode_id']
    if name=='random':
        r=random.Random(42)
        for _ in range(8):
            step({'action':'list_records','table':r.choice(['ledger','invoices','payments','fx']),'cursor':0,'limit':r.randrange(1,51)})
        return step({'action':'submit','findings':{'episode_id':ep,'findings':[]}})
    tables={}
    for table in ('ledger','invoices','payments','fx','policy'):
        tables[table]=[];cursor=0
        while True:
            obs,_,term,trunc,_=step({'action':'list_records','table':table,'cursor':cursor,'limit':50})
            if term or trunc:return
            tables[table].extend(obs['result']['records']);cursor=obs['result']['next_cursor']
            if cursor is None:break
    answer=reconcile(tables,manifest)
    if name=='flag_all':
        # Bounded spray attack: plausible IDs but unsupported amount/evidence.
        answer={'episode_id':ep,'findings':[{'type':TYPES[i%4],'affected_ids':[x['row_id']],
                 'claimed_amount_minor':0,'currency':'KZT','evidence_ids':[x['row_id']],
                 'explanation':'Suspicious.'} for i,x in enumerate(tables['ledger'][:30])]}
    return step({'action':'submit','findings':answer})
