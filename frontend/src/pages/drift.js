import {esc,number} from '../utils/format.js';
import {api,send} from '../api.js';
import {notify,empty} from '../components/common.js';
import {DRIFT_RUN_STATES,DRIFT_ALARM_STATES,DRIFT_GATE_STATES,DRIFT_OBS_KINDS,DRIFT_DECISIONS} from '../constants.js';

const date=x=>x?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'short'}).format(new Date(x)):'—';
const pct=x=>(x*100).toFixed(1)+'%';
const gateClass=g=>g==='paused'?'failed':'committed';

export function render(s){
  if(!s.drift)return empty('漂移守门数据加载中…');
  const tab=s.driftTab||'gates';
  const tabs=[['gates','机台闸门与检测'],['pending','待观测（未完工下界）'],['alarms','报警与解除'],['runs','检测轮次依据']];
  return `<div class="pagehead"><div><div class="kicker">10 / UNDERESTIMATE DRIFT GUARD</div><h1>工时低估漂移守门</h1><p>保留估价回归，按开工顺序对冻结预测与实际工时做序贯 CUSUM；未完工只以已耗时下界证低估，样本不足不宣称正常。</p></div></div>
  <section class="section panel paper" style="margin-bottom:26px"><h2>主管阈值（仅车间负责人可改）</h2>
  <form id="drift-config" class="formgrid" style="max-width:760px">
    <label>允许漂移常数 k（相对预测工时）<input name="k_slack" type="number" step="0.01" min="0" max="2" value="${s.drift.config.k_slack}" ${s.user.role!=='admin'?'disabled':''}></label>
    <label>CUSUM 判决阈值 h（越过即报警）<input name="h_threshold" type="number" step="0.01" min="0.01" max="10" value="${s.drift.config.h_threshold}" ${s.user.role!=='admin'?'disabled':''}></label>
    <label>判定正常所需最小完工样本数<input name="min_samples" type="number" step="1" min="2" max="100" value="${s.drift.config.min_samples}" ${s.user.role!=='admin'?'disabled':''}></label>
    <div class="actions"><button class="primary" ${s.user.role!=='admin'?'disabled':''}>保存并对全部机台重新检测</button></div>
  </form><p class="note">统计量 S 逐单累加 max(0, 相对偏差 − k)，S ≥ h 即判低估漂移并暂停该机台新机时承诺；已承诺单保留。阈值调整后所有机台按新参数重放原观测序列。</p></section>
  <nav class="drift-tabs">${tabs.map(([k,v])=>`<button type="button" data-tab="${k}" class="${tab===k?'active':''}">${v}</button>`).join('')}</nav>
  ${tab==='gates'?gates(s):tab==='pending'?pending(s):tab==='alarms'?alarms(s):runs(s)}`;
}

function gates(s){
  return `<div class="tablewrap" style="margin-top:18px"><table><thead><tr><th>机台</th><th>闸门</th><th>观测序列</th><th>最新检测</th><th>操作</th></tr></thead><tbody>
  ${s.drift.machines.map(m=>{const r=m.latest_run;return `<tr><td><b class="mono">${esc(m.machine)}</b></td>
  <td><span class="status ${gateClass(m.gate_state)}">${DRIFT_GATE_STATES[m.gate_state]}</span>${m.active_alarm?`<br><small>报警 #${m.active_alarm.id} · S=${number(m.active_alarm.statistic)}</small>`:''}</td>
  <td>${m.completed_observations} 个完工<br><small>${m.pending_lower_bounds.length} 个未完工下界</small></td>
  <td>${r?`#${r.seq} ${DRIFT_RUN_STATES[r.status]}<br><small>${DRIFT_DECISIONS[r.final_decision]}${r.final_stat!=null?` · S=${number(r.final_stat)}`:''}</small>`:'尚未检测'}</td>
  <td><button class="small" data-rerun="${esc(m.machine)}">重新发起检测</button></td></tr>`}).join('')||`<tr><td colspan="5">${empty('暂无可检测机台：先开工一份试模实绩。')}</td></tr>`}</tbody></table></div>
  <p class="rule">报警与暂停并发：只要存在活动报警，该机台新机时承诺在后端被直接拒绝；主管确认最新检测证据后才可解除，期间新异常会令旧解除失效。</p>`;
}

function pending(s){
  return `<div class="tablewrap" style="margin-top:18px"><table><thead><tr><th>机台</th><th>估单</th><th>开工时间</th><th>冻结预测</th><th>已耗时下界</th><th>下界相对偏差</th><th>说明</th></tr></thead><tbody>
  ${s.driftPending.map(o=>`<tr><td class="mono">${esc(o.machine)}</td><td>${esc(o.estimate_code)}</td><td>${date(o.started_at)}</td><td>${number(o.predicted_hours)} h</td><td>${number(o.observed_hours)} h</td><td class="${o.lower_deviation>0?'rust':''}">${pct(o.lower_deviation)}</td><td><small>未完工，下界只触发低估、不据此判正常；完工后按原位置替换重放</small></td></tr>`).join('')||`<tr><td colspan="7">${empty('暂无未完工试模，所有观测均为完工实绩。')}</td></tr>`}</tbody></table></div>`;
}

