ROLES = ('admin', 'estimator', 'viewer')
ESTIMATE_STATES = ('draft', 'calculating', 'estimated', 'requested', 'committed', 'failed')
STEELS = ('P20', 'H13', 'S136')

EXECUTION_STATES = ('running', 'completed', 'settled')
REVIEW_STATES = ('draft', 'signed')

MAINTENANCE_STATES=('planned','approved','completed')
RESIN_STATES=('available','quarantine')
MATERIAL_STATES=('issued','returned')
DELIVERY_STATES=('dispatched','received','accepted','rectification','closed')

DRIFT_RUN_STATES=('queued','running','done','failed')
DRIFT_ALARM_STATES=('active','released','superseded')
DRIFT_GATE_STATES=('open','paused')
DRIFT_OBS_KINDS=('actual','lower_bound')
DRIFT_DECISIONS=('normal','alarm','insufficient','pending')
