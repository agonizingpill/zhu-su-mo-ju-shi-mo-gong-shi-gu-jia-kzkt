"""Sequential underestimate-drift guard.

Detector (one-sided CUSUM on relative hour error, per machine):
  d_i      = (observed_i - frozen_predicted_i) / frozen_predicted_i
  inc_i    = max(0, d_i - k)        # k = k_slack, supervisor-set tolerance
  S_i      = S_{i-1} + inc_i        # never reset inside one run
  alarm    <=> S_i >= h             # h = h_threshold, supervisor-set
  "normal" requires >= min_samples *completed* observations AND S < h.

A still-running trial contributes an elapsed-hours LOWER BOUND: it may push S across h
(underestimate evidence) but is never counted for normality. On completion the next run
replays the whole sequence in original start order, replacing that provisional observation
with the final actual hours at its original position — late reports are replayed in place and
never counted twice. Each step is checkpointed, so an interrupted run resumes the original
sequence and any alarm that should have fired still fires.
"""
import os
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, func

from .db import Session
from .models import Estimate, MachineSlot
from .execution_models import TrialExecution
from .drift_models import DriftConfig, DriftRun, DriftStep, DriftAlarm, DriftMachineGate
from .services import record

QUEUE = 'drift-runs'
MAX_ATTEMPTS = 5
_redis_client = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
    return _redis_client


def dispatch(run_ids):
    """Push queued run ids after the DB transaction has committed."""
    if not run_ids:
        return
    try:
        _redis().rpush(QUEUE, *[str(i) for i in run_ids])
    except Exception:
        logging.exception('drift queue dispatch failed; runs stay queued for manual re-trigger')


def get_config(s):
    """Read supervisor thresholds; return an unsaved default row until the supervisor saves."""
    cfg = s.get(DriftConfig, 1)
    if cfg is None:
        return DriftConfig(id=1, k_slack=0.05, h_threshold=0.30, min_samples=5)
    return cfg


def config_snapshot(cfg):
    return {'k_slack': cfg.k_slack, 'h_threshold': cfg.h_threshold, 'min_samples': cfg.min_samples}


def execution_machine(s, x):
    e = s.get(Estimate, x.estimate_id)
    slot = s.get(MachineSlot, e.slot_id) if e and e.slot_id else None
    if slot:
        return slot.machine
    return x.snapshot.get('machine')


def machines_with_data(s):
    rows = s.scalars(
        select(MachineSlot.machine)
        .join(Estimate, Estimate.slot_id == MachineSlot.id)
        .join(TrialExecution, TrialExecution.estimate_id == Estimate.id)
        .distinct()
    )
    return list(rows)


def _ordered_executions(s, machine):
    return list(s.scalars(
        select(TrialExecution)
        .join(Estimate, TrialExecution.estimate_id == Estimate.id)
        .join(MachineSlot, Estimate.slot_id == MachineSlot.id)
        .where(MachineSlot.machine == machine)
        .order_by(TrialExecution.started_at, TrialExecution.id)
    ))


def build_observations(s, machine, at=None):
    """Frozen prediction vs observed hours, in 开工顺序. Running rows use elapsed lower bound."""
    at = at or datetime.now(timezone.utc)
    out = []
    for x in _ordered_executions(s, machine):
        predicted = float(x.snapshot['predicted_hours'])
        if x.actual_hours is not None:
            kind, observed = 'actual', float(x.actual_hours)
        else:
            elapsed = (at - datetime.fromisoformat(x.started_at)).total_seconds() / 3600
            kind, observed = 'lower_bound', max(0.0, round(elapsed, 4))
        out.append({'execution_id': x.id, 'estimate_code': x.snapshot['estimate_code'],
                    'started_at': x.started_at, 'obs_kind': kind,
                    'observed_hours': round(observed, 4), 'predicted_hours': round(predicted, 4),
                    'status': x.status})
    return out


def request_runs(s, machines=None, triggered_by='auto'):
    """Create queued run rows (one per machine). Caller commits, then dispatches the ids."""
    cfg = get_config(s)
    targets = machines if machines else machines_with_data(s)
    ids = []
    for machine in targets:
        prev = s.scalar(select(func.max(DriftRun.seq)).where(DriftRun.machine == machine)) or 0
        run = DriftRun(machine=machine, seq=prev + 1, status='queued',
                       config_snapshot=config_snapshot(cfg), total_observations=0,
                       processed_observations=0, final_decision='pending',
                       triggered_by=triggered_by, created_at=now_iso(), updated_at=now_iso())
        s.add(run)
        s.flush()
        ids.append(run.id)
    return ids


