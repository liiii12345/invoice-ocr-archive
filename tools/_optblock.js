
/* ═══════════════ 批量测试 · 12 条优化 ═══════════════
   全部以「包装原函数」的方式挂载，不改动 build_batch_ui.py 里的原始实现：
   同名函数声明在后面重新赋值即可覆盖，原实现仍可通过 _orig* 调用。      */

const REVIEW_KEY = "ocr_review_marks";
const LLM_KEY    = "ocr_llm_cfg";
let   B_REMOTE   = [];        /* /api/datasets 扫到的本机数据集 */

/* ── 小工具 ── */
function bStore(k, def){ try{ return JSON.parse(localStorage.getItem(k)||JSON.stringify(def)); }catch(e){ return def; } }
function bStoreSet(k, v){ try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} }
function bMarks(){ return bStore(REVIEW_KEY, {}); }
function bMarkSet(k, v){ const m=bMarks(); if(v==null) delete m[k]; else m[k]=v; bStoreSet(REVIEW_KEY, m); }
function bMarkKey(name){ return (BState.out||"batch_out")+"::"+name; }
function bEtaSec(done, total, t0){
  if(!done || !total) return null;
  const el=(Date.now()-t0)/1000, per=el/done;
  return Math.max(0, Math.round(per*(total-done)));
}
function bEtaText(s){ if(s==null) return "";
  return s<60 ? ("约 "+s+" 秒") : ("约 "+Math.floor(s/60)+" 分 "+(s%60)+" 秒"); }

/* ══ ① 真实数据集自动探测 ══ */
async function bLoadDatasets(force){
  try{
    if(!(await probeServer())) return false;
    const r=await fetch(LIVE_ORIGIN+"/api/datasets"+(force?"?refresh=1":""),{cache:"no-store"});
    const j=await r.json();
    if(!j.ok || !Array.isArray(j.datasets)) return false;
    B_REMOTE = j.datasets.filter(d=>d && d.scan);
    const ids = new Set(B_PRESETS.map(p=>p.id));
    B_REMOTE.forEach(d=>{
      if(ids.has(d.id)) return;
      ids.add(d.id);
      B_PRESETS.push({id:d.id, label:d.label, short:d.short||d.id, files:d.files||0,
        sample:"scan", success:.985, accScale:1, costScale:1,
        vtypes:{"intl-commercial":d.files||0}, scan:true,
        default_src:d.src, default_truth:d.truth||""});
    });
    return true;
  }catch(e){ return false; }
}
function bDsOptions(){
  const bi=B_PRESETS.filter(p=>!p.scan), sc=B_PRESETS.filter(p=>p.scan);
  const opt=p=>'<option value="'+esc(p.id)+'">'+esc(p.label)+'</option>';
  let h = bi.length ? '<optgroup label="仓库内置">'+bi.map(opt).join("")+'</optgroup>' : "";
  if(sc.length) h += '<optgroup label="本机扫描（--scan）">'+sc.map(opt).join("")+'</optgroup>';
  else h += '<optgroup label="本机扫描"><option disabled>未配置：python tools/console_server.py --scan &lt;目录&gt;</option></optgroup>';
  return h;
}
function bRefreshDs(){
  const sel=$b("#bDs"); if(!sel) return;
  sel.innerHTML=bDsOptions(); sel.value=BState.ds;
}

/* ══ ② LLM 配置面板（Key 只进内存与子进程 env，不落盘不进仓库）══ */
function bLlm(){ return bStore(LLM_KEY, {apiKey:"", baseUrl:"", model:""}); }
function bLlmCard(){
  const c=bLlm();
  const tail = c.apiKey ? c.apiKey.slice(-4) : "";
  return `
  <div class="card" id="bLlmCard" ${BState.extractor==="rule"?'style="display:none"':""}>
    <div class="card-h"><span class="t">LLM 配置</span>
      <span class="h">仅 llm / hybrid 用到；<b>Key 不写任何文件</b>，只随本次运行注入子进程环境变量</span></div>
    <div class="card-b">
      <div class="fgrid">
        <div><div class="frow"><label>API Key</label>
          <input type="password" id="f_key" autocomplete="off"
            placeholder="${tail?("已保存 ····"+esc(tail)+"（留空沿用）"):"留空则用环境变量 OCR_LLM_API_KEY"}">
        </div>
        <div class="frow"><label>模型</label>
          <input id="f_model" value="${esc(c.model||"")}" placeholder="默认 qwen-flash"></div>
        </div>
        <div><div class="frow"><label>Base URL</label>
          <input id="f_burl" value="${esc(c.baseUrl||"")}" placeholder="默认 https://dashscope.aliyuncs.com/compatible-mode/v1"></div>
          <div class="frow"><label>&nbsp;</label>
          <span class="lbl" style="line-height:1.5">命令行等价写法：<span style="font-family:var(--mono)">export OCR_LLM_API_KEY=…</span><br>
            面板下发只对「界面运行」生效；复制出去的命令仍需自己 export。</span></div>
        </div>
      </div>
    </div>
  </div>`;
}

