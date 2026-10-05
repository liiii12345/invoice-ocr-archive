# -*- coding: utf-8 -*-
"""向单文件控制台注入「批量测试」工作台（幂等，可重复运行）。"""
import os, sys, re, json

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "..", "demo_console.html")
MARK_CSS = "/*BATCH_CSS*/"
RUNBLOCK = __import__("io").open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "_runblock.js"),
    encoding="utf-8").read()
MARK_JS = "/*BATCH_JS*/"
MARK_DOM = "<!--BATCH_DOM-->"


# ─────────────────────────────────────────────── CSS
CSS = r"""
  /* ── segmented (doc / batch) ── */
  .seg{display:flex;background:var(--hover);border:1px solid var(--line);border-radius:7px;padding:2px;gap:2px}
  .seg button{border:none;background:transparent;height:26px;padding:0 13px;border-radius:5px;font-size:12.5px;color:var(--ink-2);cursor:pointer;font-family:var(--sans)}
  .seg button.on{background:var(--panel);color:var(--accent);font-weight:500;box-shadow:0 1px 2px rgba(0,0,0,.05)}
  .topbar .search.hide{display:none}
  /* ── batch workbench ── */
  .shell.hidden{display:none}
  .bpane{padding:22px 28px 60px;max-width:1200px;display:flex;flex-direction:column;gap:16px}
  .bp-head{display:flex;align-items:flex-start;gap:14px}
  .bp-head .t{font-size:17px;font-weight:600}
  .bp-head .s{font-size:12px;color:var(--ink-3);margin-top:3px}
  .bp-head .acts{margin-left:auto;display:flex;gap:8px;flex:none}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:9px}
  .card-h{padding:11px 15px;border-bottom:1px solid var(--line-2);display:flex;align-items:center;gap:10px}
  .card-h .t{font-size:12.5px;font-weight:600}
  .card-h .h{font-size:11.5px;color:var(--ink-3);margin-left:auto}
  .card-b{padding:14px 15px}
  .fgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
  .frow{display:grid;grid-template-columns:96px 1fr;gap:9px;align-items:center}
  .frow>label{font-size:12px;color:var(--ink-3);text-align:right}
  .frow input,.frow select{height:31px;border:1px solid var(--line);border-radius:6px;background:#fafbfc;
    padding:0 9px;font-size:12.5px;color:var(--ink);outline:none;font-family:var(--sans);width:100%}
  .frow input:focus,.frow select:focus{border-color:var(--accent);background:#fff}
  .frow input[type=checkbox]{width:15px;height:15px;accent-color:var(--accent);margin:0}
  .frow .num{width:74px}
  .chkrow{display:flex;flex-wrap:wrap;gap:16px}
  .chk{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--ink-2);cursor:pointer;user-select:none}
  /* 多输入源 chips */
  .srclist{display:flex;flex-direction:column;gap:6px}
  .srcline{display:grid;grid-template-columns:1fr 28px;gap:6px}
  .srcline input{font-family:var(--mono);font-size:11.5px;height:31px}
  .srcline button{border:1px solid var(--line);background:var(--panel);border-radius:6px;cursor:pointer;color:var(--ink-3);font-size:14px;line-height:1}
  .srcline button:hover{background:var(--hover);color:var(--bad)}
  .addsrc{border:1px dashed var(--line);background:#fafbfc;border-radius:6px;height:28px;cursor:pointer;
    font-size:11.5px;color:var(--ink-2);align-self:flex-start;padding:0 11px}
  .addsrc:hover{border-color:var(--accent);color:var(--accent)}
  /* bars / kpi / tables */
  .kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:1px;background:var(--line);
    border:1px solid var(--line);border-radius:9px;overflow:hidden}
  .kpi .c{background:var(--panel);padding:12px 14px}
  .kpi .c .k{font-size:11px;color:var(--ink-3)}
  .kpi .c .v{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px;letter-spacing:-.3px}
  .kpi .c .v small{font-size:11px;font-weight:400;color:var(--ink-3);margin-left:3px}
  .bars{display:flex;flex-direction:column;gap:8px}
  .bar-row{display:grid;grid-template-columns:116px 1fr 52px;gap:10px;align-items:center;font-size:12px;color:var(--ink-2)}
  .bar-track{height:8px;background:#f2f3f5;border-radius:5px;overflow:hidden}
  .bar-track i{display:block;height:100%;border-radius:5px;background:var(--accent)}
  .bar-track i.g{background:#1a8a4a} .bar-track i.y{background:#c8730a} .bar-track i.r{background:#d83931}
  .bar-track i.n{background:#8f959e}
  .bar-row .n{text-align:right;color:var(--ink-3);font-variant-numeric:tabular-nums}
  .hist{display:flex;align-items:flex-end;gap:8px;height:120px;padding:12px 0 0}
  .hist .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:5px;height:100%}
  .hist .col i{display:block;width:70%;background:var(--accent);border-radius:4px 4px 0 0;min-height:2px;opacity:.85}
  .hist .col span{font-size:10.5px;color:var(--ink-3);white-space:nowrap}
  .hist .col b{font-size:10.5px;color:var(--ink-2);font-weight:500}
  table.dt{width:100%;border-collapse:collapse;font-size:12.5px}
  table.dt th,table.dt td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line-2)}
  table.dt th{color:var(--ink-3);font-weight:500;background:#fafbfc;font-size:11.5px;letter-spacing:.3px}
  table.dt tr:last-child td{border-bottom:none}
  table.dt td.m{font-family:var(--mono);font-size:11.5px}
  .logbox{background:#1f2329;color:#e6e8eb;font-family:var(--mono);font-size:11.5px;line-height:1.72;
    padding:13px 15px;border-radius:9px;height:236px;overflow:auto;white-space:pre-wrap;word-break:break-all}
  .logbox .cm{color:#7c8493} .logbox .ok{color:#7ee2a8} .logbox .wn{color:#f0c674} .logbox .bd{color:#ff9b94} .logbox .ac{color:#79b8ff}
  .prog{height:6px;background:var(--line);border-radius:4px;overflow:hidden}
  .prog i{display:block;height:100%;background:var(--accent);width:0;transition:width .3s}
  .tagline{font-size:11.5px;color:var(--ink-3);display:flex;align-items:center;gap:7px;flex-wrap:wrap}
"""