function alarms(s){
  return `<section class="section" style="margin-top:18px">${s.driftAlarms.map(a=>`<article class="slot"><div class="slothead"><div><b class="mono">${esc(a.machine)}</b> 报警 #${a.id} <span class="status ${a.state==='active'?'failed':a.state==='released'?'committed':'requested'}">${DRIFT_ALARM_STATES[a.state]}</span></div><span class="mono">S=${number(a.statistic)} / h=${number(a.threshold)}</span></div>
  <p class="muted">${esc(a.detail)}<br>触发：${date(a.raised_at)} · 正常样本数 ${a.normal_count}（门槛 ${a.min_samples}）· 闸门 ${DRIFT_GATE_STATES[a.gate_state]}</p>
  <details><summary>查看每次检测依据（${a.evidence.length} 条关键步骤）</summary><div class="tablewrap"><table><thead><tr><th>序</th><th>估单</th><th>观测类型</th><th>观测/预测 (h)</th><th>相对偏差</th><th>增量</th><th>S</th><th>计入正常</th><th>依据</th></tr></thead><tbody>
  ${a.evidence.map(st=>`<tr><td>${st.position}</td><td>${esc(st.estimate_code)}</td><td>${DRIFT_OBS_KINDS[st.obs_kind]}</td><td>${number(st.observed_hours)} / ${number(st.predicted_hours)}</td><td>${pct(st.deviation)}</td><td>${number(st.increment)}</td><td><b>${number(st.cusum_stat)}</b></td><td>${st.counted_for_normal?'是':'否'}</td><td><small>${esc(st.note)}</small></td></tr>`).join('')}</tbody></table></div></details>
  ${a.state==='active'?`<form class="release-form" data-alarm="${a.id}"><label>主管解除原因（至少8字，须确认最新检测证据仍低于阈值）<textarea name="reason" minlength="8" required placeholder="例如：已核实为一次性设备故障，最新检测 S 回落至阈值以下，后续三单恢复预测口径"></textarea></label><button class="primary" ${s.user.role!=='admin'?'disabled':''}>确认最新证据并解除</button></form>`:`<p class="note">${a.state==='released'?`解除人 ${esc(a.released_by)} · ${date(a.released_at)}<br>原因：${esc(a.release_reason)}<br>证据：第 ${a.release_evidence.run_id} 轮检测 S=${number(a.release_evidence.final_stat)} < h=${number(a.release_evidence.h_threshold)}，完工样本 ${a.release_evidence.normal_count}`:'该解除已被后续新异常作废，请以最新活动报警为准。'}</p>`}
  </article>`).join('')||empty('暂无漂移报警。样本不足时页面显示「不宣称正常」，但不会产生报警。')}</section>`;
}

function runs(s){
  return `<section class="section" style="margin-top:18px">${s.driftRuns.map(r=>`<article class="slot"><div class="slothead"><div><b class="mono">${esc(r.machine)}</b> 第 ${r.seq} 轮 <span class="status ${r.status==='done'?'committed':'requested'}">${DRIFT_RUN_STATES[r.status]}</span> <small>${DRIFT_DECISIONS[r.final_decision]}</small></div><span class="mono">进度 ${r.processed_observations}/${r.total_observations}${r.final_stat!=null?` · S=${number(r.final_stat)}`:''}</span></div>
  <p class="muted">${esc(r.message||'排队或检测中，中断后按原观测序列续跑，不重复计数。')}<br><small>触发方式 ${esc(r.triggered_by)} · k=${number(r.config_snapshot.k_slack)} · h=${number(r.config_snapshot.h_threshold)} · 最小样本 ${r.config_snapshot.min_samples} · ${date(r.updated_at)}</small></p>
  <details><summary>展开本轮逐单检测依据</summary><div data-run-steps="${r.id}">加载中…</div></details></article>`).join('')||empty('尚无检测轮次，开工或完工后自动入队，也可在机台页手动发起。')}</section>`;
}

export function bind(s,draw){
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{s.driftTab=b.dataset.tab;draw()});
  const cfg=document.querySelector('#drift-config');
  if(cfg)cfg.onsubmit=async e=>{e.preventDefault();try{const p=Object.fromEntries(new FormData(e.target));p.k_slack=Number(p.k_slack);p.h_threshold=Number(p.h_threshold);p.min_samples=Number(p.min_samples);await api('/drift/config',{method:'PATCH',body:JSON.stringify(p)});notify('阈值已保存，全部机台重新检测已入队');draw()}catch(x){notify(x.message)}};
  document.querySelectorAll('[data-rerun]').forEach(b=>b.onclick=async()=>{try{const r=await send('/drift/runs',{machine:b.dataset.rerun});notify(`检测已入队（轮次 id ${r.queued_run_ids.join(',')}）`);draw()}catch(x){notify(x.message)}});
  document.querySelectorAll('.release-form').forEach(f=>f.onsubmit=async e=>{e.preventDefault();try{await send(`/drift/alarms/${f.dataset.alarm}/release`,Object.fromEntries(new FormData(f)));notify('报警已解除，机台恢复新机时承诺');draw()}catch(x){notify(x.message)}});
  document.querySelectorAll('[data-run-steps]').forEach(d=>{const box=d.closest('details');box.addEventListener('toggle',async function(){if(this.open&&d.dataset.loaded!=='1'){d.dataset.loaded='1';try{const r=await api('/drift/runs/'+d.dataset.runSteps);d.innerHTML=`<div class="tablewrap"><table><thead><tr><th>序</th><th>估单</th><th>类型</th><th>观测/预测 (h)</th><th>相对偏差</th><th>增量</th><th>CUSUM S</th><th>计入正常</th><th>依据</th></tr></thead><tbody>${r.steps.map(st=>`<tr><td>${st.position}</td><td>${esc(st.estimate_code)}</td><td>${DRIFT_OBS_KINDS[st.obs_kind]}</td><td>${number(st.observed_hours)} / ${number(st.predicted_hours)}</td><td>${pct(st.deviation)}</td><td>${number(st.increment)}</td><td><b>${number(st.cusum_stat)}</b></td><td>${st.counted_for_normal?'是':'否'}</td><td><small>${esc(st.note)}</small></td></tr>`).join('')}</tbody></table></div>`}catch(x){d.textContent=x.message}}},{once:true})});
}