/* ══ ④ 断点续跑：读上一次的结果，算出「新增 / 重跑 / 失败」══ */
async function bPrevRun(out){
  try{
    const r=await fetch(LIVE_ORIGIN+"/api/results?out="+encodeURIComponent(out||"batch_out"),{cache:"no-store"});
    const j=await r.json();
    if(!j.ok) return null;
    return {rows:j.rows||[],
            ok:new Set((j.rows||[]).filter(x=>String(x.ok).toLowerCase()==="true").map(x=>x.name))};
  }catch(e){ return null; }
}
function bSandboxRows(m, prefix){
  /* 沙箱没有真实 results.csv，按指标合成一批行，让明细表 / 字段对照 / 复核标记照样能用 */
  const rows=[]; const pre=prefix||(bPreset().sample||"sample");
  for(let i=0;i<m.total;i++){
    const okf = i < m.ok;
    rows.push({name:pre+"_"+(i+1)+".jpg", ok:okf?"True":"False",
      vtype:"intl-commercial", vtype_name:"英文商发",
      avg_conf:String(m.avgConf-(i%3)*0.01), line_count:"18",
      elapsed:String(m.el.p50), extractor:BState.extractor,
      invoice_number:"SYN"+String(i+1).padStart(6,"0"),
      invoice_date:"2026-0"+((i%9)+1)+"-1"+((i%9)),
      seller_name:"示例供应商 "+(i+1), client_name:"示例采购中心",
      tax:(100+i*7.3).toFixed(2), total:(1000+i*73.5).toFixed(2),
      item_count:"3", error:okf?"":"OCR 未检出票面主字段"});
  }
  return rows;
}
function bSegOf(prev, rows){
  /* 三段式：本次新跑出来的 / 上次已有这次又跑了一遍 / 失败 */
  const pOK = prev ? prev.ok : new Set();
  let add=0, again=0, fail=0;
  (rows||[]).forEach(r=>{
    const ok = String(r.ok).toLowerCase()==="true";
    if(!ok){ fail++; return; }
    if(pOK.has(r.name)) again++; else add++;
  });
  return {add, again, fail, prevTotal:pOK.size};
}
function bSegBar(seg){
  const tot=Math.max(1, seg.add+seg.again+seg.fail);
  const pc=n=>Math.round(n/tot*100);
  return '<div class="segbar"><i class="a" style="width:'+pc(seg.add)+'%"></i>'+
    '<i class="r" style="width:'+pc(seg.again)+'%"></i>'+
    '<i class="f" style="width:'+pc(seg.fail)+'%"></i></div>'+
    '<div class="segkey"><span><i class="a"></i>新增 '+seg.add+'</span>'+
    '<span><i class="r"></i>重跑 '+seg.again+'</span>'+
    '<span><i class="f"></i>失败 '+seg.fail+'</span>'+
    (seg.prevTotal?'<span class="muted">上次已成功 '+seg.prevTotal+' 张（--resume 会跳过）</span>':'')+
    '</div>';
}

/* ══ ⑥ 跳文档档案视图 ══ */
function bRowToDoc(r, idx){
  const det={
    doc_type:r.vtype_name||r.vtype||"发票票据",
    title:r.name,
    summary:"来自批量任务「"+esc(BState.out||"batch_out")+"」的抽取结果。"+
            (r.error?("失败原因："+esc(r.error)):"OCR 原文快照需加 --detail 才会导出，此处为空。"),
    parties:[{role:"seller", name:r.seller_name||""},{role:"buyer", name:r.client_name||""}],
    primary_date:r.invoice_date||"", primary_amount_text:r.total||"",
    primary_amount_value:parseFloat(String(r.total||"").replace(/[^0-9.\-]/g,""))||null,
    computed_total_value:parseFloat(String(r.total||"").replace(/[^0-9.\-]/g,""))||null,
    fields:[["发票号码",r.invoice_number],["开票日期",r.invoice_date],
            ["销售方",r.seller_name],["购买方",r.client_name],
            ["税额",r.tax],["价税合计",r.total]].filter(f=>f[1]),
    seals:[], identity_issues:[], amounts:[], key_dates:[], raw_evidence:[], field_verdicts:[],
    llm_model:r.extractor||BState.extractor,
    completeness:null
  };
  return {id:-1000-idx, sha:"batch", doc_type:det.doc_type, title:r.name,
    amount_text:r.total||"", confidence:parseFloat(r.avg_conf)||0,
    ingested_at:new Date().toISOString(), ocr_s:parseFloat(r.elapsed)||0, llm_s:null,
    completeness:null, details:det, raw_text:"", markdown:"", preview_img:null,
    _batch:true};
}
function bOpenInDocs(r, idx){
  try{
    const doc=bRowToDoc(r, idx);
    DATA.docs = DATA.docs.filter(d=>d.id!==doc.id);
    DATA.docs.unshift(doc);
    DATA.total = DATA.docs.length;
    switchView("doc");
    if(document.querySelector("#q")) document.querySelector("#q").value="";
    select(doc.id);
    toast("已载入档案视图");
  }catch(e){ toast("跳转失败：" + e.message); }
}