# ─────────────────────────────────────────────── DOM
DOM = """
<!--BATCH_DOM-->
<div class="shell hidden" id="shellBatch">
  <aside class="side">
    <div class="side-h"><span class="t">批量任务</span><span class="c" id="bTaskCnt">0</span></div>
    <div class="filters">
      <div class="row">
        <button class="btn" id="bNewTask" style="flex:1">新建</button>
        <button class="btn primary" id="bRunNow" style="flex:1">运行</button>
      </div>
      <div>
        <div class="lbl">数据集</div>
        <select id="bDs"><option value="">—</option></select>
      </div>
    </div>
    <div class="list" id="bTaskList"></div>
  </aside>
  <main class="main"><div class="bpane" id="bBody"></div></main>
</div>
"""

# ─────────────────────────────────────────────── JS
JS = r"""
/*BATCH_JS*/
/* ═══════════════ 批量测试工作台 ═══════════════ */
const B_PRESETS = __PRESETS__;            /* 构建脚本注入的数据集预设 */
const B_LIVE    = __LIVE__;               /* true = 本地构建（可出现本地数据集） */
const $b = s=>document.querySelector(s);
const LIVE_ORIGIN = location.protocol==="file:" ? "http://127.0.0.1:8770" : "";
function copy(t){
  if(navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(t).then(()=>toast("已复制"),()=>toast("复制失败"));
  else{ const a=document.createElement("textarea"); a.value=t; a.style.position="fixed"; a.style.opacity="0";
    document.body.appendChild(a); a.select();
    try{ document.execCommand("copy"); toast("已复制"); }catch(e){ toast("复制失败"); }
    document.body.removeChild(a); }
}

const BState = {
  view:"doc",
  ds: B_PRESETS[0].id,
  srcs: [B_PRESETS[0].default_src],
  srcType:"dir",
  limit:0, offset:0,
  truth:"", base:"",
  extractor:"rule", workers:1,
  out:"batch_out", resume:false, detail:false,
  tasks: [], cur: null, running:false, timer:null, mode:"sandbox"
};
try{ const t=localStorage.getItem("ocr_batch_tasks"); if(t) BState.tasks=JSON.parse(t); }catch(e){}
function bSave(){ try{ localStorage.setItem("ocr_batch_tasks", JSON.stringify(BState.tasks)); }catch(e){} }
function bPreset(){ return B_PRESETS.find(p=>p.id===BState.ds) || B_PRESETS[0]; }
function dsFiles(){ return bPreset().files; }

/* ── 指标模型：抽取策略系数 × 数据集系数 ── */
const B_EXT = {
  rule  : {rate:7.8, items:.95, acc:{发票号码:.98,开票日期:1,销售方:1,购买方:.98,税额:1,价税合计:.95}, cost:0},
  llm   : {rate:1.5, items:.98, acc:{发票号码:1,开票日期:.98,销售方:.98,购买方:1,税额:.98,价税合计:1}, cost:1},
  hybrid: {rate:3.4, items:.99, acc:{发票号码:.99,开票日期:1,销售方:1,购买方:1,税额:1,价税合计:.99}, cost:.55}
};
const B_ACC_ORDER = ["发票号码","开票日期","销售方","购买方","税额","价税合计"];
function bMetrics(extractor, n){
  const e = B_EXT[extractor] || B_EXT.rule;
  const prop = bPreset();
  const scale = prop.accScale || 1;
  const total = Math.max(1, Math.round(n));
  const ok = Math.round(total * (prop.success ?? .98));
  const fail = total - ok;
  const w = Math.max(1, BState.workers);          /* 多进程边际收益递减 */
  const secPer = (60/e.rate) / Math.pow(w, .55);  /* rate 单位：张/分钟 → 秒/张 */
  const elapsed = +(total*secPer).toFixed(1);
  const acc = {}; let accSum=0;
  for(const k in e.acc){ const v = Math.min(1, +(e.acc[k]*scale).toFixed(3)); acc[k]=v; accSum+=v; }
  const avg = +(accSum/B_ACC_ORDER.length).toFixed(3);
  /* 耗时分布（秒/张） */
  const p50 = +(secPer*0.86).toFixed(2), p90 = +(secPer*1.24).toFixed(2),
        max = +(secPer*1.94).toFixed(2);
  const hist = [0,0,0,0,0];
  for(let i=0;i<total;i++){
    const t = p50 * (0.62 + 1.5*Math.pow((i%7)/6,1.6));
    const b = t< p50*0.75?0 : t< p50*0.95?1 : t< p50*1.2?2 : t< p50*1.6?3 : 4;
    hist[b]++;
  }
  return {total, ok, fail, elapsed, throughput:+(total/elapsed*60).toFixed(1),
          avgConf:+(0.88+avg*0.1).toFixed(3), acc, avgAcc:avg,
          itemsFull:+(e.items*scale).toFixed(3), vtype: prop.vtypes,
          el:{p50,p90,max}, hist, extractor, workers:w, cost:+(total*e.cost*(prop.costScale||1)).toFixed(2)};
}

/* ── 命令拼装（与 src/batch_run.py 参数一一对应）── */
function bCmd(){
  const p=[], a=BState;
  a.srcs.filter(Boolean).forEach(s=>p.push("--input "+JSON.stringify(s)));
  if(a.limit>0) p.push("--limit "+a.limit);
  if(a.offset>0) p.push("--offset "+a.offset);
  if(a.truth) p.push("--truth "+JSON.stringify(a.truth));
  if(a.base) p.push("--base "+JSON.stringify(a.base));
  if(a.extractor!=="rule") p.push("--extractor "+a.extractor);
  if(a.workers>1) p.push("--workers "+a.workers);
  if(a.out) p.push("--out "+a.out);
  if(a.resume) p.push("--resume");
  if(a.detail) p.push("--detail");
  return "python src/batch_run.py " + p.join(" ");
}

/* ── 侧栏：数据集 / 任务列表 ── */
function bRenderSide(){
  const sel=$b("#bDs"); if(!sel) return;
  sel.innerHTML = B_PRESETS.map(p=>
    '<option value="'+p.id+'">'+esc(p.label)+'</option>').join("");
  sel.value = BState.ds;
  sel.onchange = ()=>{ BState.ds=sel.value; BState.srcs=[bPreset().default_src];
                       BState.truth=bPreset().truth||""; bRenderForm(); bRenderBody(); };
  $b("#bTaskCnt").textContent = BState.tasks.length + " 个";
  const list=$b("#bTaskList");
  if(!BState.tasks.length){
    list.innerHTML='<div class="item"><div class="sub"><span class="ty">尚无任务，点「运行」创建一条</span></div></div>';
    return;
  }
  list.innerHTML = BState.tasks.slice().reverse().map((t,i)=>{
    const real = t.idx===BState.tasks.length-1;
    return '<div class="item'+(real?' active':'')+'" data-i="'+(BState.tasks.length-1-i)+'">'+
      '<div class="top"><span class="ttl">'+esc(t.name)+'</span></div>'+
      '<div class="sub"><span class="ty">'+esc(t.extractor)+' · '+t.total+' 张 · '+
      (t.kind==="live"?'<span class="pill ok">实跑</span>':'<span class="pill warn">样例</span>')+'</span>'+
      '<span class="ty">'+t.elapsed+'s</span></div></div>';
  }).join("");
  list.querySelectorAll(".item").forEach(el=>el.onclick=()=>{
    const t=BState.tasks[+el.dataset.i]; if(!t) return;
    BState.cur=t; bRenderBody();
  });
}

/* ── 表单 ── */
function bRenderForm(){
  const p=bPreset();
  return `
  <div class="card">
    <div class="card-h"><span class="t">运行参数</span>
      <span class="h">与 <span style="font-family:var(--mono)">src/batch_run.py</span> 参数一一对应</span></div>
    <div class="card-b">
      <div class="fgrid">
        <div>
          <div class="frow"><label>输入源</label>
            <select id="f_stype">
              <option value="dir">目录 / glob</option>
              <option value="list">清单 .txt</option>
              <option value="csv">清单 .csv</option>
            </select></div>
          <div class="srclist" id="f_srcs"></div>
          <button class="addsrc" id="f_add">+ 添加输入源（可多个）</button>
          <div class="frow" style="margin-top:10px"><label>路径拼接</label>
            <input id="f_base" placeholder="清单内只写文件名时填（--base）"></div>
        </div>
        <div>
          <div class="frow"><label>条数</label>
            <span style="display:flex;gap:6px">
              <input class="num" id="f_limit" type="number" min="0" placeholder="0=全部">
              <span style="align-self:center;color:var(--ink-3);font-size:12px">条数上限　</span>
              <input class="num" id="f_offset" type="number" min="0" placeholder="0" style="width:62px">
              <span style="align-self:center;color:var(--ink-3);font-size:12px">起始偏移</span>
            </span></div>
          <div class="frow"><label>真值表</label>
            <input id="f_truth" placeholder="留空则不计算准确率"></div>
          <div class="frow"><label>抽取策略</label>
            <select id="f_ext">
              <option value="rule">rule（规则抽取，快）</option>
              <option value="llm">llm（大模型抽取，稳但慢）</option>
              <option value="hybrid">hybrid（规则+大模型融合）</option>
            </select></div>
          <div class="frow"><label>并发</label>
            <span style="display:flex;gap:6px;align-items:center">
              <input class="num" id="f_w" type="number" min="1" max="8" value="1">
              <span style="color:var(--ink-3);font-size:12px">进程（--workers，Windows 需 spawn）</span>
            </span></div>
          <div class="frow"><label>输出目录</label>
            <input id="f_out"></div>
        </div>
      </div>
      <div class="chkrow" style="padding-top:2px">
        <label class="chk"><input type="checkbox" id="f_resume">--resume（跳过上次已成功）</label>
        <label class="chk"><input type="checkbox" id="f_detail">--detail（额外导出 OCR 全文与坐标）</label>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-h"><span class="t">命令</span><span class="h" id="bEst"></span></div>
    <div class="card-b"><pre class="code" id="bCmd" style="margin:0;white-space:pre-wrap"></pre></div>
  </div>`;
}
const bFmt = v=>v>=100?Math.round(v):(v>=10?(+v).toFixed(1):(+v).toFixed(2));
function bBindForm(){
  const p=bPreset();
  const st=$b("#f_stype"); if(!st) return;
  st.value=BState.srcType; st.onchange=()=>{BState.srcType=st.value; bRenderBody();};
  const wrap=$b("#f_srcs");
  const draw=()=>{ wrap.innerHTML=BState.srcs.map((s,i)=>
      '<div class="srcline"><input value="'+esc(s)+'" placeholder="目录 / glob / list.txt / 清单.csv">'+
      (BState.srcs.length>1?'<button data-i="'+i+'">×</button>':'')+'</div>').join("");
    wrap.querySelectorAll("input").forEach((inp,i)=>{
      inp.oninput=()=>{BState.srcs[i]=inp.value; bRenderCmd();};
    });
    wrap.querySelectorAll("button").forEach(b=>b.onclick=()=>{
      BState.srcs.splice(+b.dataset.i,1); if(!BState.srcs.length) BState.srcs=[""]; bRenderBody();});
  };
  draw();
  $b("#f_add").onclick=()=>{BState.srcs.push(""); bRenderBody();};
  const bind=(sel,key,cast)=>{const el=$(sel); if(!el) return;
    el.value = BState[key] ?? "";
    el.oninput = ()=>{ BState[key] = cast?cast(el.value):el.value; bRenderCmd(); };};
  bind("#f_base","base"); bind("#f_truth","truth"); bind("#f_out","out");
  bind("#f_limit","limit",v=>Math.max(0,parseInt(v||"0",10)||0));
  bind("#f_offset","offset",v=>Math.max(0,parseInt(v||"0",10)||0));
  bind("#f_ext","extractor"); bind("#f_w","workers",v=>Math.max(1,parseInt(v||"1",10)||1));
  const c=(sel,key)=>{const el=$(sel); if(!el) return; el.checked=!!BState[key];
    el.onchange=()=>{BState[key]=el.checked; bRenderCmd();};};
  c("#f_resume","resume"); c("#f_detail","detail");
  BState.out = $b("#f_out").value || "batch_out";
}

/* ── 命令卡 ── */
function bRenderCmd(){
  const box=$b("#bCmd"); if(!box) return;
  box.innerHTML = esc(bCmd()).replace(/\n/g,"");
  const est = bMetrics(BState.extractor, BState.limit>0?Math.min(BState.limit,dsFiles()):dsFiles());
  const el=$b("#bEst"); if(el){
    el.innerHTML = "预计 "+est.total+" 张 · 成功 "+est.ok+" · 失败 "+est.fail+
      " · 约 "+est.elapsed+"s（"+est.throughput+" 张/分钟）";
  }
}

/* ── 结果区 ── */
function bBar(label,v,cls,right){
  const pct=Math.round(Math.max(0,Math.min(1,v))*100);
  return '<div class="bar-row"><span>'+esc(label)+'</span>'+
    '<span class="bar-track"><i class="'+(cls||"")+'" style="width:'+pct+'%"></i></span>'+
    '<span class="n">'+(right||pct+"%")+'</span></div>';
}
function bNormAcc(src){
  const M={"发票号码":"invoice_number","开票日期":"invoice_date","销售方":"seller_name",
           "购买方":"client_name","税额":"tax","价税合计":"total"}, o={};
  B_ACC_ORDER.forEach(k=>{ const key=M[k]||k;
    o[k] = (src && (src[key]!==undefined ? src[key] : (src[k]!==undefined?src[k]:0))) || 0; });
  return o;
}
function bResult(m,kind){
  const acc=bNormAcc(m.accRaw||m.acc);
  const avgAll=B_ACC_ORDER.reduce((s,k)=>s+acc[k],0)/B_ACC_ORDER.length;
  const vt = m.vtk ? Object.keys(m.vtk).map(k=>[k,m.vtk[k]]) : (m.vtypes||[]);
  const lines=[];
  lines.push('<div class="sec-h">总览</div>');
  lines.push('<div class="kpi">'+
    bKpi("总数", m.total,"") + bKpi("成功", m.ok,"") + bKpi("失败", m.fail,"") +
    bKpi("总耗时", m.elapsed,"s") + bKpi("吞吐", m.throughput,"张/分") +
    bKpi("明细一致率", Math.round(m.itemsFull*100),"%") + "</div>");
  lines.push('<div class="sec-h" style="margin-top:22px">字段准确率<span class="pill '+
    (avgAll>=.95?"ok":"warn")+'" style="margin-left:8px">均值 '+(avgAll*100).toFixed(1)+'%</span></div>');
  lines.push('<div class="bars">'+B_ACC_ORDER.map(k=>bBar(k, acc[k]||0,
      (acc[k]||0)>=.99?"g":(acc[k]||0)>=.95?"":"y", ((acc[k]||0)*100).toFixed(1)+"%")).join("")+"</div>");
  if(m.hist){
  lines.push('<div class="sec-h" style="margin-top:22px">耗时分布（秒/张）</div>');
  lines.push('<div class="hist">'+m.hist.map((c,i)=>{
    const mx=Math.max.apply(null,m.hist)||1;
    const prev=[0,.75,.95,1.2,1.6][i], cut=[.75,.95,1.2,1.6,99][i];
    const lab = i===4 ? (">"+bFmt(m.el.p50*prev)+"s")
                      : (bFmt(m.el.p50*prev)+"-"+bFmt(m.el.p50*cut)+"s");
    return '<div class="col"><b>'+c+'</b><i style="height:'+Math.round(c/mx*88)+'%"></i><span>'+lab+'</span></div>';
  }).join("")+'</div>');
  } else {
    lines.push('<div class="sec-h" style="margin-top:22px">耗时统计（秒/张）</div><div class="bars">'+
      bBar("p50", Math.min(1,m.el.p50/20), "", bFmt(m.el.p50)+"s")+
      bBar("p90", Math.min(1,m.el.p90/20), "", bFmt(m.el.p90)+"s")+
      bBar("max", Math.min(1,m.el.max/20), "y", bFmt(m.el.max)+"s")+'</div>');
  }
  if(vt.length){
    lines.push('<div class="sec-h" style="margin-top:22px">票种分布</div><div class="bars">'+
      vt.map(v=>bBar(v[0], v[1]/m.total, "n", v[1]+" 张")).join("")+"</div>");
  }
  if(m.fail>0){
    lines.push('<div class="sec-h" style="margin-top:22px">失败清单</div>');
    lines.push('<table class="dt"><thead><tr><th>文件</th><th>原因</th><th>耗时</th></tr></thead><tbody>'+
      Array.from({length:Math.min(5,m.fail)},(_,i)=>
        '<tr><td class="m">sample_'+(m.total-m.fail+i+1)+'.jpg</td><td>OCR 未检出票面主字段</td><td>'+
        m.el.p50+'s</td></tr>').join("")+
      (m.fail>5?'<tr><td colspan="3" class="muted">…另有 '+(m.fail-5)+' 条，见 results.csv</td></tr>':'')+
      '</tbody></table>');
  }
  /* 策略对比 */
  const cmp=B_ACC_ORDER.map(k=>({k:k, v:B_EXT[BState.extractor]?B_EXT[BState.extractor].acc[k]:0}));
  lines.push('<div class="sec-h" style="margin-top:22px">同数据集三策略对比（准确率）</div>');
  lines.push('<div class="bars">'+["rule","hybrid","llm"].map(x=>{
    const mm=bMetrics(x, m.total);
    return bBar(x, mm.avgAcc, x===BState.extractor?"":"n",
      (mm.avgAcc*100).toFixed(1)+"% · "+mm.elapsed+"s");
  }).join("")+'</div>');
  return lines.join("");
}
function bKpi(k,v,u){ return '<div class="c"><div class="k">'+k+'</div><div class="v">'+v+
  (u?'<small>'+u+'</small>':'')+'</div></div>'; }

/* ── 主区渲染 ── */
function bRenderBody(){
  const body=$b("#bBody"); if(!body) return;
  const cur=BState.cur;
  if(BState.running){ body.innerHTML=bRenderForm()+"<div id='bMid'></div>"; bBindForm(); bRenderCmd(); }
  else if(cur && cur.kind==="preview"){
    body.innerHTML = `
      <div class="bp-head"><div><div class="t">批量测试</div>
        <div class="s">样例指标 · 由所选参数与数据集预设推算，用于演示评估口径，非真实跑批结果</div></div>
        <div class="acts"><button class="btn" id="bNew2">新建任务</button>
        <button class="btn" id="bCopy2">复制命令</button></div></div>
      <div id="bMid">${bRenderForm()}</div>`;
    bBindForm(); bRenderCmd();
    $("#bNew2").onclick=bNewTask;
    $("#bCopy2").onclick=()=>{copy(bCmd());};
  } else if(cur){
    const m=cur.metrics;
    body.innerHTML = `
      <div class="bp-head"><div><div class="t">批量测试</div>
        <div class="s"><span class="pill ${cur.kind==="live"?"ok":"warn"}">${cur.kind==="live"?"实跑":"样例"}</span>
          ${esc(cur.name)} · ${esc(cur.extractor)} · ${m.total} 张 · ${cur.elapsed}s</div></div>
        <div class="acts"><button class="btn" id="bNew2">新建</button>
          <button class="btn" id="bCopy2">复制命令</button>
          <button class="btn" id="bCsv">导出 CSV</button></div></div>
      <div id="bMid">${bRenderForm()}</div>
      <div id="bRes">${bResult(m,cur.kind)}</div>`;
    bBindForm(); bRenderCmd();
    $("#bNew2").onclick=bNewTask;
    $("#bCopy2").onclick=()=>{copy(bCmd());};
    $("#bCsv").onclick=()=>bCsv(cur);
  } else {
    body.innerHTML = `
      <div class="bp-head"><div><div class="t">批量测试</div>
        <div class="s">配置输入源与抽取策略 → 一键跑批 → 看字段准确率、耗时分布与失败清单</div></div>
        <div class="acts"><button class="btn" id="bCopy">复制命令</button>
          <button class="btn primary" id="bGo">运行</button></div></div>
      <div id="bMid">${bRenderForm()}</div>
      <div class="hint">单文件控制台不自带 Python 运行时：点「运行」会先探测本地 <b>tools/console_server.py</b>（<span style="font-family:var(--mono)">python tools/console_server.py</span>），
      在线则为<b>实跑</b>并回传真实结果；离线则为<b>样例</b>模式，指标由所选参数推算，仅供参考，界面全程标注模式。</div>`;
    bBindForm(); bRenderCmd();
    $("#bCopy").onclick=()=>{copy(bCmd());};
    $("#bGo").onclick=bRun;
  }
  const nr=$b("#bRunNow"); if(nr) nr.onclick=bRun;
  const nt=$b("#bNewTask"); if(nt) nt.onclick=bNewTask;
}
function bCsv(t){
  const rows=[["name","ok","vtype","avg_conf","line_count","elapsed","extractor","invoice_number","total","item_count"]];
  for(let i=0;i<t.metrics.total;i++) rows.push([t.name+"_"+i,"true","sample",t.metrics.avgConf,"18",
    t.metrics.el.p50,t.extractor,"SYN"+(i+1).padStart(4,"0"),"","3"]);
  const csv="\ufeff"+rows.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(",")).join("\n");
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));
  a.download="results_"+Date.now()+".csv"; a.click(); toast("已导出 CSV");
}

/* ── 运行 ── */
function bNewTask(){ BState.cur=null; bRenderBody(); }
async function probeServer(){
  try{ const r=await fetch(LIVE_ORIGIN+"/api/health",{cache:"no-store"});
       if(!r.ok) return false; const j=await r.json(); return !!j.ok; }
  catch(e){ return false; }
}
__RUNBLOCK__

/* ── 顶栏切换 ── */
function switchView(v){
  BState.view=v;
  document.querySelectorAll(".seg button").forEach(b=>b.classList.toggle("on", b.dataset.v===v));
  $("#shellDoc").classList.toggle("hidden", v!=="doc");
  $("#shellBatch").classList.toggle("hidden", v!=="batch");
  /* 搜索框只筛文档，批量视图里没有文档列表，留着是误导 */
  const q=document.querySelector(".topbar .search");
  if(q) q.classList.toggle("hide", v!=="doc");
  if(v==="batch"){ bRenderSide(); bRenderBody(); }
}
document.querySelectorAll(".seg button").forEach(b=>b.onclick=()=>switchView(b.dataset.v));

/* 首次进入：填好默认数据集的真值路径 */
(function bInit(){
  const p=bPreset();
  if(!p) return;
  BState.truth=p.truth||"";
  if(p.default_truth!==undefined) BState.truth=p.default_truth;
})();
"""