def ensure_gate(s, machine):
    gate = s.scalar(select(DriftMachineGate).where(DriftMachineGate.machine == machine))
    if gate is None:
        gate = DriftMachineGate(machine=machine, state='open', updated_at=now_iso())
        s.add(gate)
        s.flush()
    return gate


def reset_stuck_runs(s):
    """Worker boot recovery: a run left 'running' by a crashed worker is requeued; its
    checkpointed steps mean replay resumes instead of double counting."""
    ids = []
    for run in s.scalars(select(DriftRun).where(DriftRun.status.in_(['queued', 'running']))):
        if run.status == 'running':
            run.status = 'queued'
            run.updated_at = now_iso()
        ids.append(run.id)
    s.commit()
    return ids


def requeue_or_fail(run_id):
    """Bounded retry after an in-flight failure: resume from checkpoints until attempts run out."""
    with Session() as s:
        run = s.get(DriftRun, run_id)
        if run is None or run.status not in ('queued', 'running'):
            return
        if run.attempts >= MAX_ATTEMPTS:
            run.status = 'failed'
            run.message = f'连续 {run.attempts} 次检测中断，已停止重试；可在页面手动重新发起检测'
            run.updated_at = now_iso()
            s.commit()
            return
        run.status = 'queued'
        run.updated_at = now_iso()
        s.commit()
        rid = run.id
    dispatch([rid])


def process_run(s, run_id):
    run = s.scalar(select(DriftRun).where(DriftRun.id == run_id).with_for_update())
    if run is None or run.status not in ('queued', 'running'):
        return
    if run.status == 'queued':
        run.status = 'running'
        run.attempts = (run.attempts or 0) + 1
        run.updated_at = now_iso()
        s.commit()
    cfg = run.config_snapshot
    k, h, min_samples = float(cfg['k_slack']), float(cfg['h_threshold']), int(cfg['min_samples'])

    existing = {st.position: st for st in s.scalars(select(DriftStep).where(DriftStep.run_id == run.id))}
    observations = build_observations(s, run.machine)
    run.total_observations = len(observations)

    stat, normal_count = 0.0, 0
    for position, obs in enumerate(observations, start=1):
        if position in existing:  # resume from checkpointed original sequence
            step = existing[position]
            stat = step.cusum_stat
            if step.counted_for_normal:
                normal_count += 1
            run.processed_observations = max(run.processed_observations, position)
            continue
        deviation = round((obs['observed_hours'] - obs['predicted_hours']) / obs['predicted_hours'], 6)
        increment = round(max(0.0, deviation - k), 6)
        stat = round(stat + increment, 6)
        counted = obs['obs_kind'] == 'actual'
        if counted:
            normal_count += 1
        if obs['obs_kind'] == 'lower_bound':
            note = '未完工：以已耗时下界计入，仅作低估证据，不计入正常样本'
        elif stat >= h:
            note = f'CUSUM 统计量 {stat} 越过阈值 {h}'
        else:
            note = '完工实绩替换观测，计入正常样本'
        step = DriftStep(run_id=run.id, position=position, execution_id=obs['execution_id'],
                         estimate_code=obs['estimate_code'], started_at=obs['started_at'],
                         obs_kind=obs['obs_kind'], observed_hours=obs['observed_hours'],
                         predicted_hours=obs['predicted_hours'], deviation=deviation,
                         increment=increment, cusum_stat=stat,
                         counted_for_normal=1 if counted else 0, note=note)
        s.add(step)
        run.processed_observations = position
        run.updated_at = now_iso()
        s.commit()  # durable checkpoint: interruption resumes from the next position

    steps = list(s.scalars(select(DriftStep).where(DriftStep.run_id == run.id).order_by(DriftStep.position)))
    if stat >= h:
        decision, message = 'alarm', f'CUSUM 统计量 {stat} 已越过主管阈值 {h}，判定工时低估漂移'
    elif normal_count >= min_samples:
        decision, message = 'normal', f'{normal_count} 个完工样本，统计量 {stat} 低于阈值 {h}，未发现低估漂移'
    else:
        decision, message = 'insufficient', f'仅 {normal_count} 个完工样本（需 {min_samples}），样本不足，不宣称正常'
    run.final_stat = stat
    run.final_decision = decision
    run.message = message
    run.status = 'done'
    run.updated_at = now_iso()
    if decision == 'alarm':
        _reconcile_alarm(s, run, steps, normal_count)
    s.commit()