/* ══ ⑦ 字段横向差异对照 ══ */
const B_DIFF_FIELDS=[["invoice_number","发票号码"],["invoice_date","开票日期"],
  ["seller_name","销售方"],["client_name","购买方"],["tax","税额"],["total","价税合计"]];
function bDiffBad(k, v){
  v=String(v==null?"":v).trim();
  if(!v) return "缺失";
  if(k==="invoice_number" && !/[0-9]/.test(v)) return "无数字";
  if(k==="invoice_date"   && !/[0-9]{4}|[0-9]{2}\/[0-9]{2}\/[0-9]{4}/.test(v)) return "日期格式";
  if((k==="tax"||k==="total") && !/^[0-9][0-9.,\-]*$/.test(v)) return "非数值";
  return "";
}
function bDiffHtml(rows){
  const rws=(rows||[]).slice(0,200);
  if(!rws.length) return '<div class="hint">没有可对照的结果（实跑后才有 results.csv）。</div>';
  let h='<div class="hint">列=字段，行=文件。<span class="dbad">橙色</span>表示该字段缺失或格式可疑，用来找系统性偏差，而不是只看均值。</div>';
  h+='<div class="dtwrap"><table class="dt diff"><thead><tr><th>文件</th>'+
     B_DIFF_FIELDS.map(f=>'<th>'+f[1]+'</th>').join("")+'</tr></thead><tbody>';
  rws.forEach(r=>{
    h+='<tr><td class="m">'+esc(r.name)+'</td>'+
      B_DIFF_FIELDS.map(f=>{ const why=bDiffBad(f[0], r[f[0]]);
        return '<td class="'+(why?'dbad':'')+'" title="'+esc(why)+'">'+esc(r[f[0]]||"—")+'</td>'; }).join("")+
      '</tr>';
  });
  h+='</tbody></table></div>';
  const cnt={}; B_DIFF_FIELDS.forEach(f=>cnt[f[1]]=0);
  rws.forEach(r=>B_DIFF_FIELDS.forEach(f=>{ if(bDiffBad(f[0], r[f[0]])) cnt[f[1]]++; }));
  h+='<div class="bars" style="margin-top:12px">'+
     B_DIFF_FIELDS.map(f=>bBar(f[1]+" 异常", cnt[f[1]]/rws.length, cnt[f[1]]?"y":"g", cnt[f[1]]+" / "+rws.length)).join("")+
     '</div>';
  return h;
}

/* ══ ⑧ 日志分色 ══ */
function bLogClass(line){
  if(/^\$/.test(line)) return "ac";
  if(/\[error\]|Traceback|Error:/.test(line)) return "bd";
  if(/\[warn\]|\[done\] 沙箱/.test(line)) return "wn";
  if(/\[overview\]|\[done\] 实跑/.test(line)) return "ok";
  if(/\[field\]/.test(line)) return "fm";
  if(/\[plan\]/.test(line)) return "pl";
  if(/\[out\]/.test(line)) return "fm";
  if(/\bFAIL\b/.test(line)) return "wn";
  return "cm";
}