def build(presets, live):
    with open(os.path.abspath(TARGET), encoding="utf-8") as f:
        html = f.read()

    js = (JS
          .replace("__PRESETS__", presets)
          .replace("__LIVE__", "true" if live else "false")
          .replace("__RUNBLOCK__", RUNBLOCK))

    # ── CSS（幂等替换）──
    if MARK_CSS in html:
        html = re.sub(r"\n  /\* ── batch workbench ──[\s\S]*?\n  \.tagline\{[^}]*\}\n",
                      lambda m: "\n" + CSS, html, count=1)
    else:
        html = html.replace("</style>", CSS + "\n</style>", 1)

    # ── 给文档 shell 加 id ──
    if 'id="shellDoc"' not in html:
        html = html.replace('<div class="shell">', '<div class="shell" id="shellDoc">', 1)

    # ── DOM ──
    if MARK_DOM in html:
        html = re.sub(r"\n<!--BATCH_DOM-->\n<div class=\"shell hidden\" id=\"shellBatch\">[\s\S]*?\n</div>\n",
                      lambda m: DOM.rstrip("\n"), html, count=1)
    else:
        html = html.replace("<!-- reference drawer -->", DOM + "\n<!-- reference drawer -->", 1)

    # ── seg 控件 ──
    if "class=\"seg\"" not in html:
        html = html.replace('<span class="envtag" id="envtag">',
                            '<div class="seg" style="margin-right:4px">'
                            '<button class="on" data-v="doc">文档档案</button>'
                            '<button data-v="batch">批量测试</button></div>'
                            '<span class="envtag" id="envtag">', 1)

    # ── JS（幂等替换）──
    if MARK_JS in html:
        html = re.sub(r"\n/\*BATCH_JS\*/[\s\S]*?\naddEventListener\(\"load\", \(\)=>\{[\s\S]*?\n\}\);\n",
                      lambda m: "\n" + js, html, count=1)
    else:
        html = html.replace("</script>", js + "\n</script>", 1)

    with open(os.path.abspath(TARGET), "w", encoding="utf-8") as f:
        f.write(html)
    return html


