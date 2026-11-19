import copy
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN

TYPES = ('duplicate_invoice', 'payment_allocation', 'fx_conversion', 'period_cutoff')
VERSION = '1.1.0'

def convert(amount, rate, source_exponent=2, target_exponent=2):
    return int((Decimal(amount) * Decimal(rate) * Decimal(10)**(target_exponent-source_exponent)).quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))

def generate(seed, config=None):
    c = {'invoices': 60, 'errors': dict.fromkeys(TYPES, 1), 'interaction': None,
         'timezone': '+05:00', 'transform': 0, 'payment_delay_days': 5, **(config or {})}
    if set(c)-{'invoices','errors','interaction','timezone','transform','payment_delay_days'}:raise ValueError('unknown config field')
    if type(c['payment_delay_days']) is not int or not 0<=c['payment_delay_days']<=7:raise ValueError('invalid payment delay')
    n = c['invoices']
    if type(n) is not int or not 50 <= n <= 200:
        raise ValueError('invoices must be 50..200')
    if set(c['errors']) - set(TYPES) or any(type(x) is not int or x < 0 or x > 3 for x in c['errors'].values()):
        raise ValueError('invalid error counts')
    if c['interaction'] not in (None, 'allocation_cutoff', 'fx_cutoff'):
        raise ValueError('invalid interaction')
    if c['timezone'] not in ('+05:00', '+00:00', '-04:00', '+09:00'):
        raise ValueError('invalid timezone')
    r = random.Random(seed)
    used = set()
    def uid(prefix):
        while True:
            v = prefix + '-' + format(r.getrandbits(48), '012x')
            if v not in used:
                used.add(v)
                return v
    tz = timezone(timedelta(hours=int(c['timezone'][:3])))
    start = datetime(2026, 10, 1, tzinfo=tz)
    end = datetime(2026, 11, 1, tzinfo=tz)
    identity=json.dumps({k:v for k,v in c.items() if k!='transform'},sort_keys=True)
    ep = 'ep-' + hashlib.sha256(f'{seed}:{VERSION}:{identity}'.encode()).hexdigest()[:16]
    exponents={'KZT':2,'USD':2,'EUR':2,'JPY':0,'KWD':3}
    policy = {'doc_id': 'policy', 'fx_date': 'payment_local_date', 'period_rule': 'service_at',
              'period_interval': '[start,end)', 'rounding': 'ROUND_HALF_EVEN',
              'amount_tolerance_minor': 0, 'currency_exponents': exponents,
              'settlement_rule':'remeasure receivable at payment-date FX before settling; final residual to fx_revaluation',
              'allocation_rule': 'sum signed invoice_refs per payment; no guessing null refs'}
    fx = []
    for day in range(39):
        for cur, base in [('USD',480),('EUR',520),('KZT',1),('JPY',3),('KWD',1550)]:
            fx.append({'row_id': uid('fx'), 'date': (start+timedelta(days=day)).date().isoformat(),
                       'source_currency': cur, 'target_currency': 'KZT',
                       'rate': str(Decimal(base) + (Decimal(day%5)/10 if cur != 'KZT' else 0))})
    ledger, invoices, payments, cases = [], [], [], []
    def post(ts, account, debit, credit, inv, pay=None):
        row = {'row_id': uid('row'), 'timestamp': ts, 'account': account,
               'debit_minor': debit, 'credit_minor': credit, 'currency': 'KZT',
               'entity_id': inv['entity_id'], 'invoice_id': inv['invoice_id'], 'payment_id': pay}
        ledger.append(row)
        return row
    for i in range(n):
        # Equal recurring amounts, split settlements and signed credit notes are valid distractions.
        cur = ['KZT', 'USD', 'EUR','JPY','KWD'][i%5]
        amount = (10000 if i%7 == 0 else r.randint(2000, 90000)) * (-1 if i%11 == 0 else 1)
        dt = start+timedelta(days=30 if i < 12 else r.randrange(28), hours=23 if i < 12 else r.randrange(20), minutes=r.randrange(60))
        inv = {'doc_id': uid('doc'), 'invoice_id': uid('inv'), 'entity_id': uid('entity'),
               'supplier_name': r.choice(['Alma', 'Orion', 'Steppe', 'Turan'])+' Synthetic Ltd',
               'issued_at': dt.isoformat(), 'service_at': dt.isoformat(),
               'due_at': (dt+timedelta(days=10)).isoformat(), 'currency': cur,
               'total_minor': amount, 'line_items': [{'description': 'Synthetic services / credit note', 'total_minor': amount}]}
        invoices.append(inv)
        rate = next(x for x in fx if x['date']==dt.date().isoformat() and x['source_currency']==cur)
        gross = convert(amount, rate['rate'],exponents[cur])
        sale = [post(dt.isoformat(), 'receivable', gross, 0, inv), post(dt.isoformat(), 'revenue', 0, gross, inv)]
        parts = [amount//2, amount-amount//2] if i%5==0 else [amount]
        pays=[]
        for part_index,part in enumerate(parts):
            paid_at=dt+timedelta(days=r.randrange(c['payment_delay_days']+1))
            payment_rate=next(x for x in fx if x['date']==paid_at.date().isoformat() and x['source_currency']==cur)
            p = {'doc_id': uid('doc'), 'payment_id': uid('pay'), 'timestamp': paid_at.isoformat(),
                 'currency': cur, 'amount_minor': part,
                 'invoice_refs': [{'invoice_id': inv['invoice_id'], 'amount_minor': part}]}
            payments.append(p)
            val=convert(part,payment_rate['rate'],exponents[cur])
            bank=post(paid_at.isoformat(),'bank',val,0,inv,p['payment_id'])
            ar=post(paid_at.isoformat(),'receivable',0,val,inv,p['payment_id'])
            # Allocate the invoice carrying amount exactly, including split rounding residual.
            carrying=convert(part,rate['rate'],exponents[cur]) if part_index<len(parts)-1 else gross-sum(convert(x,rate['rate'],exponents[cur]) for x in parts[:-1])
            delta=val-carrying
            if delta:
                post(paid_at.isoformat(),'receivable',delta,0,inv,p['payment_id'])
                post(paid_at.isoformat(),'fx_revaluation',0,delta,inv,p['payment_id'])
            pays.append((p,bank,ar,payment_rate))
        cases.append((inv,sale,pays))
    # Correct customer advances have explicit null links and legitimately use unallocated.
    # Account names alone must not identify injected errors.
    for _ in range(3):
        dt=start+timedelta(days=r.randrange(28),hours=12)
        amount=r.randint(1000,50000)
        payment={'doc_id':uid('doc'),'payment_id':uid('pay'),'timestamp':dt.isoformat(),
                 'currency':'KZT','amount_minor':amount,
                 'invoice_refs':[{'invoice_id':None,'amount_minor':amount}]}
        payments.append(payment)
        empty={'entity_id':None,'invoice_id':None}
        post(dt.isoformat(),'bank',amount,0,empty,payment['payment_id'])
        post(dt.isoformat(),'unallocated',0,amount,empty,payment['payment_id'])
    clean = copy.deepcopy({'ledger':ledger,'invoices':invoices,'payments':payments,'fx':fx,'policy':[policy]})
    truth, changes = [], []
    selected={}
    pool=list(range(12,n));r.shuffle(pool)
    for kind in TYPES:
        eligible=[i for i in pool if kind!='fx_conversion' or cases[i][0]['currency']!='KZT']
        selected[kind]=eligible[:c['errors'].get(kind,0)]
        pool=[i for i in pool if i not in selected[kind]]
    if c['interaction']:
        kind='payment_allocation' if c['interaction']=='allocation_cutoff' else 'fx_conversion'
        selected[kind]=list(dict.fromkeys([1]+selected[kind]))[:3]
        selected['period_cutoff']=list(dict.fromkeys([1]+selected['period_cutoff']))[:3]
    for kind, indices in selected.items():
        for i in indices:
            inv,sales,pays=cases[i]; p,bank,ar,rate=pays[0]
            before=[];after=[];cause={}
            if kind=='duplicate_invoice':
                duplicates=[]
                for row in sales:
                    new=copy.deepcopy(row);new['row_id']=uid('row');ledger.append(new);duplicates.append(new)
                affected=[inv['invoice_id']]; amount=abs(sales[0]['debit_minor'])
                evidence=[inv['doc_id']]+[x['row_id'] for x in sales+duplicates]
                after=duplicates
            elif kind=='payment_allocation':
                before=[copy.deepcopy(ar)]
                amount=max(1,abs(ar['credit_minor'])//3)
                delta=amount if ar['credit_minor']>0 else -amount
                ar['credit_minor']-=delta
                suspense=post(ar['timestamp'],'unallocated',0,delta,inv,p['payment_id'])
                affected=[p['payment_id'],inv['invoice_id']]
                evidence=[p['doc_id'],ar['row_id'],suspense['row_id'],rate['row_id'],'policy']
                after=[copy.deepcopy(ar),copy.deepcopy(suspense)]
            elif kind=='fx_conversion':
                before=[copy.deepcopy(bank)]
                # Wrong rate/date, not arbitrary numeric noise. Record the causal choice.
                wrong_date=(datetime.fromisoformat(p['timestamp'])-timedelta(days=1)).date().isoformat()
                wrong_rate=next((x['rate'] for x in fx if x['date']==wrong_date and x['source_currency']==p['currency']),str(Decimal(rate['rate'])*Decimal('1.05')))
                wrong_value=convert(p['amount_minor'],wrong_rate,exponents[p['currency']])
                if wrong_value==bank['debit_minor']:
                    wrong_rate=str(Decimal(rate['rate'])*Decimal('1.05'));wrong_value=convert(p['amount_minor'],wrong_rate,exponents[p['currency']])
                delta=wrong_value-bank['debit_minor'];amount=abs(delta)
                bank['debit_minor']=wrong_value
                # A balanced FX posting is still economically wrong.
                fxrow=post(bank['timestamp'],'fx_revaluation',0,delta,inv,p['payment_id'])
                cause={'required_rate':rate['rate'],'used_rate':wrong_rate,'used_date':wrong_date,'required_date':rate['date']}
                affected=[p['payment_id']]
                evidence=[p['doc_id'],bank['row_id'],rate['row_id'],'policy']
                after=[copy.deepcopy(bank),copy.deepcopy(fxrow)]
            else:
                before=copy.deepcopy(sales)
                for row in sales: row['timestamp']=(end+timedelta(hours=1)).isoformat()
                amount=abs(sales[0]['debit_minor']);affected=[inv['invoice_id']]
                evidence=[inv['doc_id'],sales[0]['row_id'],'policy']
                after=copy.deepcopy(sales)
            truth.append({'issue_id':uid('issue'),'type':kind,'affected_ids':sorted(affected),
                          'expected_amount_minor':amount,'currency':'KZT','evidence_sets':[sorted(evidence)]})
            changes.append({'type':kind,'before':before,'after':after,'affected_ids':affected,'cause':cause})
    tables={'ledger':ledger,'invoices':invoices,'payments':payments,'fx':fx,'policy':[policy]}
    # Metamorphic transformation: all identifiers are renamed, lists permuted; no money changes.
    if c['transform']:
        tr=random.Random(c['transform']); ids=set()
        def collect(v,key=''):
            if isinstance(v,dict):
                for k,x in v.items(): collect(x,k)
            elif isinstance(v,list):
                for x in v: collect(x,key)
            elif isinstance(v,str) and (key.endswith('_id') or key.endswith('_ids') or key=='evidence_sets') and v!='policy': ids.add(v)
        collect([tables,truth,changes,clean])
        mapping={v:'id-'+format(tr.getrandbits(64),'016x') for v in sorted(ids)}
        def rewrite(v):
            if isinstance(v,dict):return {k:rewrite(x) for k,x in v.items()}
            if isinstance(v,list):return [rewrite(x) for x in v]
            return mapping.get(v,v) if isinstance(v,str) else v
        tables,truth,changes,clean=rewrite([tables,truth,changes,clean])
        for rows in tables.values():tr.shuffle(rows)
    else:
        for rows in tables.values():r.shuffle(rows)
    manifest={'schema_version':VERSION,'episode_id':ep,'company':'Synthetic '+r.choice(['Alma','Steppe','Orion']),
              'base_currency':'KZT','period_start':start.isoformat(),'period_end':end.isoformat(),
              'timezone':c['timezone'],'record_counts':{k:len(v) for k,v in tables.items()},
              'document_count':len(invoices)+len(payments)+1,'rounding':'ROUND_HALF_EVEN'}
    return {'manifest':manifest,'tables':tables,'truth':truth,
            'provenance':{'version':VERSION,'seed':seed,'config':c,'clean':clean,'changes':changes}}
