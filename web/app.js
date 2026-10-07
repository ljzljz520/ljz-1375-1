/* 渔港记忆展厅编辑台（原生 JS，无依赖） */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let STATE = null, EDITOR = 1, TAB = "dash";

async function api(path, method = "GET", body) {
  const opt = { method, headers: { "X-Editor-Id": EDITOR, "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "show" + (err ? " err" : "");
  setTimeout(() => t.className = "", 3200);
  if (err) console.error(msg);
}
async function reload(msg) { await refresh(); if (msg) toast(msg); }
async function refresh() { STATE = await api("/api/state"); render(); }

/* ---------- 事件时间 ---------- */
const CERTAIN = { "确切": "b-exact", "约": "b-approx", "区间": "b-range", "未定": "b-unknown" };
function certaintyLabel(ev) {
  if (!ev || ev.type === "undated") return "未定";
  if (ev.type === "range") return "区间";
  return ev.approx ? "约" : "确切";
}
function displayTime(ev) {
  if (!ev) return "年代未定";
  const p = t => { if (!t) return null; const [y, m, d] = t.split("-"); return d ? `${y}年${+m}月${+d}日` : m ? `${y}年${+m}月` : `${y}年`; };
  if (ev.type === "point") return (ev.approx ? "约" : "") + p(ev.date);
  if (ev.type === "range") {
    const s = p(ev.start), e = p(ev.end);
    return s && e ? `${ev.approx ? "约" : ""}${s} 至 ${e}` : s ? `${s} 或更晚` : e ? `不晚于 ${e}` : "年代未定";
  }
  return ev.note ? `年代未定（${ev.note}）` : "年代未定";
}
const badge = ev => { const l = certaintyLabel(ev); return `<span class="badge ${CERTAIN[l]}" title="事件发生时间的确定性">${l}</span>`; };
function statusBadge(s) {
  const map = { approved: "已审校", draft: "草稿", submitted: "待审校", rejected: "已退回", retracted: "已撤回",
    open: "待处理", done: "已处理", building: "构建中", failed: "失败", current: "当前版本", superseded: "旧版" };
  return `<span class="status s-${s}">${map[s] || s}</span>`;
}
/* 事件时间编辑表单（name 前缀 pre） */
function etForm(pre, ev = {}) {
  ev = ev || {};
  return `<div class="etform" data-pre="${pre}">
    <label>时间类型</label>
    <select data-et="type" onchange="etTypeChange(this)">
      <option value="point" ${ev.type !== "range" && ev.type !== "undated" ? "selected" : ""}>确切/约略时点</option>
      <option value="range" ${ev.type === "range" ? "selected" : ""}>区间</option>
      <option value="undated" ${ev.type === "undated" ? "selected" : ""}>年代未定</option>
    </select>
    <div data-grp="point"><label>日期 YYYY[-MM[-DD]]</label><input data-et="date" value="${esc(ev.date || "")}" placeholder="1962 / 1962-05 / 1962-05-01"></div>
    <div data-grp="range">
      <label>不早于（可空）</label><input data-et="start" value="${esc(ev.start || "")}" placeholder="1958">
      <label>不晚于（可空）</label><input data-et="end" value="${esc(ev.end || "")}" placeholder="1962-05">
    </div>
    <div data-grp="undated"><label>未定说明（可空）</label><input data-et="note" value="${esc(ev.note || "")}"></div>
    <label class="row" style="margin-top:4px"><input type="checkbox" style="width:auto" data-et="approx" ${ev.approx ? "checked" : ""}> 约略（不确定到精确年份/月）</label>
  </div>`;
}
function etTypeChange(sel) {
  const box = sel.closest(".etform");
  $$("[data-grp]", box).forEach(g => g.style.display = "none");
  const grp = box.querySelector(`[data-grp="${sel.value}"]`);
  if (grp) grp.style.display = "";
}
function readET(box) {
  if (!box) return null;
  const g = k => box.querySelector(`[data-et="${k}"]`);
  const type = g("type").value;
  const approx = g("approx").checked;
  if (type === "point") return { type, date: g("date").value.trim(), approx };
  if (type === "range") return { type, start: g("start").value.trim() || null, end: g("end").value.trim() || null, approx };
  return { type, note: g("note").value.trim() };
}
function initETForms() { $$(".etform select[data-et=type]").forEach(etTypeChange); }

/* ---------- 页签 ---------- */
const TABS = [
  ["dash", "概览/时间轴"], ["boats", "船只与身份"], ["jargons", "行话"],
  ["photos", "照片"], ["interviews", "口述"], ["statements", "陈述与审校"],
  ["places", "地点/人物"], ["publish", "发布与版本"],
];
async function act(fn) { try { await fn(); } catch (e) { toast(e.message, true); } }

function renderTabs() {
  $("#tabs").innerHTML = TABS.map(([k, n]) =>
    `<button class="${TAB === k ? "active" : ""}" onclick="setTab('${k}')">${n}</button>`).join("");
}
function setTab(k) { TAB = k; render(); }

function render() {
  renderTabs();
  const v = $("#view");
  const fns = { dash: viewDash, boats: viewBoats, jargons: viewJargons, photos: viewPhotos,
    interviews: viewInterviews, statements: viewStatements, places: viewPlaces, publish: viewPublish };
  v.innerHTML = fns[TAB]();
  initETForms();
  if (TAB === "publish") loadReleases();
}

/* ---------- 概览 / 时间轴 ---------- */
function viewDash() {
  const tl = STATE.timeline;
  const li = arr => arr.map(e =>
    `<li>${esc(displayTime(e.event_time))}${badge(e.event_time)} ${esc(e.title)}
      <span class="meta">[${e.kind === "statement" ? "陈述#" + e.ref_id : "船事件#" + e.ref_id}]</span></li>`).join("");
  return `<div class="card">
    <p>船只 <b>${STATE.boats.length}</b> · 行话 <b>${STATE.jargons.length}</b> · 照片 <b>${STATE.photos.length}</b>
    · 口述 <b>${STATE.interviews.length}</b> · 地点 <b>${STATE.places.length}</b>
    · 陈述 <b>${STATE.statements.length}</b>（已审校 ${STATE.statements.filter(s => s.status === "approved").length}）
    · 身份候选 <b>${STATE.candidates.length}</b> · 待处理撤稿 <b>${STATE.tasks.filter(t => t.status === "open").length}</b></p>
    <p class="muted">徽标含义：${badge({ type: "point" })}确切 ${badge({ approx: 1 })}约 ${badge({ type: "range" })}区间 ${badge({ type: "undated" })}年代未定。时间轴仅依据“事件发生时间”，不依据导入顺序。</p>
  </div>
  <div class="card"><h3>时间轴（可排序）</h3><ul>${li(tl.dated) || "<li class=muted>无</li>"}</ul>
  <div class="sep"><h3>年代未定 / 无法可靠排序</h3><ul>${li(tl.undated) || "<li class=muted>无</li>"}</ul>
  <p class="meta">这些条目缺少可用的事件时间上下界，绝不以采集时间或录入顺序代替先后。</p></div></div>`;
}

/* ---------- 船只与身份 ---------- */
function viewBoats() {
  const boatCards = STATE.boats.map(b => {
    const merged = b.status === "merged_into" ? ` <span class="meta">（已并入 #${b.merged_into_id}）</span>` : "";
    return `<details class="card" ${b.status !== "merged_into" ? "open" : ""}>
      <summary>#${b.id} ${esc(b.current_name)} ${merged} ${badge(b.built_event_time)}</summary>
      <div class="meta">船号 ${esc(b.hull_no || "未知")} ｜ 建造 ${esc(displayTime(b.built_event_time))} ${esc(b.notes || "")}</div>
      <div id="boatDetail${b.id}"><button class="act" onclick="loadBoat(${b.id})">加载完整档案</button></div>
    </details>`;
  }).join("");
  const cands = STATE.candidates.map(c => {
    const supportCount = c.evidence.filter(e => e.supports).length;
    const creator = c.created_by;
    return `
    <div class="card"><h3>候选 #${c.id}：${esc(c.boat_a_name)} ↔ ${esc(c.boat_b_name)} ${statusBadge(c.status)}</h3>
      <div class="meta">推测关系 ${c.relation} ｜ 可信度 ${c.confidence} ｜ 支持证据 ${supportCount}/2 ｜ 候选建立人 #${creator}</div>
      <ul>${c.evidence.map(e => `<li class="${e.supports ? "evi-support" : "evi-against"}">${e.supports ? "支持" : "反证"}（${e.kind}）：${esc(e.detail)}</li>`).join("")}</ul>
      ${c.status === "open" ? `
      <div class="grid">
        <input id="ev${c.id}" placeholder="证据描述（如：档案、口述、船号）">
        <select id="evk${c.id}"><option value="document">档案</option><option value="oral">口述</option><option value="name">船名</option><option value="photo">照片</option><option value="hull">船体</option></select>
        <label class="row" style="align-items:center"><input type="checkbox" id="evs${c.id}" checked style="width:auto"> 支持（反证取消勾选）</label>
      </div>
      <button class="act" onclick="act(async()=>{await api('/api/candidates/${c.id}/evidence','POST',{detail:$('#ev${c.id}').value,kind:$('#evk${c.id}').value,supports:$('#evs${c.id}'.checked)});await reload('证据已加入');})">加证据</button>
      <button class="act warn" onclick="act(async()=>{await api('/api/candidates/${c.id}/merge','POST',{surviving_boat_id:${c.boat_a},absorbed_boat_id:${c.boat_b}});await reload('第二位编辑确认：已合并船名（可撤销）');})">第二位编辑确认合并（存 #${c.boat_a}，并 #${c.boat_b}）</button>
      <p class="meta">规则：需≥2条支持证据，且由不同于建立人（#${creator}）的编辑执行。请用右上角切换操作者。</p>
      <button class="act" onclick="act(async()=>{await api('/api/candidates/${c.id}/reject','POST',{});await reload('候选已拒绝：同名不等于同船');})">拒绝同名即同船</button>` : ""}
    </div>`;
  }).join("");
  const merges = STATE.merges.map(m => `<tr><td>#${m.id}</td><td>存 #${m.surviving_boat_id} 并 #${m.absorbed_boat_id}</td>
      <td>${m.undone ? "已撤销" : "生效中"}</td><td>${m.undone ? "" : `<button class="act danger" onclick="act(async()=>{await api('/api/merges/${m.id}/undo','POST',{});await reload('合并已撤销，历史与引用已恢复');})">撤销合并并恢复引用</button>`}</td></tr>`).join("");
  return `
  <div class="card"><h3>新增船只</h3>
    <div class="grid">
      <div><label>现用船名</label><input id="bn" placeholder="如：鲁岱渔3301"></div>
      <div><label>船号（可空）</label><input id="bh" placeholder="HD-3301"></div>
      <div><label>备注</label><input id="bno"></div>
    </div>
    <label>建造时间</label>${etForm("newboat")}
    <button class="act ok" onclick="act(async()=>{const b=readET($$('.etform')[0]);await api('/api/boats','POST',{current_name:$('#bn').value,hull_no:$('#bh').value,notes:$('#bno').value,built_event_time:b});await reload('船已新增');})">新增船只</button>
    <p class="meta">提示：同名船只不会自动认作同船，请在下方“身份候选”中登记证据、由两位编辑确认后合并。</p>
  </div>
  <h2>船只档案</h2>${boatCards}
  <div class="card"><h3>新建身份候选</h3>
    <div class="grid">
      <select id="ca">${STATE.boats.map(b => `<option value="${b.id}">#${b.id} ${esc(b.current_name)}</option>`).join("")}</select>
      <select id="cb">${STATE.boats.map(b => `<option value="${b.id}">#${b.id} ${esc(b.current_name)}</option>`).join("")}</select>
      <select id="cr"><option value="unknown">关系未知</option><option value="same_vessel">同一艘</option><option value="renamed">改名关系</option><option value="rebuilt">重建关系</option></select>
    </div>
    <button class="act" onclick="act(async()=>{await api('/api/candidates','POST',{boat_a:+$('#ca').value,boat_b:+$('#cb').value,relation:$('#cr').value});await reload('候选已建，等待证据与两编辑确认');})">建立候选（不自动合并）</button>
  </div>
  <h2>身份候选与关系证据</h2>${cands || "<p class='meta'>暂无候选</p>"}
  <div class="card"><h3>合并记录（合并可撤销并恢复引用）</h3>
    <table><tr><th>记录</th><th>内容</th><th>状态</th><th>操作</th></tr>${merges || "<tr><td colspan=4 class=muted>暂无合并</td></tr>"}</table></div>`;
}
async function loadBoat(id) {
  const b = await api(`/api/boats/${id}/detail`);
  const row = (x, who) => `<li>${esc(displayTime(x.event_time))}${badge(x.event_time)} ${esc(who)} ${esc(x.note || x.detail || "")}</li>`;
  $(`#boatDetail${id}`).innerHTML = `
    <h3>改名史</h3><ul>${b.names.map(n => row(n, "改名：" + n.name)).join("") || "<li class=muted>无</li>"}</ul>
    <h3>转手史</h3><ul>${b.owners.map(o => row(o, "船东：" + o.owner)).join("") || "<li class=muted>无</li>"}</ul>
    <h3>重建/事件</h3><ul>${b.events.map(e => row(e, e.kind + "：" + e.title)).join("") || "<li class=muted>无</li>"}</ul>
    <details><summary>添加改名 / 转手 / 事件</summary>
      <div class="grid">
        <input id="nt${id}" placeholder="新名字">
        <input id="ot${id}" placeholder="船东">
        <input id="et${id}" placeholder="事件标题（如：大修重建）">
      </div>
      <label>事件时间</label><div id="etbox${id}">${etForm("b" + id)}</div>
      <button class="act" onclick="act(async()=>{const v=$('#nt'+${id}).value;if(v){await api('/api/boats/${id}/names','POST',{name:v,event_time:readET($('#etbox'+${id}+' .etform'))});}await reload('已登记改名');})">加改名</button>
      <button class="act" onclick="act(async()=>{const v=$('#ot'+${id}).value;if(v){await api('/api/boats/${id}/owners','POST',{owner:v,event_time:readET($('#etbox'+${id}+' .etform'))});}await reload('已登记转手');})">加转手</button>
      <button class="act" onclick="act(async()=>{const v=$('#et'+${id}).value;if(v){await api('/api/boats/${id}/events','POST',{kind:'rebuilt',title:v,event_time:readET($('#etbox'+${id}+' .etform'))});}await reload('已登记事件');initETForms();})">加事件</button>
    </details>
    <details><summary>该船史料陈述</summary><ul>${b.statements.map(s => `<li>${statusBadge(s.status)} ${esc(s.title)} <span class=meta>(${esc(displayTime(s.event_time))})</span></li>`).join("") || "<li class=muted>无</li>"}</ul></details>`;
  initETForms();
}

/* ---------- 行话 ---------- */
function viewJargons() {
  const list = STATE.jargons.map(j => `<div class="card"><h3>${esc(j.term)} <span class="meta">${esc(j.pronunciation || "")}</span></h3>
    <p>${esc(j.meaning || "")}</p><div class="meta">例：${esc(j.example || "")} ｜ 采集 ${esc(j.collected_at)}</div></div>`).join("");
  return `<div class="card"><h3>新增行话</h3>
    <div class="grid">
      <div><label>词目</label><input id="jt"></div>
      <div><label>读音</label><input id="jp"></div>
      <div><label>释义</label><input id="jm"></div>
      <div><label>例句</label><input id="je"></div>
    </div>
    <button class="act ok" onclick="act(async()=>{await api('/api/jargons','POST',{term:$('#jt').value,pronunciation:$('#jp').value,meaning:$('#jm').value,example:$('#je').value});await reload('行话已收录');})">收录</button>
  </div>${list || "<p class=meta>暂无</p>"}`;
}

/* ---------- 照片 ---------- */
function viewPhotos() {
  const placeOpts = () => STATE.places.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  const list = STATE.photos.map(p => {
    const refs = p.refs.map(r => `${r.ref_table}#${r.ref_id}`).join("、");
    const wd = p.withdrawn ? `<div class="tombstone">已撤稿：${esc(p.withdrawn_reason || "")}（${esc(p.withdrawn_at)}）<br>原件与派生缩略图已进入撤稿任务。</div>` : "";
    return `<div class="card">
      <h3>照片 #${p.id} ${p.withdrawn ? statusBadge("retracted") : ""}</h3>
      <p><b>说明：</b>${esc(p.caption || "（无）")}</p>
      <p class="meta"><b>授权：</b>${esc(p.license || "未授权/待核实")}（${esc(p.license_ref || "无凭证")}）——说明与授权分开追踪</p>
      <div class="meta">拍摄 ${esc(displayTime(p.taken_event_time))}${badge(p.taken_event_time)} ｜ 采集 ${esc(p.collected_at)}</div>
      <div class="meta">引用（${p.refs.length}）：${refs || "—"} ｜ 资源 ${esc(p.asset_path || "未上传")} / 缩略 ${esc(p.thumb_path || "无")}</div>
      ${wd}
      <details><summary>操作（传原件、改授权/说明、加引用、撤稿）</summary>
        <div class="grid">
          <div><label>上传原件（选择文件，base64）</label><input type="file" id="pf${p.id}"></div>
        </div>
        <button class="act" onclick="act(()=>uploadPhoto(${p.id}))">上传原件并生成缩略图</button>
        <div class="grid">
          <div><label>仅更新说明</label><input id="pc${p.id}" value="${esc(p.caption || "")}"></div>
          <div><label>仅更新授权状态</label><input id="pl${p.id}" value="${esc(p.license || "")}"></div>
          <div><label>授权凭证</label><input id="plr${p.id}" value="${esc(p.license_ref || "")}"></div>
        </div>
        <button class="act" onclick="act(async()=>{await api('/api/photos/${p.id}/caption','POST',{caption:$('#pc'+${p.id}).value});await reload('说明已更新（授权未动）');})">存说明</button>
        <button class="act" onclick="act(async()=>{await api('/api/photos/${p.id}/license','POST',{license:$('#pl'+${p.id}).value,license_ref:$('#plr'+${p.id}).value});await reload('授权已更新（说明未动）');})">存授权</button>
        <div class="grid">
          <select id="prt${p.id}"><option value="places">地点</option><option value="boats">船只</option><option value="interviews">口述</option></select>
          <input id="pri${p.id}" type="number" placeholder="被引用条目 ID" value="${p.place_id || ""}">
        </div>
        <button class="act" onclick="act(async()=>{await api('/api/photos/${p.id}/refs','POST',{ref_table:$('#prt'+${p.id}).value,ref_id:+$('#pri'+${p.id}).value});await reload('已登记引用（同一照片可被多地点引用）');})">加引用</button>
        <div class="sep"><input id="pwr${p.id}" placeholder="撤稿原因（如家属撤回授权）">
        <button class="act danger" onclick="act(async()=>{await api('/api/photos/${p.id}/withdraw','POST',{reason:$('#pwr'+${p.id}).value});await reload('已撤稿：说明/授权分别标记，原件与缩略图纳入撤稿任务');})">撤稿（含派生缩略图）</button></div>
      </details>
    </div>`;
  }).join("");
  return `<div class="card"><h3>登记新照片</h3>
    <div class="grid">
      <div><label>说明</label><input id="np_cap"></div>
      <div><label>授权状态</label><input id="np_lic" placeholder="如 CC BY-SA 4.0"></div>
      <div><label>授权凭证</label><input id="np_ref" placeholder="家属授权书#"></div>
      <div><label>关联地点</label><select id="np_place"><option value="">（无）</option>${placeOpts()}</select></div>
    </div>
    <label>拍摄时间（不确定可填区间/未定）</label>${etForm("newphoto")}
    <button class="act ok" onclick="act(async()=>{await api('/api/photos','POST',{caption:$('#np_cap').value,license:$('#np_lic').value,license_ref:$('#np_ref').value,place_id:+$('#np_place').value||null,taken_event_time:readET($$('.etform')[0])});await reload('照片已登记，可继续上传原件');})">登记照片</button>
  </div>${list || "<p class=meta>暂无</p>"}`;
}
function uploadPhoto(id) {
  const f = $("#pf" + id).files[0];
  if (!f) return toast("请先选择文件", true);
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = async () => {
      const b64 = r.result.split(",")[1];
      try { await api(`/api/photos/${id}/raw`, "POST", { filename: f.name, content: b64, encoding: "base64" }); await reload("原件已存，派生缩略图已生成"); res(); }
      catch (e) { rej(e); }
    };
    r.onerror = rej;
    r.readAsDataURL(f);
  });
}