/* ══ ⑩ 导出 Markdown / JSON ══ */
function bReport(kind){
  const cur=BState.cur; if(!cur){ toast("先跑一次"); return; }
  const m=cur.metrics, acc=bNormAcc(m.accRaw||m.acc);
  const marks=bMarks(); const rows=cur.rows||[];
  const bad=rows.filter(r=>marks[bMarkKey(r.name)]==="bad").length;
  const good=rows.filter(r=>marks[bMarkKey(r.name)]==="ok").length;
  const obj={
    task:cur.name, kind:cur.kind, extractor:cur.extractor, out:BState.out,
    cmd:bCmd(), ts:cur.ts,
    total:m.total, ok:m.ok, fail:m.fail, elapsed:m.elapsed, throughput:m.throughput,
    items_full_match:m.itemsFull, avg_accuracy:m.avgAcc,
    field_accuracy:acc,
    review:{checked:good+bad, wrong:bad, rate:(good+bad)?+(bad/(good+bad)).toFixed(3):null}
  };
  if(kind==="json"){
    const a=document.createElement("a");
    a.href=URL.createObjectURL(new Blob([JSON.stringify(obj,null,2)],{type:"application/json"}));
    a.download="batch_report_"+Date.now()+".json"; a.click(); toast("已导出 JSON"); return;
  }
  const pct=v=>(+(v||0)*100).toFixed(1)+"%";
  let md="# 批量测试报告\n\n";
  md+="- 任务："+cur.name+"　模式："+(cur.kind==="live"?"实跑":"样例沙箱")+"\n";
  md+="- 抽取策略："+cur.extractor+"　输出目录：`"+BState.out+"`\n";
  md+="- 命令：`"+bCmd()+"`\n\n";
  md+="## 总览\n\n| 指标 | 值 |\n| :- | :- |\n";
  md+="| 总数 | "+m.total+" |\n| 成功 | "+m.ok+" |\n| 失败 | "+m.fail+" |\n";
  md+="| 总耗时 | "+m.elapsed+"s |\n| 吞吐 | "+m.throughput+" 张/分 |\n";
  md+="| 明细一致率 | "+pct(m.itemsFull)+" |\n| 字段准确率均值 | "+pct(m.avgAcc)+" |\n\n";
  md+="## 字段准确率\n\n| 字段 | 准确率 |\n| :- | :- |\n";
  B_ACC_ORDER.forEach(k=>{ md+="| "+k+" | "+pct(acc[k])+" |\n"; });
  if(good+bad){
    md+="\n## 抽样复核\n\n共复核 "+(good+bad)+" 张，判错 "+bad+" 张，错误率 "+
        ((bad/(good+bad))*100).toFixed(1)+"%。\n";
  }
  md+="\n> "+(cur.kind==="live"
      ? "实跑结果，来自 `"+BState.out+"/summary.json`。"
      : "样例沙箱结果：指标由所选参数推算，用于演示评估口径，非真实跑批。")+"\n";
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([md],{type:"text/markdown"}));
  a.download="batch_report_"+Date.now()+".md"; a.click(); toast("已导出 Markdown");
}

/* ══ ⑪ 抽样复核标记 ══ */
function bReviewBar(rows){
  const m=bMarks();
  let bad=0, good=0;
  (rows||[]).forEach(r=>{ const v=m[bMarkKey(r.name)]; if(v==="bad") bad++; else if(v==="ok") good++; });
  const n=good+bad;
  return n ? ('<span class="pill '+(bad?"warn":"ok")+'">已复核 '+n+' 张 · 判错 '+bad+
              ' · 错误率 '+(bad/n*100).toFixed(1)+'%</span>') : '';
}
function bRowsHtml(rows, kind){
  if(!rows || !rows.length) return '<div class="hint">没有明细行。</div>';
  const m=bMarks();
  let h='<div class="dtwrap"><table class="dt rows"><thead><tr><th>复核</th><th>文件</th><th>结果</th>'+
    '<th>票种</th><th>置信</th><th>耗时</th><th>发票号码</th><th>价税合计</th><th>明细</th><th></th>'+
    '</tr></thead><tbody>';
  rows.slice(0,300).forEach((r,i)=>{
    const ok=String(r.ok).toLowerCase()==="true";
    const mk=m[bMarkKey(r.name)]||"";
    h+='<tr data-n="'+esc(r.name)+'">'+
      '<td class="mk"><button class="mkbtn '+(mk==="ok"?"on":"")+'" data-m="ok" title="复核无误">✓</button>'+
      '<button class="mkbtn '+(mk==="bad"?"on":"")+'" data-m="bad" title="判错">✗</button></td>'+
      '<td class="m">'+esc(r.name)+'</td>'+
      '<td>'+(ok?'<span class="pill ok">OK</span>':'<span class="pill bad">FAIL</span>')+'</td>'+
      '<td>'+esc(r.vtype_name||r.vtype||"—")+'</td>'+
      '<td>'+esc(r.avg_conf||"—")+'</td><td>'+esc(r.elapsed||"—")+'s</td>'+
      '<td>'+esc(r.invoice_number||"—")+'</td><td>'+esc(r.total||"—")+'</td>'+
      '<td>'+esc(r.item_count||"—")+'</td>'+
      '<td><button class="lnk" data-i="'+i+'">档案视图 ›</button></td></tr>';
  });
  h+='</tbody></table></div>';
  if(rows.length>300) h+='<div class="hint">仅显示前 300 行。</div>';
  return h;
}
function bBindRows(box, rows){
  box.querySelectorAll(".mkbtn").forEach(b=>b.onclick=()=>{
    const n=b.closest("tr").dataset.n, v=b.dataset.m;
    const cur=bMarks()[bMarkKey(n)];
    bMarkSet(bMarkKey(n), cur===v?null:v);
    bEnhance();
  });
  box.querySelectorAll(".lnk").forEach(b=>b.onclick=()=>{
    const r=rows[+b.dataset.i]; if(r) bOpenInDocs(r, +b.dataset.i);
  });
}