if __name__ == "__main__":
    # 公开版：仅合成样例；本地版追加真实批次（不含任何真实金额/票号）
    public_presets = json.dumps([{
        "id": "sample",
        "label": "合成样例 3 张（仓库内置）",
        "short": "sample",
        "files": 3,
        "sample": "synth",
        "success": 1.0,
        "accScale": 1.0,
        "costScale": 1.0,
        "vtypes": [["发票票样（合成）", 3]],
        "default_src": "data/samples",
        "default_truth": ""
    }], ensure_ascii=False)

    local_presets = json.dumps([
        {
            "id": "sample", "label": "合成样例 3 张（仓库内置）", "short": "sample",
            "files": 3, "sample": "synth", "success": 1.0, "accScale": 1.0, "costScale": 1.0,
            "vtypes": [["发票票样（合成）", 3]],
            "default_src": "data/samples", "default_truth": ""
        },
        {
            "id": "en20", "label": "英文商业发票 20 张（含真值）", "short": "en20",
            "files": 20, "sample": "inv", "success": 1.0, "accScale": 1.0, "costScale": 1.0,
            "vtypes": [["商业发票", 18], ["估算单", 2]],
            "default_src": "D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1/batch1_1/*.jpg",
            "default_truth": "D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1/batch1_1.csv"
        },
        {
            "id": "en1489", "label": "英文商业发票 1489 张（全量）", "short": "en_all",
            "files": 1489, "sample": "inv", "success": .985, "accScale": .985, "costScale": .8,
            "vtypes": [["商业发票", 1302], ["估算单", 118], ["运输单", 69]],
            "default_src": "D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1", "default_truth": ""
        }
    ], ensure_ascii=False)

    # 本地版控制台
    with open(os.path.abspath(TARGET), encoding="utf-8") as f:
        cur = f.read()
    before = len(cur)

    build(public_presets if "--public" in sys.argv else local_presets,
          "--public" in sys.argv)

    after = os.path.getsize(os.path.abspath(TARGET))
    print("patched: %d -> %d bytes" % (before, after))
    print("public mode" if "--public" in sys.argv else "local mode")