/* ---------- 口述 ---------- */
function viewInterviews() {
  const list = STATE.interviews.map(iv => `<details class="card"><summary>口述 #${iv.id}：${esc(iv.title)} ${iv.withdrawn ? statusBadge("retracted") : ""}</summary>
    <div class="meta">受访者 #${iv.person_id} ｜ 采集 ${esc(iv.collected_at)} ｜ 音频 ${esc(iv.audio_path || "缺失")}</div>
    ${iv.withdrawn ? `<div class="tombstone">整篇已撤回：${esc(iv.withdrawn_reason || "")}</div>` : ""}
    <div id="iv${iv.id}"><button class="act" onclick="loadIv(${iv.id})">加载逐字稿</button></div>
    <details><summary>添加逐字稿段（可缺时码）</summary>
      <div class="grid">
        <div><label>时码（留空=缺失）</label><input id="tc${iv.id}" placeholder="00:02:40"></div>
        <div><label>说话人</label><input id="sp${iv.id}"></div>
      </div>
      <textarea id="tx${iv.id}" placeholder="逐字稿文本"></textarea>
      <button class="act" onclick="act(async()=>{await api('/api/interviews/${iv.id}/segments','POST',{timecode:$('#tc'+${iv.id}).value||null,speaker:$('#sp'+${iv.id}).value,text:$('#tx'+${iv.id}).value});await reload('段落已保存'+($('#tc'+${iv.id}).value?'':'（时码缺失已显式标记）'));})">保存段落</button>
    </details>
  </details>`).join("");
  return `<div class="card"><h3>新登记口述</h3>
    <div class="grid">
      <div><label>标题</label><input id="ni_t"></div>
      <div><label>受访者</label><select id="ni_p">${STATE.persons.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></div>
      <div><label>地点</label><select id="ni_pl"><option value="">无</option>${STATE.places.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></div>
    </div>
    <button class="act ok" onclick="act(async()=>{await api('/api/interviews','POST',{title:$('#ni_t').value,person_id:+$('#ni_p').value,place_id:+$('#ni_pl').value||null});await reload('口述已登记');})">登记口述</button>
  </div>${list || "<p class=meta>暂无</p>"}`;
}
async function loadIv(id) {
  const iv = await api(`/api/interviews/${id}`);
  $(`#iv${id}`).innerHTML = `<h3>逐字稿</h3>` + iv.segments.map(s => s.withdrawn
    ? `<div class="tombstone">第 ${s.seq} 段已由受访者撤回：${esc(s.withdrawn_reason || "")}</div>`
    : `<div class="card"><span class="meta">${s.timecode ? esc(s.timecode) : "<span class='tc-missing'>时码缺失</span>"} · ${esc(s.speaker || "")}</span>
        <div>${esc(s.text)}</div>
        <input id="sw${s.id}" placeholder="撤回原因"><button class="act danger" onclick="act(async()=>{await api('/api/segments/${s.id}/withdraw','POST',{reason:$('#sw'+${s.id}).value});await loadIv(${id});toast('该段已撤回，发布页将以墓碑呈现');})">受访者撤回此段</button></div>`).join("");
}