/* ══ ③ 多任务对比 ══ */
function bCompareHtml(){
  const sel=(BState.cmp||[]).map(i=>BState.tasks[i]).filter(Boolean);
  if(sel.length<2) return '<div class="hint">至少勾选两个任务。</div>';
  const cols=["策略","张数","总耗时","吞吐","明细一致率","准确率均值"].concat(B_ACC_ORDER);
  let h='<div class="dtwrap"><table class="dt cmp"><thead><tr><th>任务</th>'+
    cols.map(c=>'<th>'+c+'</th>').join("")+'</tr></thead><tbody>';
  sel.forEach(t=>{
    const m=t.metrics, acc=bNormAcc(m.accRaw||m.acc);
    h+='<tr><td class="m">'+esc(t.name)+' <span class="pill '+(t.kind==="live"?"ok":"warn")+'">'+
       (t.kind==="live"?"实跑":"样例")+'</span></td>'+
      '<td>'+esc(t.extractor)+'</td><td>'+m.total+'</td><td>'+m.elapsed+'s</td>'+
      '<td>'+m.throughput+'</td><td>'+Math.round(m.itemsFull*100)+'%</td>'+
      '<td><b>'+(m.avgAcc*100).toFixed(1)+'%</b></td>'+
      B_ACC_ORDER.map(k=>'<td>'+((acc[k]||0)*100).toFixed(1)+'%</td>').join("")+'</tr>';
  });
  h+='</tbody></table></div>';
  /* 谁最快 / 谁最准 */
  const fast=sel.slice().sort((a,b)=>a.metrics.elapsed-b.metrics.elapsed)[0];
  const best=sel.slice().sort((a,b)=>b.metrics.avgAcc-a.metrics.avgAcc)[0];
  h+='<div class="hint">最快：<b>'+esc(fast.name)+'</b>（'+fast.metrics.elapsed+'s）；'+
     '最准：<b>'+esc(best.name)+'</b>（'+(best.metrics.avgAcc*100).toFixed(1)+'%）。'+
     (fast===best?'　两者是同一个任务，这个策略在这个数据集上没有取舍。':'　二者不同，按你要的是速度还是准确率选。')+
     '</div>';
  return h;
}

/* ══ 结果区增强：把上面这些挂进 #bExt ══ */
function bEnhance(){
  const ext=$b("#bExt"); if(!ext) return;
  const cur=BState.cur;
  if(!cur){ ext.innerHTML=""; return; }
  const rows=cur.rows||[];
  const seg=cur.seg;
  if(BState.cmpOpen){
    ext.innerHTML='<div class="card"><div class="card-h"><span class="t">多任务对比</span>'+
      '<span class="h"><button class="btn" id="bCmpX">关闭对比</button></span></div>'+
      '<div class="card-b">'+bCompareHtml()+'</div></div>';
    const x=$b("#bCmpX"); if(x) x.onclick=()=>{BState.cmpOpen=false; bEnhance();};
    return;
  }
  let h="";
  if(seg && (seg.prevTotal||seg.again)){
    h+='<div class="card"><div class="card-h"><span class="t">断点续跑</span>'+
       '<span class="h">与上一次 '+esc(BState.out||"batch_out")+' 的结果比对</span></div>'+
       '<div class="card-b">'+bSegBar(seg)+'</div></div>';
  }
  if(rows.length){
    h+='<div class="card"><div class="card-h"><span class="t">结果明细</span>'+
      '<span class="h">'+bReviewBar(rows)+'</span></div>'+
      '<div class="card-b"><div class="tabs2">'+
      '<button class="on" data-t="rows">明细</button><button data-t="diff">字段对照</button></div>'+
      '<div id="bRowsBox">'+bRowsHtml(rows, cur.kind)+'</div></div></div>';
  }
  ext.innerHTML=h;
  const box=$b("#bRowsBox");
  if(box){
    bBindRows(box, rows);
    box.parentElement.querySelectorAll(".tabs2 button").forEach(b=>b.onclick=()=>{
      box.parentElement.querySelectorAll(".tabs2 button").forEach(x=>x.classList.toggle("on", x===b));
      box.innerHTML = b.dataset.t==="diff" ? bDiffHtml(rows) : bRowsHtml(rows, cur.kind);
      if(b.dataset.t!=="diff") bBindRows(box, rows);
    });
  }
}

