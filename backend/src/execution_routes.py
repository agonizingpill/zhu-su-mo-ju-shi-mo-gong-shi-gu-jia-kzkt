from fastapi import APIRouter,Depends
from sqlalchemy import select
from .db import db_session
from .auth import current,writers,admin
from .execution_models import TrialExecution,CostReview
from .execution_schemas import StartTrial,FinishTrial,ReviewInput,ReviseReview
from . import execution_service as svc
from . import drift_service as drift
router=APIRouter()
@router.get('/executions')
def executions(s=Depends(db_session),u=Depends(current)):return [svc.view(s,x)for x in s.scalars(select(TrialExecution).order_by(TrialExecution.id.desc()))]
@router.post('/executions')
def start(p:StartTrial,s=Depends(db_session),u=Depends(writers)):
    x=svc.begin(s,p,u)
    machine=drift.execution_machine(s,x)
    ids=drift.request_runs(s,[machine],triggered_by='start')
    s.commit();drift.dispatch(ids);return svc.view(s,x)
@router.post('/executions/{eid}/finish')
def finish(eid:int,p:FinishTrial,s=Depends(db_session),u=Depends(writers)):
    x=svc.finish(s,eid,p,u)
    machine=drift.execution_machine(s,x)
    # 完工替换原下界观测：整段序列按开工顺序重放，迟到的完工回放到原位，不重复计数。
    ids=drift.request_runs(s,[machine],triggered_by='finish')
    s.commit();drift.dispatch(ids);return svc.view(s,x)
@router.get('/cost-reviews')
def reviews(s=Depends(db_session),u=Depends(current)):return [svc.view(s,x)for x in s.scalars(select(CostReview).order_by(CostReview.id.desc()))]
@router.post('/cost-reviews')
def prepare(p:ReviewInput,s=Depends(db_session),u=Depends(writers)):
    x=svc.prepare_review(s,p,u);s.commit();return svc.view(s,x)
@router.patch('/cost-reviews/{rid}')
def revise(rid:int,p:ReviseReview,s=Depends(db_session),u=Depends(writers)):
    x=svc.revise(s,rid,p,u);s.commit();return svc.view(s,x)
@router.post('/cost-reviews/{rid}/sign')
def sign(rid:int,s=Depends(db_session),u=Depends(admin)):
    x=svc.sign(s,rid,u);s.commit();return svc.view(s,x)