def _step_brief(step):
    return {'position': step.position, 'estimate_code': step.estimate_code,
            'obs_kind': step.obs_kind, 'observed_hours': step.observed_hours,
            'predicted_hours': step.predicted_hours, 'deviation': step.deviation,
            'increment': step.increment, 'cusum_stat': step.cusum_stat,
            'counted_for_normal': bool(step.counted_for_normal), 'note': step.note}


def _reconcile_alarm(s, run, steps, normal_count):
    crossing = next((st for st in steps if st.cusum_stat >= run.config_snapshot['h_threshold']), steps[-1])
    evidence = [_step_brief(st) for st in steps if st.position >= max(1, crossing.position - 2)]
    gate = ensure_gate(s, run.machine)
    active = s.scalar(select(DriftAlarm).where(DriftAlarm.machine == run.machine, DriftAlarm.state == 'active'))
    if active:
        active.latest_run_id = run.id
        active.latest_step_id = crossing.id
        active.statistic = run.final_stat
        active.normal_count = normal_count
        active.detail = run.message
        active.evidence = evidence
        active.updated_at = now_iso()
        gate.state = 'paused'
        gate.active_alarm_id = active.id
        gate.updated_at = now_iso()
        return
    # A fresh anomaly invalidates every prior release for this machine.
    for old in s.scalars(select(DriftAlarm).where(DriftAlarm.machine == run.machine,
                                                  DriftAlarm.state == 'released')):
        old.state = 'superseded'
        old.updated_at = now_iso()
    alarm = DriftAlarm(machine=run.machine, state='active', first_run_id=run.id, latest_run_id=run.id,
                       first_step_id=crossing.id, latest_step_id=crossing.id,
                       statistic=run.final_stat, threshold=run.config_snapshot['h_threshold'],
                       min_samples=run.config_snapshot['min_samples'], normal_count=normal_count,
                       detail=run.message, evidence=evidence, raised_at=now_iso(), updated_at=now_iso())
    s.add(alarm)
    s.flush()
    gate.state = 'paused'
    gate.active_alarm_id = alarm.id
    gate.updated_at = now_iso()


def assert_gate_open(s, machine):
    """Commitment guard: alarm and pause move together; existing commitments are untouched."""
    gate = s.scalar(select(DriftMachineGate).where(DriftMachineGate.machine == machine))
    if gate and gate.state == 'paused':
        alarm = s.get(DriftAlarm, gate.active_alarm_id) if gate.active_alarm_id else None
        detail = alarm.detail if alarm else '存在未解除的工时低估漂移报警'
        raise HTTPException(409, f'{machine} 工时低估漂移报警未解除，已暂停新机时承诺：{detail}')


