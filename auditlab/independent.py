"""Second financial checker. No imports from generator, policies, money or scoring.

Uses rational arithmetic and explicit tie-to-even rounding, not Decimal.convert.
Checks causal truth and each evidence set using observable financial relationships.
"""
from collections import Counter,defaultdict
from datetime import datetime
from fractions import Fraction

def require(condition,message):
    if not condition:raise ValueError(message)

def rational_minor(amount,rate,source_exponent=2,target_exponent=2):
    require(type(amount) is int,'money must be integer minor units')
    value=Fraction(rate)*amount*Fraction(10)**(target_exponent-source_exponent)
    sign=-1 if value<0 else 1
    q,r=divmod(abs(value.numerator),value.denominator)
    if 2*r>value.denominator or (2*r==value.denominator and q%2):q+=1
    return sign*q

def signature(issue):
    return issue['type'],tuple(sorted(issue['affected_ids'])),issue['expected_amount_minor'],issue['currency']

def indexes(tables):
    rows=defaultdict(list)
    for row in tables['ledger']:rows[row['invoice_id']].append(row)
    payments=defaultdict(list)
    for row in tables['ledger']:
        if row['payment_id'] is not None:payments[row['payment_id']].append(row)
    rates={(x['date'],x['source_currency'],x['target_currency']):x for x in tables['fx']}
    return rows,payments,rates

def public_discrepancies(tables,manifest):
    policy=tables['policy'][0];by_invoice,by_payment,rates=indexes(tables)
    base=manifest['base_currency'];exps=policy['currency_exponents'];result=[]
    left=datetime.fromisoformat(manifest['period_start']);right=datetime.fromisoformat(manifest['period_end'])
    def add(kind,ids,value):
        result.append({'type':kind,'affected_ids':sorted(ids),'expected_amount_minor':abs(value),'currency':base})
    for invoice in tables['invoices']:
        sales=[x for x in by_invoice[invoice['invoice_id']] if x['payment_id'] is None]
        ar=[x for x in sales if x['account']=='receivable']
        if len(ar)>1:
            require(len({x['debit_minor'] for x in ar})==1,'inconsistent duplicate amounts')
            add('duplicate_invoice',[invoice['invoice_id']],sum(x['debit_minor'] for x in ar)-ar[0]['debit_minor'])
        delayed=[x for x in ar if not left<=datetime.fromisoformat(x['timestamp'])<right]
        if delayed and left<=datetime.fromisoformat(invoice['service_at'])<right:
            add('period_cutoff',[invoice['invoice_id']],delayed[0]['debit_minor'])
    for payment in tables['payments']:
        rate=rates[(payment['timestamp'][:10],payment['currency'],base)]['rate']
        rows=by_payment[payment['payment_id']]
        expected=rational_minor(payment['amount_minor'],rate,exps[payment['currency']],exps[base])
        actual=sum(x['debit_minor'] for x in rows if x['account']=='bank')
        if actual!=expected:add('fx_conversion',[payment['payment_id']],actual-expected)
        refs=defaultdict(int)
        for ref in payment['invoice_refs']:
            if ref['invoice_id'] is not None:refs[ref['invoice_id']]+=ref['amount_minor']
        for invoice_id,amount in refs.items():
            expected=rational_minor(amount,rate,exps[payment['currency']],exps[base])
            actual=sum(x['credit_minor'] for x in rows if x['account']=='receivable' and x['invoice_id']==invoice_id)
            if actual!=expected:add('payment_allocation',[payment['payment_id'],invoice_id],actual-expected)
    return result

def validate_clean(tables,manifest):
    base=manifest['base_currency'];policy=tables['policy'][0];exps=policy['currency_exponents']
    by_invoice,by_payment,rates=indexes(tables)
    require(sum(x['debit_minor']-x['credit_minor'] for x in tables['ledger'])==0,'unbalanced clean ledger')
    invoices={x['invoice_id']:x for x in tables['invoices']};settled=defaultdict(int)
    for payment in tables['payments']:
        require(sum(x['amount_minor'] for x in payment['invoice_refs'])==payment['amount_minor'],'payment refs do not sum to total')
        for ref in payment['invoice_refs']:
            if ref['invoice_id'] is not None:
                require(ref['invoice_id'] in invoices,'dangling invoice reference')
                require(invoices[ref['invoice_id']]['currency']==payment['currency'],'unsupported cross-currency allocation')
                settled[ref['invoice_id']]+=ref['amount_minor']
        require(sum(x['debit_minor']-x['credit_minor'] for x in by_payment[payment['payment_id']])==0,'unbalanced settlement')
    for invoice in invoices.values():
        require(sum(x['total_minor'] for x in invoice['line_items'])==invoice['total_minor'],'invoice line items mismatch')
        rate=rates[(invoice['issued_at'][:10],invoice['currency'],base)]['rate']
        expected=rational_minor(invoice['total_minor'],rate,exps[invoice['currency']],exps[base])
        rows=by_invoice[invoice['invoice_id']]
        sales=[x for x in rows if x['payment_id'] is None]
        require(sum(x['debit_minor'] for x in sales if x['account']=='receivable')==expected,'incorrect clean invoice FX value')
        require(sum(x['credit_minor'] for x in sales if x['account']=='revenue')==expected,'incorrect clean revenue')
        require(settled[invoice['invoice_id']]==invoice['total_minor'],'invoice not fully settled in source currency')
        require(sum(x['debit_minor']-x['credit_minor'] for x in rows if x['account']=='receivable')==0,'receivable residual')
    require(not public_discrepancies(tables,manifest),'clean data contain discrepancies')

