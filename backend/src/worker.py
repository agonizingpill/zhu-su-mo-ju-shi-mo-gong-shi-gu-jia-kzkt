import os,time,json,logging
from pathlib import Path
import redis
from .db import Session
from .models import Estimate,Mould
from .ml import predict_trial
from . import drift_service
r=redis.Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
Path('/artifacts').mkdir(exist_ok=True)
# 崩溃恢复：上次取走后未跑完的漂移检测重新入队，checkpoint 保证按原观测序列续跑、不重复计数。
try:
    with Session() as s:
        leftover=drift_service.reset_stuck_runs(s)
    drift_service.dispatch(leftover)
except Exception:
    logging.exception('drift recovery scan failed')
while True:
    Path('/tmp/worker-heartbeat').touch()
    # 漂移守门优先被处理，避免低估报警滞留后机台仍放出新承诺
    item=r.blpop(drift_service.QUEUE,'mould-estimates',timeout=2)
    if not item: continue
    queue,payload=item
    if queue==drift_service.QUEUE:
        try:
            with Session() as s:
                drift_service.process_run(s,int(payload))
        except Exception:
            # 检测中断：轮次与已落库 checkpoint 保留，有界重试并续跑原观测序列，不重复计数。
            logging.exception('drift run failed for id %s, requeue',payload)
            try: drift_service.requeue_or_fail(int(payload))
            except Exception: logging.exception('drift requeue failed for id %s',payload)
        continue
    eid=int(payload)
    with Session() as s:
        e=s.get(Estimate,eid)
        if not e or e.status!='calculating':continue
        try:
            e.predicted_hours,e.model_result=predict_trial(s,s.get(Mould,e.mould_id),e.setup_hours)
            e.status='estimated'
            Path(f'/artifacts/estimate-{eid}.json').write_text(json.dumps(e.model_result,ensure_ascii=False))
        except Exception as ex:
            logging.exception('estimate failed');e.status='failed';e.model_result={'error':str(ex)}
        s.commit()
