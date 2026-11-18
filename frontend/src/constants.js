export const ROLES={admin:'车间负责人',estimator:'工艺估价员',viewer:'只读观察员'};
export const STATES={draft:'待估算',calculating:'计算中',estimated:'已估工时',requested:'待机时承诺',committed:'已承诺锁定',failed:'计算失败'};
export const STEELS=['P20','H13','S136'];

export const EXECUTION_STATES={running:'试模进行中',completed:'已完工待结算',settled:'实绩已签结'};
export const REVIEW_STATES={draft:'待主管复核',signed:'主管已签结'};

export const MAINTENANCE_STATES={planned:'待批准保养',approved:'停机已批准',completed:'保养已完检'};
export const RESIN_STATES={available:'批次可用',quarantine:'批次隔离'};
export const MATERIAL_STATES={issued:'已领料',returned:'已登记退料'};
export const DELIVERY_STATES={dispatched:'已发样待签收',received:'客户已签收',accepted:'客户接受',rectification:'需整改重试',closed:'主管已关闭'};

export const DRIFT_RUN_STATES={queued:'排队待检',running:'检测进行中',done:'检测完成',failed:'检测失败'};
export const DRIFT_ALARM_STATES={active:'报警中·暂停新承诺',released:'主管已解除',superseded:'新异常令旧解除失效'};
export const DRIFT_GATE_STATES={open:'可接新承诺',paused:'暂停新机时承诺'};
export const DRIFT_OBS_KINDS={actual:'完工实绩',lower_bound:'未完工已耗时下界'};
export const DRIFT_DECISIONS={normal:'未发现低估漂移',alarm:'工时低估漂移报警',insufficient:'样本不足·不宣称正常',pending:'尚未出结论'};