def prove(issue,records,manifest):
    """Compute one claim using only the cited records, without consulting other data."""
    base=manifest['base_currency'];kind=issue['type'];ids=set(issue['affected_ids'])
    rows=[r for table,r in records if table=='ledger']
    inv=[r for table,r in records if table=='invoices']
    pay=[r for table,r in records if table=='payments']
    policies=[r for table,r in records if table=='policy']
    if kind=='duplicate_invoice':
        require(len(inv)==1 and ids=={inv[0]['invoice_id']},'duplicate invoice proof lacks primary document')
        ar=[x for x in rows if x['account']=='receivable' and x['invoice_id'] in ids and x['payment_id'] is None]
        revenue=[x for x in rows if x['account']=='revenue' and x['invoice_id'] in ids and x['payment_id'] is None]
        require(len(ar)>=2 and len(ar)==len(revenue),'duplicate proof lacks matching posting pairs')
        require(len({x['debit_minor'] for x in ar})==1,'duplicate proof amounts differ')
        require(sum(x['debit_minor'] for x in ar)==sum(x['credit_minor'] for x in revenue),'duplicate pairs unbalanced')
        amount=abs(sum(x['debit_minor'] for x in ar)-ar[0]['debit_minor'])
    else:
        require(len(policies)==1,'proof lacks policy')
        policy=policies[0]
        if kind=='period_cutoff':
            require(policy['period_rule']=='service_at','unknown cutoff rule')
            require(len(inv)==1 and ids=={inv[0]['invoice_id']},'cutoff proof lacks invoice')
            left=datetime.fromisoformat(manifest['period_start']);right=datetime.fromisoformat(manifest['period_end'])
            require(left<=datetime.fromisoformat(inv[0]['service_at'])<right,'service outside period')
            ar=[x for x in rows if x['account']=='receivable' and x['invoice_id'] in ids and x['payment_id'] is None
                and not left<=datetime.fromisoformat(x['timestamp'])<right]
            require(len(ar)==1,'cutoff proof lacks offending row');amount=abs(ar[0]['debit_minor'])
        else:
            require(len(pay)==1,'proof lacks payment');payment=pay[0]
            fx=[r for table,r in records if table=='fx' and r['date']==payment['timestamp'][:10]
                and r['source_currency']==payment['currency'] and r['target_currency']==base]
            require(len(fx)==1 and policy['fx_date']=='payment_local_date','proof lacks applicable FX rate')
            require(policy['rounding']=='ROUND_HALF_EVEN','unknown rounding rule')
            exps=policy['currency_exponents'];p_rows=[x for x in rows if x['payment_id']==payment['payment_id']]
            if kind=='fx_conversion':
                require(ids=={payment['payment_id']},'FX affected ids mismatch')
                bank=[x for x in p_rows if x['account']=='bank'];require(len(bank)==1,'FX proof lacks bank posting')
                expected=rational_minor(payment['amount_minor'],fx[0]['rate'],exps[payment['currency']],exps[base])
                amount=abs(bank[0]['debit_minor']-expected)
            elif kind=='payment_allocation':
                invoice_ids=ids-{payment['payment_id']};require(len(invoice_ids)==1 and payment['payment_id'] in ids,'allocation affected ids mismatch')
                invoice_id=next(iter(invoice_ids))
                refs=[x for x in payment['invoice_refs'] if x['invoice_id']==invoice_id];require(refs,'allocation proof has no reference')
                ar=[x for x in p_rows if x['account']=='receivable' and x['invoice_id']==invoice_id]
                require(ar,'allocation proof lacks observed allocation')
                expected=rational_minor(sum(x['amount_minor'] for x in refs),fx[0]['rate'],exps[payment['currency']],exps[base])
                delta=expected-sum(x['credit_minor'] for x in ar)
                require(sum(x['credit_minor'] for x in p_rows if x['account']=='unallocated' and x['invoice_id']==invoice_id)==delta,'allocation proof lacks counterposting')
                amount=abs(delta)
            else:raise ValueError('unknown issue type')
    require(amount>0 and amount==issue['expected_amount_minor'] and issue['currency']==base,'proof does not establish claimed amount')
    require(all(x['currency']==base for x in rows),'proof posting currency mismatch')

def validate_episode(episode):
    tables=episode['tables'];manifest=episode['manifest'];truth=episode['truth']
    validate_clean(episode['provenance']['clean'],manifest)
    require(sum(x['debit_minor']-x['credit_minor'] for x in tables['ledger'])==0,'unbalanced mutated ledger')
    public=public_discrepancies(tables,manifest)
    require(Counter(map(signature,public))==Counter(map(signature,truth)),'public discrepancies differ from causal truth')
    lookup={}
    for table,records in tables.items():
        for record in records:
            key=record.get('row_id',record.get('doc_id'))
            require(key not in lookup,'duplicate observable record id');lookup[key]=(table,record)
    for issue in truth:
        require(issue['evidence_sets'],'no valid evidence sets')
        for evidence in issue['evidence_sets']:
            require(len(evidence)<=10 and all(k in lookup for k in evidence),'inaccessible proof')
            prove(issue,[lookup[k] for k in evidence],manifest)
    return {'issues_checked':len(truth),'evidence_sets_checked':sum(len(x['evidence_sets']) for x in truth),'method':'Fraction + per-proof reconstruction'}