/* ---------- 陈述与审校 ---------- */
function viewStatements() {
  const subjOpts = ['<option value="boats">船只</option><option value="jargons">行话</option>',
    '<option value="places">地点</option><option value="persons">人物</option>'].join("");
  const list = STATE.statements.map(s => {
    const acts = {
      draft: `<button class="act ok" onclick="review(${s.id},'submit')">提交审校</button>`,
      submitted: `<button class="act ok" onclick="review(${s.id},'approve')">通过</button>
                  <button class="act danger" onclick="review(${s.id},'reject')">退回</button>`,
      approved: `<button class="act danger" onclick="review(${s.id},'retract')">撤回（撤稿）</button>`,
      rejected: `<button class="act ok" onclick="review(${s.id},'submit')">重新提交</button>`,
      retracted: "",
    }[s.status];
    return `<div class="card"><h3>陈述 #${s.id}：${esc(s.title)} ${statusBadge(s.status)}</h3>
      <p>${esc(s.body)}</p>
      <div class="meta">对象 ${s.subject_table}#${s.subject_id ?? ""} ｜ 事件时间 ${esc(displayTime(s.event_time))}${badge(s.event_time)} ｜ 采集 ${esc(s.collected_at)}</div>
      <div class="meta">来源 ${esc(s.source_type || "未注")}：${esc(s.source_ref || "未注")} ｜ 仅“已审校”进入公开图与时间轴</div>
      ${acts}</div>`;
  }).join("");
  return `<div class="card"><h3>新增史料陈述（API 维护，经审校才公开）</h3>
    <div class="grid">
      <div><label>对象类型</label><select id="ns_t">${subjOpts}</select></div>
      <div><label>对象 ID</label><input id="ns_i" type="number"></div>
      <div><label>标题</label><input id="ns_tt"></div>
    </div>
    <textarea id="ns_b" placeholder="陈述正文"></textarea>
    <div class="grid">
      <div><label>来源类型</label><select id="ns_st"><option>oral</option><option>document</option><option>photo</option><option>interview</option></select></div>
      <div><label>来源出处</label><input id="ns_sr" placeholder="interviews#1 / 档案号"></div>
    </div>
    <label>事件发生时间（不确定可区间/未定）</label>${etForm("newstmt")}
    <button class="act ok" onclick="act(async()=>{await api('/api/statements','POST',{subject_table:$('#ns_t').value,subject_id:+$('#ns_i').value||null,title:$('#ns_tt').value,body:$('#ns_b').value,source_type:$('#ns_st').value,source_ref:$('#ns_sr').value,event_time:readET($$('.etform')[0])});await reload('陈述已存为草稿');})">保存草稿</button>
  </div>${list}`;
}
async function review(id, action) {
  await act(async () => { await api(`/api/statements/${id}/reviews`, "POST", { action }); await reload("审校状态已更新"); });
}

