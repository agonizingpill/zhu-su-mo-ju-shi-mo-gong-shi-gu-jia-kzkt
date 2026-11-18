from fastapi import APIRouter, Depends
from sqlalchemy import select
from .db import db_session
from .auth import current, writers, admin
from .drift_models import DriftConfig, DriftRun, DriftAlarm
from .drift_schemas import DriftConfigInput, RunTrigger, AlarmRelease
from . import drift_service as svc

router = APIRouter()


@router.get('/drift/overview')
def overview(s=Depends(db_session), u=Depends(current)):
    return svc.overview(s)


@router.get('/drift/pending')
def pending(s=Depends(db_session), u=Depends(current)):
    return svc.pending_view(s)


@router.get('/drift/alarms')
def alarms(s=Depends(db_session), u=Depends(current)):
    return [svc.alarm_view(s, x) for x in
            s.scalars(select(DriftAlarm).order_by(DriftAlarm.id.desc()).limit(100))]


@router.get('/drift/runs')
def runs(machine: str | None = None, s=Depends(db_session), u=Depends(current)):
    return svc.list_runs(s, machine)


@router.get('/drift/runs/{run_id}')
def run_detail(run_id: int, s=Depends(db_session), u=Depends(current)):
    run = s.get(DriftRun, run_id)
    if run is None:
        from fastapi import HTTPException
        raise HTTPException(404, '检测轮次不存在')
    return svc.run_view(s, run, with_steps=True)


@router.get('/drift/config')
def config(s=Depends(db_session), u=Depends(current)):
    return svc.config_snapshot(svc.get_config(s))


@router.post('/drift/runs')
def trigger(p: RunTrigger, s=Depends(db_session), u=Depends(writers)):
    machines = [p.machine] if p.machine else None
    ids = svc.request_runs(s, machines, triggered_by='manual')
    s.commit()
    svc.dispatch(ids)
    return {'queued_run_ids': ids}


@router.patch('/drift/config')
def update_config(p: DriftConfigInput, s=Depends(db_session), u=Depends(admin)):
    cfg, ids = svc.update_config(s, p, u)
    s.commit()
    svc.dispatch(ids)
    return svc.config_snapshot(cfg)


@router.post('/drift/alarms/{alarm_id}/release')
def release(alarm_id: int, p: AlarmRelease, s=Depends(db_session), u=Depends(admin)):
    alarm = svc.release_alarm(s, alarm_id, p, u)
    s.commit()
    return svc.alarm_view(s, alarm)