/* ══ 侧栏：加勾选框与「对比」入口 ══ */
const _origRenderSide = bRenderSide;
bRenderSide = function(){
  _origRenderSide();
  const sel=$b("#bDs"); if(!sel) return;
  if(sel.dataset.opt!=="1"){ sel.innerHTML=bDsOptions(); sel.value=BState.ds; sel.dataset.opt="1"; }
  const list=$b("#bTaskList");
  if(!list || !BState.tasks.length) return;
  BState.cmp = BState.cmp || [];
  list.querySelectorAll(".item").forEach(el=>{
    const i=+el.dataset.i;
    if(el.querySelector(".cmpbox")) return;
    const cb=document.createElement("input");
    cb.type="checkbox"; cb.className="cmpbox"; cb.checked=BState.cmp.indexOf(i)>=0;
    cb.onclick=e=>{ e.stopPropagation();
      const k=BState.cmp.indexOf(i);
      if(cb.checked){ if(k<0) BState.cmp.push(i); } else if(k>=0) BState.cmp.splice(k,1);
      bEnhance(); };
    el.insertBefore(cb, el.firstChild);
  });
};

/* ══ 表单：追加 LLM 卡 ══ */
const _origRenderForm = bRenderForm;
bRenderForm = function(){ return _origRenderForm() + bLlmCard(); };
const _origBindForm = bBindForm;
bBindForm = function(){
  _origBindForm();
  const key=$b("#f_key"), burl=$b("#f_burl"), mdl=$b("#f_model");
  const c=bLlm();
  if(burl) burl.oninput=()=>{ c.baseUrl=burl.value; bStoreSet(LLM_KEY,c); };
  if(mdl)  mdl.oninput =()=>{ c.model =mdl.value;  bStoreSet(LLM_KEY,c); };
  if(key)  key.oninput =()=>{ if(key.value) { c.apiKey=key.value; bStoreSet(LLM_KEY,c); } };
  const ex=$b("#f_ext");
  if(ex) ex.onchange=()=>{ BState.extractor=ex.value;
    const card=$b("#bLlmCard"); if(card) card.style.display = ex.value==="rule" ? "none":"";
    bRenderCmd(); };
};

/* ══ 命令：把 LLM 参数带上（Key 只走进程 env，不进命令行回显）══ */
const _origLiveQuery = bLiveQuery;
bLiveQuery = function(){
  const q=_origLiveQuery();
  const c=bLlm();
  const p=new URLSearchParams(q);
  if(c.apiKey) p.set("apiKey", c.apiKey);
  if(c.baseUrl) p.set("baseUrl", c.baseUrl);
  if(c.model) p.set("model", c.model);
  p.set("task", BState.taskId || ("t"+Date.now()));
  return p.toString();
};