/* ---------- 地点 / 人物 ---------- */
function viewPlaces() {
  const list = STATE.places.map(p => `<div class="card"><h3>${esc(p.name)}</h3>
    <div class="meta">${esc(p.kind || "")} ｜ 坐标 ${p.lat ?? "?"},${p.lng ?? "?"} ｜ 引用照片 ${p.photo_ids.length} 张：${p.photo_ids.join(", ") || "无"}</div>
    <p>${esc(p.note || "")}</p></div>`).join("");
  const persons = STATE.persons.map(p => `<div class="card"><h3>${esc(p.name)} <span class="meta">${esc(p.role || "")}</span></h3><p>${esc(p.bio || "")}</p></div>`).join("");
  return `<div class="card"><h3>新增地点</h3>
    <div class="grid">
      <div><label>名称</label><input id="pl_n"></div><div><label>纬度</label><input id="pl_la" type="number" step="0.0001"></div>
      <div><label>经度</label><input id="pl_lo" type="number" step="0.0001"></div><div><label>类型</label><input id="pl_k"></div>
    </div>
    <textarea id="pl_no" placeholder="说明"></textarea>
    <button class="act ok" onclick="act(async()=>{await api('/api/places','POST',{name:$('#pl_n').value,lat:+$('#pl_la').value||null,lng:+$('#pl_lo').value||null,kind:$('#pl_k').value,note:$('#pl_no').value});await reload('地点已登记，可在照片处加引用');})">新增地点</button>
  </div>
  <h2>地点（地图点位与照片引用同源）</h2>${list}
  <h2>受访者/人物</h2>${persons || "<p class=meta>暂无</p>"}`;
}

