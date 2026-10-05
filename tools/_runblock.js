
function bPush(kind, m, name){
  m.el = m.el || {p50:0,p90:0,max:0};
  const t={idx:BState.tasks.length,
    name:name||((bPreset().short||"batch")+"_"+BState.extractor+"_"+(bPreset().files||m.total)+"_"+
      (new Date()).toTimeString().slice(0,5).replace(":","")),
    extractor:BState.extractor, total:m.total, elapsed:m.elapsed, metrics:m, kind:kind,
    ts:new Date().toISOString()};
  BState.tasks.push(t); BState.cur=t; bSave(); return t;
}
function bLiveQuery(){
  const p=new URLSearchParams();
  BState.srcs.filter(Boolean).forEach(s=>p.append("input",s));
  if(BState.limit>0) p.set("limit",BState.limit);
  if(BState.offset>0) p.set("offset",BState.offset);
  if(BState.truth) p.set("truth",BState.truth);
  if(BState.base) p.set("base",BState.base);
  p.set("extractor",BState.extractor);
  if(BState.workers>1) p.set("workers",BState.workers);
  if(BState.out) p.set("out",BState.out);
  if(BState.resume) p.set("resume","1");
  if(BState.detail) p.set("detail","1");
  return p.toString();
}
function bFromSummary(s){
  const el=s.elapsed||{};
  return {total:s.total||0, ok:s.ok||0, fail:s.failed||0,
    elapsed:+(el.wall||el.sum||0).toFixed(1),
    throughput:+(parseFloat(String(s.throughput||"0").replace(/[^0-9.]/g,""))||0),
    itemsFull:(function(){ const t=String(s.itemsFullMatch||"").split("/");
      return t.length===2 && +t[1] ? (+t[0]/+t[1]) : 0; })(),
    avgConf:0, accRaw:s.field_accuracy_vs_truth||{}, vtk:s.vtype_distribution||{},
    el:{p50:el.p50||0,p90:el.p90||0,max:el.max||0}, hist:null};
}
function bRunLive(put, prog, tx){
  return new Promise(res=>{
    let done=null, closed=false;
    const close=()=>{ if(closed) return; closed=true; try{ es.close(); }catch(e){} res(done); };
    const es=new EventSource(LIVE_ORIGIN+"/api/run?"+bLiveQuery());
    es.onerror=()=>{ if(!done) put("[error] 与本地后端断连，以下为已收到的输出","bd"); close(); };
    es.addEventListener("start", e=>{ try{ put("$ "+JSON.parse(e.data).cmd,"ac"); }catch(_){} });
    es.addEventListener("log", e=>{
      let line=""; try{ line=JSON.parse(e.data).line; }catch(_){ return; }
      put(line, /FAIL/.test(line)?"wn":(/overview|done|\[overview\]/.test(line)?"ok":""));
      const mm=line.match(/\[\s*(\d+)\s*\/\s*(\d+)\s*\]/);
      if(mm && tx){ prog.style.width=Math.round(mm[1]/mm[2]*100)+"%"; tx.textContent=mm[1]+" / "+mm[2]; }
    });
    es.addEventListener("done", e=>{
      let j={}; try{ j=JSON.parse(e.data).summary||{}; }catch(_){}
      done=bFromSummary(j);
      if(tx){ prog.style.width="100%"; tx.textContent=j.total+" / "+(j.total||0); }
      put("[done] 实跑返回 summary.json","ok");
      setTimeout(close, 400);
    });
    es.addEventListener("error", e=>{
      let msg="后端报错";
      try{ msg=JSON.parse(e.data||'{"message":"后端报错"}').message||msg; }catch(_){}
      put("[error] "+msg,"bd"); close();
    });
  });
}
async function bRun(){
  if(BState.running) return;
  BState.running=true;
  const n = BState.limit>0 ? Math.min(BState.limit, dsFiles()) : dsFiles();
  let live=false;
  try{ live = await probeServer(); }catch(e){ live=false; }
  const kind = live?"live":"sandbox";
  const m0 = bMetrics(BState.extractor, n);
  const body=$b("#bBody"); if(!body) { BState.running=false; return; }
  body.innerHTML = `
    <div class="bp-head"><div><div class="t">批量测试</div>
      <div class="s"><span class="pill ${kind==="live"?"ok":"warn"}">${kind==="live"?"实跑 · localhost":"样例 · 离线沙箱"}</span>
        ${esc(bPreset().short||"batch")} · ${esc(BState.extractor)} · ${n} 张</div></div>
      <div class="acts"><button class="btn" id="bStop">停止</button></div></div>
    <div id="bMid">${bRenderForm()}</div>
    <div class="card"><div class="card-h"><span class="t">执行</span><span class="h" id="bProgTx">0 / ${n}</span></div>
      <div class="card-b"><div class="prog"><i id="bProg"></i></div><div class="logbox" id="bLog"></div></div></div>`;
  bBindForm();
  const log=$b("#bLog"), prog=$b("#bProg"), tx=$b("#bProgTx");
  const put=(s,c)=>{ log.innerHTML+='<span class="'+(c||"cm")+'">'+esc(s)+"</span>\n";
    log.scrollTop=log.scrollHeight; };
  $("#bStop").onclick=()=>{ BState.running=false;
    if(BState.tasks.length) BState.tasks[BState.tasks.length-1].kind="stopped";
    bSave(); bRenderSide(); bRenderBody(); };
  put("$ "+bCmd(),"ac");

  if(live){
    const res = await bRunLive(put, prog, tx);
    BState.running=false;
    if(res && res.total){ const t=bPush("live", res); t.name=(bPreset().short||"batch")+"_"+BState.extractor+"_"+res.total; }
    bRenderSide(); setTimeout(bRenderBody, 300); return;
  }

  /* ── 离线沙箱：按参数推算并逐条演示 ── */
  const task={idx:BState.tasks.length, name:(bPreset().short||"batch")+"_"+BState.extractor+"_"+n+"_"+
      (new Date()).toTimeString().slice(0,5).replace(":",""),
    extractor:BState.extractor, total:m0.total, elapsed:m0.elapsed, metrics:m0, kind:"sandbox",
    ts:new Date().toISOString()};
  BState.tasks.push(task); BState.cur=task; bSave(); bRenderSide();
  put("[plan] 待跑 "+m0.total+" 张 | extractor="+BState.extractor+" | workers="+BState.workers+
      " | 模式=沙箱样例（未连接后端，指标由参数推算）","");
  let step=0;
  const lines=Math.max(1, Math.ceil(m0.total/4));
  function tick(){
    for(let i=0;i<lines && step<m0.total;i++,step++){
      const failAt = m0.total - m0.fail;
      const okf = step < failAt;
      put("[ "+(step+1)+"/"+m0.total+"] "+(bPreset().sample||"sample")+"_"+(step+1)+
          ((step+1)%10)+".jpg  "+(okf?"OK ":"FAIL")+"  conf="+
          (m0.avgConf-(step%3)*0.01).toFixed(2)+"  "+m0.el.p50+"s", okf?"ok":"wn");
    }
    prog.style.width=Math.round(step/m0.total*100)+"%";
    tx.textContent=step+" / "+m0.total;
    if(step<m0.total){ BState.timer=setTimeout(tick, Math.max(60, Math.min(1200, 24000/Math.max(1,m0.total)))); return; }
    put("[overview] 总数 "+m0.total+" | 成功 "+m0.ok+" | 失败 "+m0.fail+" | "+
        m0.elapsed+"s | "+m0.throughput+" 张/分钟","ok");
    put("[field] 字段准确率均值 "+(m0.avgAcc*100).toFixed(1)+"% | 明细条数全对 "+
        (m0.itemsFull*100).toFixed(0)+"%","");
    put("[out] "+esc(BState.out)+"/results.csv · summary.json · failed.txt","");
    put("[done] 沙箱样例：以上为演示输出。真实数字请复制上面的命令执行，或启动 tools/console_server.py 后重试。","wn");
    BState.timer=null; BState.running=false; bRenderSide();
    setTimeout(bRenderBody, 400);
  }
  tick();
}
