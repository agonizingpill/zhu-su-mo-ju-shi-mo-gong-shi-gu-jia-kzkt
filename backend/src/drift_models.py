"""Drift guard domain: CUSUM underestimate detection over the frozen-prediction / actual-hours sequence.

Observations are TrialExecution rows ordered by start time (开工顺序). Every detection run
replays the *same* ordered sequence; a running trial contributes an elapsed-hours lower bound
that can only raise the underestimate statistic, it is never used as evidence of normality.
Late completion replaces the provisional lower-bound observation at its original position on
the next run, so nothing is counted twice.
"""
from sqlalchemy import String, Float, Integer, ForeignKey, JSON, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class DriftConfig(Base):
    """Singleton row (id=1) holding supervisor-set CUSUM thresholds."""
    __tablename__ = 'drift_config'
    __table_args__ = (CheckConstraint('k_slack >= 0 AND h_threshold > 0 AND min_samples >= 2'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    k_slack: Mapped[float] = mapped_column(Float, default=0.05)
    h_threshold: Mapped[float] = mapped_column(Float, default=0.30)
    min_samples: Mapped[int] = mapped_column(Integer, default=5)
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)


class DriftRun(Base):
    """One sequential-detection replay for one machine. Steps are checkpointed so an
    interrupted run can resume from the original observation sequence without re-counting."""
    __tablename__ = 'drift_runs'
    __table_args__ = (UniqueConstraint('machine', 'seq'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    machine: Mapped[str] = mapped_column(String(40), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='queued')  # queued/running/done/failed
    config_snapshot: Mapped[dict] = mapped_column(JSON)
    total_observations: Mapped[int] = mapped_column(Integer, default=0)
    processed_observations: Mapped[int] = mapped_column(Integer, default=0)
    final_stat: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_decision: Mapped[str] = mapped_column(String(20), default='pending')  # normal/alarm/insufficient/pending
    message: Mapped[str] = mapped_column(String(600), default='')
    triggered_by: Mapped[str] = mapped_column(String(40), default='auto')  # auto/manual/resume
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class DriftStep(Base):
    """Per-observation evidence: exactly what the detector computed at each sequence position."""
    __tablename__ = 'drift_steps'
    __table_args__ = (UniqueConstraint('run_id', 'position'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('drift_runs.id'), index=True)
    position: Mapped[int] = mapped_column(Integer)
    execution_id: Mapped[int] = mapped_column(ForeignKey('trial_executions.id'))
    estimate_code: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[str] = mapped_column(String(40))
    obs_kind: Mapped[str] = mapped_column(String(20))  # actual / lower_bound
    observed_hours: Mapped[float] = mapped_column(Float)
    predicted_hours: Mapped[float] = mapped_column(Float)
    deviation: Mapped[float] = mapped_column(Float)
    increment: Mapped[float] = mapped_column(Float)
    cusum_stat: Mapped[float] = mapped_column(Float)
    counted_for_normal: Mapped[bool] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(300), default='')


class DriftAlarm(Base):
    __tablename__ = 'drift_alarms'
    __table_args__ = (CheckConstraint("state in ('active','released','superseded')"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    machine: Mapped[str] = mapped_column(String(40), index=True)
    state: Mapped[str] = mapped_column(String(20), default='active')
    first_run_id: Mapped[int] = mapped_column(ForeignKey('drift_runs.id'))
    latest_run_id: Mapped[int] = mapped_column(ForeignKey('drift_runs.id'))
    first_step_id: Mapped[int] = mapped_column(ForeignKey('drift_steps.id'))
    latest_step_id: Mapped[int | None] = mapped_column(ForeignKey('drift_steps.id'), nullable=True)
    statistic: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    min_samples: Mapped[int] = mapped_column(Integer)
    normal_count: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(String(600), default='')
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    raised_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    released_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    released_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(600), nullable=True)
    release_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DriftMachineGate(Base):
    """Per-machine commitment gate. Paused => no new machine-time commitments are accepted.
    Existing commitments stay untouched; alarm and pause always move together."""
    __tablename__ = 'drift_machine_gates'
    id: Mapped[int] = mapped_column(primary_key=True)
    machine: Mapped[str] = mapped_column(String(40), unique=True)
    state: Mapped[str] = mapped_column(String(20), default='open')  # open/paused
    active_alarm_id: Mapped[int | None] = mapped_column(ForeignKey('drift_alarms.id'), nullable=True)
    updated_at: Mapped[str] = mapped_column(String(40))