def release_alarm(s, alarm_id, data, u):
    alarm = s.scalar(select(DriftAlarm).where(DriftAlarm.id == alarm_id).with_for_update())
    if alarm is None:
        raise HTTPException(404, '漂移报警不存在')
    if alarm.state != 'active':
        raise HTTPException(409, '该报警已解除或已被新异常作废，不能重复解除')
    busy = s.scalar(select(DriftRun.id).where(DriftRun.machine == alarm.machine,
                                              DriftRun.status.in_(['queued', 'running'])))
    if busy:
        raise HTTPException(409, '该机台序贯检测尚未跑完，请等待最新检测证据后再解除')
    latest = s.get(DriftRun, alarm.latest_run_id)
    cfg = get_config(s)
    if config_snapshot(cfg) != latest.config_snapshot:
        raise HTTPException(409, '主管阈值已调整，最新证据基于旧阈值，请先重新发起检测')
    if latest.status != 'done' or latest.final_decision == 'alarm' or latest.final_stat >= cfg.h_threshold:
        raise HTTPException(409, '最新检测证据仍指向低估漂移，主管不能解除该报警')
    gate = s.scalar(select(DriftMachineGate).where(DriftMachineGate.machine == alarm.machine)
                    .with_for_update())
    normal_count = s.scalar(select(func.count()).select_from(DriftStep)
                            .where(DriftStep.run_id == latest.id, DriftStep.counted_for_normal == 1))
    evidence = {'run_id': latest.id, 'final_stat': latest.final_stat,
                'h_threshold': cfg.h_threshold, 'k_slack': cfg.k_slack,
                'min_samples': cfg.min_samples, 'normal_count': normal_count,
                'decision': latest.final_decision}
    alarm.state = 'released'
    alarm.released_by = u.email
    alarm.released_at = now_iso()
    alarm.release_reason = data.reason
    alarm.release_evidence = evidence
    alarm.updated_at = now_iso()
    if gate:
        gate.state = 'open'
        gate.active_alarm_id = None
        gate.updated_at = now_iso()
    record(s, u, f'解除漂移报警 {alarm.machine} #{alarm.id}')
    return alarm


def update_config(s, data, u):
    cfg = s.get(DriftConfig, 1)
    if cfg is None:
        cfg = DriftConfig(id=1)
        s.add(cfg)
    cfg.k_slack = data.k_slack
    cfg.h_threshold = data.h_threshold
    cfg.min_samples = data.min_samples
    cfg.updated_by = u.email
    s.flush()
    ids = request_runs(s, triggered_by='config')
    record(s, u, f'主管调整漂移阈值 k={cfg.k_slack} h={cfg.h_threshold} 最小样本={cfg.min_samples}')
    return cfg, ids


# ---------- read models ----------

def _gate_machines(s):
    machines = set(machines_with_data(s))
    machines.update(x for x in s.scalars(select(DriftMachineGate.machine)))
    return sorted(machines)


def run_view(s, run, with_steps=False):
    d = {c.name: getattr(run, c.name) for c in run.__table__.columns}
    if with_steps:
        d['steps'] = [_step_brief(st) for st in
                      s.scalars(select(DriftStep).where(DriftStep.run_id == run.id).order_by(DriftStep.position))]
    return d


def alarm_view(s, alarm):
    d = {c.name: getattr(alarm, c.name) for c in alarm.__table__.columns}
    d['gate_state'] = None
    gate = s.scalar(select(DriftMachineGate).where(DriftMachineGate.machine == alarm.machine))
    if gate:
        d['gate_state'] = gate.state
    return d


def overview(s):
    cards = []
    for machine in _gate_machines(s):
        gate = s.scalar(select(DriftMachineGate).where(DriftMachineGate.machine == machine))
        active = s.scalar(select(DriftAlarm).where(DriftAlarm.machine == machine,
                                                   DriftAlarm.state == 'active').order_by(DriftAlarm.id.desc()))
        latest = s.scalar(select(DriftRun).where(DriftRun.machine == machine).order_by(DriftRun.seq.desc()))
        observations = build_observations(s, machine)
        pending = [o for o in observations if o['obs_kind'] == 'lower_bound']
        cards.append({'machine': machine,
                      'gate_state': gate.state if gate else 'open',
                      'active_alarm': alarm_view(s, active) if active else None,
                      'latest_run': run_view(s, latest) if latest else None,
                      'total_observations': len(observations),
                      'pending_lower_bounds': pending,
                      'completed_observations': sum(1 for o in observations if o['obs_kind'] == 'actual')})
    return {'config': config_snapshot(get_config(s)), 'machines': cards}


def pending_view(s):
    rows = []
    for machine in _gate_machines(s):
        for obs in build_observations(s, machine):
            if obs['obs_kind'] == 'lower_bound':
                rows.append({'machine': machine, **obs,
                             'lower_deviation': round((obs['observed_hours'] - obs['predicted_hours'])
                                                      / obs['predicted_hours'], 6)})
    return rows


def list_runs(s, machine=None):
    stmt = select(DriftRun).order_by(DriftRun.id.desc())
    if machine:
        stmt = stmt.where(DriftRun.machine == machine)
    return [run_view(s, r) for r in s.scalars(stmt).limit(100)]