/* ---------- 发布 ---------- */
function viewPublish() {
  api("/api/diff").then(d => {
    const box = $("#diffBox");
    if (!box) return;
    if (!d.has_current) { box.innerHTML = "<p class='meta'>尚未发布，本次为首次发布。</p>"; return; }
    const secs = Object.entries(d.sections || {}).map(([k, v]) =>
      `<tr><td>${k}</td><td>${v.added.join(",") || "—"}</td><td>${v.removed.join(",") || "—"}</td><td>${v.modified.join(",") || "—"}</td></tr>`).join("");
    box.innerHTML = (d.drifted
      ? `<p>当前公开版本 #${d.current_release_id} 与最新已审内容<b>存在漂移</b>：${d.timeline_changed ? "（时间轴也已变化）" : ""}</p>`
      : `<p>当前公开版本与最新内容一致，无需重新发布。</p>`)
      + `<table><tr><th>区块</th><th>新增</th><th>移除</th><th>变更</th></tr>${secs || "<tr><td colspan=4 class=muted>无区块差异</td></tr>"}</table>`;
  }).catch(e => $("#diffBox") && ($("#diffBox").textContent = e.message));
  const tasks = STATE.tasks.map(t => `<tr><td>${t.target_table}#${t.target_id}</td><td>${esc(t.reason || "")}</td>
    <td>${t.assets.join("<br>") || "—"}</td><td>${statusBadge(t.status)}</td></tr>`).join("");
  return `<div class="card"><h3>发布</h3>
    <p>发布将<b>冻结</b>当前“已审校”内容图，生成纯静态、可离线、无脚本的展厅；失败时保留上个完整版本，不发布半空页面。</p>
    <input id="pubnote" placeholder="发布说明（可选）">
    <button class="act ok" onclick="act(async()=>{const r=await api('/api/publish','POST',{note:$('#pubnote').value});await reload('发布成功 #'+r.release_id+'，含 '+r.files.length+' 个文件');})">比较并发布</button>
    <div id="diffBox" class="meta">比较中…</div>
  </div>
  <div class="card"><h3>撤稿任务（派生缩略图等资源随任务处理）</h3>
    <table><tr><th>对象</th><th>原因</th><th>需移除/墓碑的资源</th><th>状态</th></tr>${tasks || "<tr><td colspan=4 class=muted>无</td></tr>"}</table></div>
  <div class="card"><h3>发布版本与回滚（离线恢复旧资源）</h3><div id="relBox" class="meta">加载中…</div></div>`;
}
function loadReleases() {
  api("/api/releases").then(rs => {
    const box = $("#relBox");
    if (!box) return;
    const rows = rs.slice().reverse().map(r => {
      const rollback = (r.state === "current") ? "" :
        `<button class="act warn" onclick="act(async()=>{await api('/api/releases/${r.id}/rollback','POST',{});await reload('已恢复旧版本 #${r.id} 的完整资源');})">恢复此版本</button>`;
      return `<tr><td>#${r.id}</td><td>${statusBadge(r.state)}</td><td class=meta>${esc(r.created_at)}</td>
        <td class=meta>${esc(r.content_hash)}</td><td>${esc(r.note || "")}</td><td>${rollback}</td></tr>`;
    }).join("");
    box.innerHTML = `<table><tr><th>版本</th><th>状态</th><th>发布时间</th><th>哈希</th><th>说明</th><th></th></tr>${rows}</table>`;
  }).catch(e => { const b = $("#relBox"); if (b) b.textContent = e.message; });
}

/* ---------- 初始化 ---------- */
async function init() {
  try {
    const editors = await api("/api/editors");
    $("#editorSel").innerHTML = editors.map(e => `<option value="${e.id}">${esc(e.name)}（${e.role}）</option>`).join("");
    $("#editorSel").onchange = e => { EDITOR = +e.target.value; toast("已切换操作者 #" + EDITOR); };
    STATE = await api("/api/state");
    render();
  } catch (e) { $("#view").innerHTML = `<div class="card">初始化失败：${esc(e.message)}</div>`; }
}
init();