/* ══ 运行：ETA + 中止 + 续跑 + 三段式（重写 bRun）══ */
async function bRun(){
  if(BState.running) return;
  BState.running=true; BState.abort=false;
  BState.taskId="t"+Date.now();
  const n = BState.limit>0 ? Math.min(BState.limit, dsFiles()) : dsFiles();
  let live=false;
  try{ live = await probeServer(); }catch(e){ live=false; }
  const kind = live?"live":"sandbox";
  BState.mode=kind;

  /* 续跑：先读上一次已成功的名单 */
  let prev=null;
  if(live){ prev = await bPrevRun(BState.out); }

  const body=$b("#bBody"); if(!body){ BState.running=false; return; }
  body.innerHTML = `
    <div class="bp-head"><div><div class="t">批量测试</div>
      <div class="s"><span class="pill ${kind==="live"?"ok":"warn"}">${kind==="live"?"实跑 · localhost":"样例 · 离线沙箱"}</span>
        ${esc(bPreset().short||"batch")} · ${esc(BState.extractor)} · ${n} 张
        ${prev&&prev.ok.size?' · 上次已成功 '+prev.ok.size+' 张':''}</div></div>
      <div class="acts"><button class="btn" id="bStop">中止</button></div></div>
    <div id="bMid">${bRenderForm()}</div>
    <div class="card"><div class="card-h"><span class="t">执行</span><span class="h" id="bProgTx">0 / ${n}</span></div>
      <div class="card-b"><div class="prog"><i id="bProg"></i></div>
        <div class="etaline" id="bEta"></div>
        <div class="logbox" id="bLog"></div></div></div>
    <div id="bExt"></div>`;
  bBindForm();
  const log=$b("#bLog"), prog=$b("#bProg"), tx=$b("#bProgTx"), eta=$b("#bEta");
  const t0=Date.now();
  const put=(s,c)=>{ log.innerHTML+='<span class="'+(c||bLogClass(s))+'">'+esc(s)+"</span>\n";
    log.scrollTop=log.scrollHeight; };
  const progTo=(done,total)=>{
    prog.style.width=Math.round(done/Math.max(1,total)*100)+"%";
    tx.textContent=done+" / "+total;
    const s=bEtaSec(done,total,t0);
    eta.textContent = (done<total && s!=null) ? ("已完成 "+done+"/"+total+" · 预计剩余 "+bEtaText(s)) :
                      (done>=total ? ("完成 "+total+" 张 · 用时 "+((Date.now()-t0)/1000).toFixed(1)+"s") : "");
  };
  $b("#bStop").onclick=()=>{
    if(!BState.running) return;
    BState.running=false; BState.abort=true;
    if(BState.es){ try{BState.es.close();}catch(e){} BState.es=null; }
    if(BState.timer){ clearTimeout(BState.timer); BState.timer=null; }
    if(live) fetch(LIVE_ORIGIN+"/api/stop?task="+encodeURIComponent(BState.taskId)).catch(()=>{});
    put("[warn] 已中止。已写入 "+esc(BState.out)+"/results.csv 的部分保留，下次 --resume 会跳过。","wn");
    bRenderSide(); bRenderBody();
  };
  put("$ "+bCmd(),"ac");
  if(prev && prev.ok.size)
    put("[plan] 上次已成功 "+prev.ok.size+" 张；本次结果会按「新增 / 重跑 / 失败」三段统计","pl");

  /* ── 实跑 ── */
  if(live){
    let res=null;
    await new Promise(r=>{
      const es=new EventSource(LIVE_ORIGIN+"/api/run?"+bLiveQuery());
      BState.es=es;
      es.addEventListener("start", e=>{ try{ const d=JSON.parse(e.data);
        put("$ "+d.cmd,"ac");
        if(d.env && Object.keys(d.env).length)
          put("[env] LLM 凭据已注入子进程："+Object.keys(d.env).join("、")+"（不明文回显）","fm");
      }catch(_){} });
      es.addEventListener("log", e=>{
        let line=""; try{ line=JSON.parse(e.data).line; }catch(_){ return; }
        put(line);
        const mm=line.match(/\[\s*(\d+)\s*\/\s*(\d+)\s*\]/);
        if(mm) progTo(+mm[1], +mm[2]);
      });
      es.addEventListener("done", e=>{
        let j={}; try{ j=JSON.parse(e.data).summary||{}; }catch(_){}
        res=bFromSummary(j);
        put("[done] 实跑返回 summary.json","ok");
        if(BState.es===es) BState.es=null;
        es.close(); r();
      });
      es.addEventListener("error", e=>{
        let msg="后端报错";
        try{ msg=JSON.parse(e.data||'{"message":"后端报错"}').message||msg; }catch(_){}
        put("[error] "+msg,"bd");
        if(BState.es===es) BState.es=null;
        es.close(); r();
      });
      es.onerror=()=>{ if(BState.abort) { r(); return; }
        put("[error] 与本地后端断连","bd");
        if(BState.es===es) BState.es=null; es.close(); r(); };
    });
    BState.running=false;
    if(res && res.total){
      const t=bPush("live", res, (bPreset().short||"batch")+"_"+BState.extractor+"_"+res.total);
      t.out=BState.out;
      const after=await bPrevRun(BState.out);
      t.rows = after ? after.rows : [];
      t.seg  = bSegOf(prev, t.rows);
      BState.cur=t; bSave();
      if(t.seg) put("[overview] 新增 "+t.seg.add+" · 重跑 "+t.seg.again+" · 失败 "+t.seg.fail,"ok");
    }
    bRenderSide(); setTimeout(bRenderBody, 200);
    return;
  }

  /* ── 离线沙箱 ── */
  const m0=bMetrics(BState.extractor, n);
  const task={idx:BState.tasks.length,
    name:(bPreset().short||"batch")+"_"+BState.extractor+"_"+n+"_"+
      (new Date()).toTimeString().slice(0,5).replace(":",""),
    extractor:BState.extractor, total:m0.total, elapsed:m0.elapsed, metrics:m0, kind:"sandbox",
    ts:new Date().toISOString(), out:BState.out,
    rows:bSandboxRows(m0), seg:null};
  task.seg=bSegOf({ok:new Set()}, task.rows);
  BState.tasks.push(task); BState.cur=task; bSave(); bRenderSide();
  put("[plan] 待跑 "+m0.total+" 张 | extractor="+BState.extractor+" | workers="+BState.workers+
      " | 模式=沙箱样例（未连接后端，指标由参数推算）","pl");
  let step=0;
  const lines=Math.max(1, Math.ceil(m0.total/4));
  function tick(){
    if(BState.abort) return;
    for(let i=0;i<lines && step<m0.total;i++,step++){
      const failAt=m0.total-m0.fail, okf=step<failAt;
      put("[ "+(step+1)+"/"+m0.total+"] "+(bPreset().sample||"sample")+"_"+(step+1)+
          ".jpg  "+(okf?"OK ":"FAIL")+"  conf="+
          (m0.avgConf-(step%3)*0.01).toFixed(2)+"  "+m0.el.p50+"s");
    }
    progTo(step, m0.total);
    if(step<m0.total){ BState.timer=setTimeout(tick, Math.max(60, Math.min(1200, 24000/Math.max(1,m0.total)))); return; }
    put("[overview] 总数 "+m0.total+" | 成功 "+m0.ok+" | 失败 "+m0.fail+" | "+
        m0.elapsed+"s | "+m0.throughput+" 张/分钟","ok");
    put("[field] 字段准确率均值 "+(m0.avgAcc*100).toFixed(1)+"% | 明细条数全对 "+
        (m0.itemsFull*100).toFixed(0)+"%","fm");
    put("[out] "+BState.out+"/results.csv · summary.json · failed.txt","fm");
    put("[done] 沙箱样例：以上为演示输出。真实数字请复制上面的命令执行，或启动 tools/console_server.py 后重试。","wn");
    BState.timer=null; BState.running=false; bRenderSide();
    setTimeout(bRenderBody, 400);
  }
  tick();
}

