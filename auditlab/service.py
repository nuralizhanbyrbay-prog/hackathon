"""Evaluator-owned service. Only bounded JSON requests cross the process boundary."""
import ast
import copy
import json
import re
import sys
import time
from decimal import Decimal, localcontext
from .generator import generate
from .scoring import score

def calculate(expression):
    if not isinstance(expression,str) or len(expression)>200 or not re.fullmatch(r'[\d. +*/()\-]+',expression):
        raise ValueError()
    tree=ast.parse(expression,mode='eval')
    if sum(1 for _ in ast.walk(tree))>80:raise ValueError()
    def visit(n):
        if isinstance(n,ast.Expression):return visit(n.body)
        if isinstance(n,ast.Constant) and type(n.value) in (int,float):
            return Decimal(ast.get_source_segment(expression,n))
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            v=visit(n.operand);return v if isinstance(n.op,ast.UAdd) else -v
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=visit(n.left),visit(n.right)
            if isinstance(n.op,ast.Add):return a+b
            if isinstance(n.op,ast.Sub):return a-b
            if isinstance(n.op,ast.Mult):return a*b
            return a/b
        raise ValueError()
    with localcontext() as ctx:
        ctx.prec=40
        result=visit(tree)
        if not result.is_finite() or abs(result)>Decimal('1e100'):raise ValueError()
        return str(result)

class Session:
    def __init__(self):self.done=True;self.episode=None;self.report=None
    def reset(self,seed,config=None,mode='train',max_steps=100,timeout=60):
        if mode not in ('train','evaluation'):raise ValueError('invalid mode')
        if type(max_steps) is not int or not 1<=max_steps<=100:raise ValueError('invalid step budget')
        if not isinstance(timeout,(int,float)) or not 0<timeout<=60:raise ValueError('invalid timeout')
        self.episode=generate(seed,config);self.mode=mode;self.limit=max_steps;self.timeout=timeout
        self.steps=0;self.done=False;self.started=time.monotonic();self.report=None
        e=self.episode
        obs={'task':'Audit this synthetic company. Submit evidence-backed discrepancies.',
             'manifest':e['manifest'],'policy':e['tables']['policy'][0], 'remaining_steps':self.limit}
        return copy.deepcopy(obs),{'schema_version':'1.0.0'}
    def step(self,action):
        if self.done:return {'error':'episode_finished','remaining_steps':0},0.,True,False,{}
        self.steps+=1;remaining=self.limit-self.steps
        if time.monotonic()-self.started>=self.timeout:
            return self.finish({'episode_id':self.episode['manifest']['episode_id'],'findings':[]},True)
        try:
            if not isinstance(action,dict) or not isinstance(action.get('action'),str):raise ValueError()
            name=action['action']
            if name=='submit':
                return self.finish(action.get('findings') if set(action)=={'action','findings'} else None,False)
            tables=self.episode['tables']
            if name=='list_records':
                if set(action)-{'action','table','cursor','limit'}:raise ValueError()
                rows=tables[action['table']];limit=action.get('limit',50);cursor=action.get('cursor',0)
                if type(limit) is not int or not 1<=limit<=50 or type(cursor) is not int or not 0<=cursor<=len(rows):raise ValueError()
                result={'records':rows[cursor:cursor+limit],'next_cursor':cursor+limit if cursor+limit<len(rows) else None}
            elif name=='get_record':
                if set(action)!={'action','table','id'}:raise ValueError()
                rows=tables[action['table']]
                result=next(x for x in rows if action['id']==x.get('row_id',x.get('doc_id')))
            elif name=='get_document':
                if set(action)!={'action','doc_id'}:raise ValueError()
                result=next(x for k in ('invoices','payments','policy') for x in tables[k] if x['doc_id']==action['doc_id'])
            elif name=='calculator':
                if set(action)!={'action','expression'}:raise ValueError()
                result={'value':calculate(action['expression'])}
            else:raise ValueError()
            obs={'result':copy.deepcopy(result),'remaining_steps':remaining}
        except (KeyError,ValueError,TypeError,StopIteration,ArithmeticError,SyntaxError,RecursionError):
            obs={'error':'invalid_request','remaining_steps':remaining}
        if remaining==0:return self.finish({'episode_id':self.episode['manifest']['episode_id'],'findings':[]},True)
        return obs,0.,False,False,{}
    def finish(self,answer,truncated):
        self.done=True
        inspection=self.steps if truncated else self.steps-1
        self.report=score(answer,self.episode['manifest']['episode_id'],self.episode['truth'],inspection)
        self.report.update(inspection_steps=inspection,steps=self.steps,seconds=time.monotonic()-self.started,
                           truncated=truncated,answer=copy.deepcopy(answer))
        reward=self.report['reward'] if self.mode=='train' else 0.
        return {'status':'truncated' if truncated else 'submitted','remaining_steps':self.limit-self.steps},reward,not truncated,truncated,{}

def main():
    session=Session()
    for line in sys.stdin:
        try:
            if len(line)>200000:raise ValueError()
            req=json.loads(line)
            if req['op']=='reset':result=session.reset(**req['args'])
            elif req['op']=='step':result=session.step(req['action'])
            elif req['op']=='report' and session.done:result=session.report
            else:raise ValueError()
            print(json.dumps({'ok':True,'value':result},ensure_ascii=True),flush=True)
        except Exception:
            print(json.dumps({'ok':False,'error':'invalid_request'}),flush=True)

if __name__=='__main__':main()
