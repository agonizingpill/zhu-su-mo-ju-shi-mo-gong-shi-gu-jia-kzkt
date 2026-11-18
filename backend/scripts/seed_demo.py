import random
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from src.db import Base,engine,Session
from src.models import User,Mould,MachineSlot,Estimate,TrialHistory
from src.execution_models import TrialExecution,CostReview
from src.workshop_models import MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery
from src.auth import hash_password
from src import drift_service

def seed():
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('admin','estimator','viewer'):
            if not s.scalar(select(User).where(User.email==role+'@ruimo.demo')): s.add(User(email=role+'@ruimo.demo',role=role,password_hash=hash_password('Mould2026!')))
        for code,name,cav,slides,steel,kg in [('MD-86','八腔医疗接头模',8,4,'S136',860),('MD-42','双腔仪表外壳模',2,2,'P20',420),('MD-115','十六腔盖件模',16,6,'H13',1150)]:
            if not s.scalar(select(Mould).where(Mould.code==code)): s.add(Mould(code=code,name=name,cavities=cav,slides=slides,steel=steel,weight_kg=kg))
        s.flush()
        for machine,start,cap in [('M280','2026-11-18 08:00',48),('M280','2026-11-19 08:00',4),('M450','2026-11-18 08:00',72)]:
            if not s.scalar(select(MachineSlot).where(MachineSlot.machine==machine,MachineSlot.start_hour==start)): s.add(MachineSlot(machine=machine,start_hour=start,capacity_hours=cap,reserved_hours=0))
        rng=random.Random(42)
        for i in range(160):
            code=f'TR-{i+1:03d}'
            if s.scalar(select(TrialHistory).where(TrialHistory.trial_code==code)):continue
            cav=rng.choice([2,4,8,16]);sl=rng.randint(0,8);kg=rng.uniform(180,1600);steel=rng.choice(['P20','H13','S136'])
            hours=2+cav*.3+sl*.65+kg*.004+cav*sl*.035+(['P20','H13','S136'].index(steel))*.8+rng.gauss(0,.4)
            s.add(TrialHistory(trial_code=code,cavities=cav,slides=sl,steel=steel,weight_kg=round(kg,1),actual_hours=round(hours,2)))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-26017')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-86'))
            s.add(Estimate(code='QE-26017',mould_id=m.id,rate=280,setup_hours=2,quote_amount=8000,status='draft'))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-FLOOR-DEMO')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-42'));slot=s.scalar(select(MachineSlot).where(MachineSlot.machine=='M450'))
            s.add(Estimate(code='QE-FLOOR-DEMO',mould_id=m.id,slot_id=slot.id,rate=300,setup_hours=1,predicted_hours=10,quote_amount=5000,status='committed',model_result={'note':'已承诺的演示订单，供实绩采集；不作模型效果指标'}));slot.reserved_hours+=10
        for code,grade,qty,price,state in [('RS-P20-DEMO','ABS自然色',500,20,'available'),('RS-ISOLATED','ABS复检隔离',50,18,'quarantine')]:
            if not s.scalar(select(ResinLot).where(ResinLot.code==code)):
                s.add(ResinLot(code=code,grade=grade,received_kg=qty,available_kg=qty,unit_price=price,state=state))
        s.flush()
        seed_drift(s)
        s.commit()
        try:
            drift_service.dispatch(s.scalars(select(drift_service.DriftRun.id).where(drift_service.DriftRun.status=='queued')).all())
        except Exception:
            pass
        print({t.__tablename__:s.scalar(select(func.count()).select_from(t)) for t in (User,Mould,MachineSlot,Estimate,TrialHistory,TrialExecution,CostReview,MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery)})


def seed_drift(s):
    """低估漂移守门演示：M280 六个完工样本实际均高于冻结预测，序贯 CUSUM 越阈报警并暂停新承诺；
    M450 一单未完工以已耗时下界进入序列，只作低估证据、不宣称正常。"""
    m280=s.scalar(select(MachineSlot).where(MachineSlot.machine=='M280',MachineSlot.start_hour=='2026-11-20 08:00'))
    if not m280:
        m280=MachineSlot(machine='M280',start_hour='2026-11-20 08:00',capacity_hours=96,reserved_hours=0);s.add(m280);s.flush()
    m450=s.scalar(select(MachineSlot).where(MachineSlot.machine=='M450'))
    mould=s.scalar(select(Mould).where(Mould.code=='MD-42'))
    base=datetime(2026,11,10,8,0,tzinfo=timezone.utc)
    for i in range(6):
        code=f'QE-DRIFT-{i+1:02d}'
        if s.scalar(select(Estimate).where(Estimate.code==code)):continue
        est=Estimate(code=code,mould_id=mould.id,slot_id=m280.id,rate=300,setup_hours=1,
                     predicted_hours=10,quote_amount=4200,status='committed',
                     model_result={'note':'漂移守门演示承诺单，不冒充模型推理结果'})
        s.add(est);s.flush();m280.reserved_hours=round(m280.reserved_hours+10,2)
        start=base+timedelta(days=i)
        snap={'estimate_code':code,'predicted_hours':10,'quoted_amount':4200,'rate':300,
              'mould_code':mould.code,'slot_id':m280.id,'machine':'M280'}
        s.add(TrialExecution(estimate_id=est.id,started_at=start.isoformat(),
                             ended_at=(start+timedelta(hours=12)).isoformat(),actual_hours=12,
                             good_shots=90,total_shots=100,overtime_reason='冷却周期偏长，演示用低估漂移样本',
                             snapshot=snap,status='completed'))
    run_code='QE-DRIFT-RUN'
    if not s.scalar(select(Estimate).where(Estimate.code==run_code)):
        est=Estimate(code=run_code,mould_id=mould.id,slot_id=m450.id,rate=300,setup_hours=1,
                     predicted_hours=10,quote_amount=4200,status='committed',
                     model_result={'note':'漂移守门演示运行单，不冒充模型推理结果'})
        s.add(est);s.flush();m450.reserved_hours=round(m450.reserved_hours+10,2)
        start=datetime.now(timezone.utc)-timedelta(hours=12)
        snap={'estimate_code':run_code,'predicted_hours':10,'quoted_amount':4200,'rate':300,
              'mould_code':mould.code,'slot_id':m450.id,'machine':'M450'}
        s.add(TrialExecution(estimate_id=est.id,started_at=start.isoformat(),snapshot=snap,status='running'))
    s.flush()
    if not s.scalar(select(drift_service.DriftRun.id)):
        drift_service.request_runs(s,triggered_by='seed')
if __name__=='__main__':seed()