/* ══ 主区渲染增强：加导出按钮与 #bExt 容器 ══ */
const _origRenderBody = bRenderBody;
bRenderBody = function(){
  _origRenderBody();
  const ext=$b("#bExt");
  if(ext){ bEnhance(); return; }
  /* 结果分支里没有 #bExt（老模板），补一个 */
  const res=$b("#bRes");
  if(res && BState.cur){
    const d=document.createElement("div"); d.id="bExt";
    res.parentNode.insertBefore(d, res.nextSibling);
    const acts=document.querySelector(".bp-head .acts");
    if(acts && !acts.querySelector("#bMd")){
      const mk=(id,txt,fn)=>{ const b=document.createElement("button");
        b.className="btn"; b.id=id; b.textContent=txt; b.onclick=fn; acts.appendChild(b); };
      mk("bMd","导出 Markdown",()=>bReport("md"));
      mk("bJs","导出 JSON",()=>bReport("json"));
      mk("bCmp","对比所选",()=>{ BState.cmpOpen=true; bEnhance(); });
    }
    bEnhance();
  }
};

/* ══ 启动：拉远端数据集 ══ */
(async function bBoot(){
  const okRemote = await bLoadDatasets(false);
  if(okRemote && B_REMOTE.length){
    const el=$b("#bDs"); if(el){ el.innerHTML=bDsOptions(); el.value=BState.ds; el.dataset.opt="1"; }
  }
  /* 重新扫描按钮（只在有后端意义） */
  const side=$b("#bTaskList");
  if(side){
    const bar=document.createElement("div");
    bar.className="rescan";
    bar.innerHTML='<button class="btn" id="bRescan" style="width:100%">重新扫描本机数据集</button>';
    const filt=document.querySelector("#shellBatch .filters");
    if(filt && !filt.querySelector("#bRescan")) filt.appendChild(bar);
    const rb=$b("#bRescan");
    if(rb) rb.onclick=async ()=>{
      rb.textContent="扫描中…"; rb.disabled=true;
      const okk=await bLoadDatasets(true);
      bRefreshDs();
      const s2=$b("#bDs"); if(s2){ s2.innerHTML=bDsOptions(); s2.value=BState.ds; s2.dataset.opt="1"; }
      rb.textContent="重新扫描本机数据集"; rb.disabled=false;
      toast(okk ? ("已更新，共 "+B_PRESETS.length+" 个数据集") : "后端未启动，只有内置样例");
    };
  }
})();
