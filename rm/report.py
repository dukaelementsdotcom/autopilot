"""Writes the dashboard (docs/index.html — one static page, data embedded) and sends Telegram notes."""
import os, json, html, datetime as dt, requests
from . import config as C

ORDER = {"champion": 0, "validated": 1, "near": 2}

def _summary(mem):
    T = mem["tests"].values()
    pick = lambda st: sorted([t for t in T if t["status"] == st], key=lambda t: -(t.get("val") or {}).get("t", 0))
    fam = sorted(mem["families"].items(), key=lambda kv: -kv[1]["n"])
    status = {}
    for r in mem["runs"]:
        for k, v in r["counts"].items(): status[k] = status.get(k, 0) + v
    slim = lambda t: {k: t.get(k) for k in ("text", "src", "gen", "why", "disc", "val", "hold", "live", "signals", "promotedOn", "proven", "certified", "certChecks", "status", "no", "plain", "twinOf")}
    return {"born": mem["born"], "total": mem["total_tests"], "runs": mem["runs"][:30], "events": mem["events"][:30], "lessons": mem.get("lessons", []),
            "ai": mem.get("ai", {}), "champions": [slim(t) for t in pick("champion")], "validated": [slim(t) for t in pick("validated")][:40],
            "near": [slim(t) for t in pick("near")][:40], "died_live": [slim(t) for t in pick("dead_live")][:20],
            "families": [[k, v["n"], round(v["best"], 2), v["ok"]] for k, v in fam[:80]], "nfam": len(fam), "status": status}

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research Machine</title><style>
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6a64;--line:#e4e2da;--good:#1f7a4d;--bad:#b23b2e;--warn:#9a6a00;--acc:#2f5fd0}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--card:#1c1c1a;--ink:#ecebe6;--mute:#9b9a93;--line:#2c2c29;--good:#4cc28a;--bad:#ff7a6b;--warn:#e0b04a;--acc:#7ea2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:20px 16px 60px}h1{font-size:26px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.mute{color:var(--mute)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.big{font-size:24px;font-weight:650}
.rule{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0}.rule b{font-weight:600}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);margin-right:6px}
.champion{color:var(--good);border-color:var(--good)}.validated{color:var(--acc);border-color:var(--acc)}.near{color:var(--warn);border-color:var(--warn)}.dead{color:var(--bad);border-color:var(--bad)}
.nums{font-size:13px;color:var(--mute);margin-top:4px}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line)}
.scroll{overflow-x:auto}details{margin:8px 0}summary{cursor:pointer;color:var(--acc)}ul{padding-left:20px}
</style></head><body><main>
<h1>Research Machine</h1><div class="mute" id="sub"></div>
<div class="grid" style="margin-top:16px" id="kpi"></div>
<div id="health"></div>
<h2>$500 account: trades only the champion of champions</h2>
<div id="box"></div>
<h2>$500 team account: up to 5 different champions, more money when they agree</h2>
<div id="tbox"></div>
<h2>The analyst: why the winners win, and which merges make sense</h2>
<div id="analyst"></div>
<h2>Invented building blocks: measurements the AI designed itself</h2>
<div id="blocks"></div>
<h2>Gambling lab: same trades, different betting systems</h2>
<div id="lab"></div>
<h2>Research trades (all champion rules, separate from the $500 account)</h2>
<p class="mute" id="acctnote"></p>
<div class="grid" id="acct"></div>
<details class="card" style="margin-top:10px"><summary id="rsum"></summary>
<div style="margin:10px 0" id="filt"></div>
<div class="scroll"><table id="trades"></table></div></details>
<h2>What happened recently</h2><div id="events"></div>
<h2>Champions — rules that passed every exam and are now being paper-traded</h2><div id="champs"></div>
<h2>Validated — passed discovery + validation, waiting for the final exam</h2><div id="val"></div>
<details><summary>Near misses (good in discovery, not yet convincing in validation)</summary><div id="near"></div></details>
<details><summary>Champions that failed live and were dropped</summary><div id="died"></div></details>
<details><summary>Twins (near-copies merged into a better champion)</summary><div id="twins"></div></details>
<h2>What it has learned</h2><div id="lessons"></div>
<h2>Idea families explored</h2><div class="scroll"><table id="fam"></table></div>
<h2>Daily runs</h2><div class="scroll"><table id="runs"></table></div>
<p class="mute" style="margin-top:30px">Paper only. Not financial advice. Every rule here is judged on data it was not found on; most ideas die, and that is the machine working honestly.</p>
</main><script>
const D=__DATA__;const $=id=>document.getElementById(id);const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const st=s=>s&&s.n?`${s.n} trades · avg ${s.mean>0?"+":""}${s.mean}% · win ${s.hit}% · t ${s.t}`:"no trades yet";
$("sub").textContent=`Running since ${D.born} UTC · data up to ${D.runs[0]?.dataEnd||"?"} · page built ${D.built}`;
const k=[["Ideas tested",D.total.toLocaleString()],["Champions",D.champions.length],["Validated",D.validated.length],["Near misses",D.near.length],["Idea families",D.nfam],["Systematic scan",D.runs[0]?.scanDone||"–"]];
$("kpi").innerHTML=k.map(([a,b])=>`<div class="card"><div class="mute">${a}</div><div class="big">${b}</div></div>`).join("");
$("events").innerHTML=D.events.length?"<ul>"+D.events.map(e=>`<li><span class="tag ${e[0]=="demoted"?"dead":"champion"}">${esc(e[0])}</span>${esc(e[1])}</li>`).join("")+"</ul>":'<p class="mute">Nothing promoted or dropped yet. Early on this is normal: the bar is high on purpose.</p>';
const rule=t=>`<div class="rule">${t.no?`<span class="tag champion">Rule #${t.no}</span>`:""}<span class="tag ${t.status.startsWith("dead")?"dead":t.status}">${esc(t.status)}</span><span class="tag">${esc(t.src)}${t.gen?" · gen "+t.gen:""}</span>${t.proven?'<span class="tag champion">proven live</span>':""}${t.certified?'<span class="tag champion">★ CERTIFIED</span>':""}
<div style="margin-top:6px;font-size:16px"><b>${esc(t.plain||t.text)}</b></div>${t.plain?`<details><summary>exact rule</summary><div class="mute">${esc(t.text)}</div></details>`:""}${t.why?`<div class="mute">${esc(t.why)}</div>`:""}
<div class="nums">discovery: ${st(t.disc)}<br>validation: ${st(t.val)}${t.val?.p_random!=null?` · random entries beat it ${Math.round(t.val.p_random*100)}% of the time`:""}${t.hold?`<br>final exam: ${st(t.hold)}`:""}${t.live?`<br><b>live paper since ${esc(t.promotedOn)}: ${st(t.live)}</b>`:""}</div>
${t.certChecks?`<details><summary>undeniable-edge checklist</summary><ul>${Object.entries(t.certChecks).map(([a,b])=>`<li>${b?"✔":"✘"} ${esc(a)}</li>`).join("")}</ul></details>`:""}
${t.signals?.length?`<div class="nums">recent signals: ${t.signals.map(s=>`${s.side} ${s.coin} @ ${s.px} (${new Date(s.t*1000).toISOString().slice(5,16).replace("T"," ")})`).join(" · ")}</div>`:""}</div>`;
const T=D.trades||[];const fmt=x=>x==null?"–":(x>=100?x.toLocaleString(undefined,{maximumFractionDigits:2}):Number(x.toPrecision(5)));
const when=s=>s?new Date(s*1000).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"–";
const sum=a=>a.reduce((x,r)=>x+r.usd,0);
const grp=ty=>{const a=T.filter(r=>r.type==ty);const w=a.filter(r=>r.usd>0).length;return [a.length,w,sum(a)]};
const [ln,lw,lu]=grp("live"),[en,ew,eu]=grp("exam"),[on,,ou]=grp("open");
$("acctnote").innerHTML=`Every trade uses <b>$${D.margin} margin at ${D.lev}x leverage</b> (a $${D.margin*D.lev} position); a loss can never be more than the $${D.margin} margin. Fees included. <b>Live</b> = trades after a rule was promoted. <b>Exam</b> = the last 4 months before promotion, data the rule never saw while being chosen. Prices come from Binance's public archive, about 1 day behind.`;
const money=x=>`<span style="color:var(--${x>=0?"good":"bad"})">${x>=0?"+":"−"}$${Math.abs(x).toFixed(2)}</span>`;
$("acct").innerHTML=[["Live trades",ln?`${ln} · ${lw} won`:"none yet"],["Live result",ln?money(lu):"–"],["Open now",on?`${on} · ${money(ou)} so far`:"none"],["Exam trades",en?`${en} · ${en?Math.round(100*ew/en):0}% won`:"–"],["Exam result",en?money(eu):"–"]].map(([a,b])=>`<div class="card"><div class="mute">${a}</div><div class="big" style="font-size:20px">${b}</div></div>`).join("");
const stats=a=>{const c=a.filter(r=>r.usd!=null);const w=c.filter(r=>r.usd>0),l=c.filter(r=>r.usd<=0);const tot=sum(c);
 const best=c.length?Math.max(...c.map(r=>r.usd)):0,worst=c.length?Math.min(...c.map(r=>r.usd)):0;
 return c.length?`<b>${c.length} trades</b> · ${w.length} won · ${l.length} lost · ${Math.round(100*w.length/c.length)}% win · total ${money(tot)} · avg ${money(tot/c.length)} · best ${money(best)} · worst ${money(worst)}`:"no trades yet"};
$("rsum").innerHTML=`Show / hide trades: `+stats(T.filter(r=>r.type!="open"));
// ── the $500 account ─────────────────────────────────────────────
const renderAcct=(A,box,isTeam)=>{if(!A){$(box).innerHTML='<p class="mute">The account starts on the next run.</p>';return}
 const ret=100*(A.equity/A.start-1);const C=A.closed;
 const svg=(()=>{const p=A.curve;if(p.length<2)return'<p class="mute">The equity chart appears after the first closed trade.</p>';
  const W=900,H=180,xs=p.map(q=>q[0]),ys=p.map(q=>q[1]),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys,A.start)*0.98,y1=Math.max(...ys,A.start)*1.02;
  const X=x=>((x-x0)/(x1-x0||1))*W,Y=y=>H-((y-y0)/(y1-y0||1))*H;const d=p.map((q,i)=>(i?"L":"M")+X(q[0]).toFixed(1)+","+Y(q[1]).toFixed(1)).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:180px"><line x1="0" x2="${W}" y1="${Y(A.start)}" y2="${Y(A.start)}" stroke="var(--line)" stroke-dasharray="4"/><path d="${d}" fill="none" stroke="var(--${A.equity>=A.start?"good":"bad"})" stroke-width="2"/></svg>`})();
 const row=r=>`<tr><td>#${r.rule??"?"}</td><td>${esc(r.coin)}</td><td>${r.side}</td><td>${when(r.t)}</td><td>${fmt(r.entry)}</td>${r.tp==null?`<td colspan=2 class=mute>none, exits on time</td>`:`<td>${fmt(r.tp)}</td><td>${fmt(r.sl)}</td>`}<td>$${r.margin} × ${D.lev}x</td>`;
 const openRows=A.open.map(r=>row(r)+`<td>${r.livePx?"live "+fmt(r.livePx):"–"}</td><td>${money(r.unreal||0)}</td></tr>`).join("");
 const closedRows=C.map(r=>row(r)+`<td>${fmt(r.exitPx)}<br><span class=mute>${when(r.exitT)}</span></td><td>${esc(r.how)}</td><td>${r.pct>0?"+":""}${r.pct}%</td><td>${money(r.usd)}</td><td>$${fmt(r.after)}</td></tr>`).join("");
 const H1="<tr><th>rule</th><th>coin</th><th>side</th><th>entered</th><th>entry</th><th>take-profit</th><th>stop-loss</th><th>margin</th>";
 $(box).innerHTML=`<div class="grid">${[["Account value",`$${A.equity.toFixed(2)}`],["Since start",`<span style="color:var(--${ret>=0?"good":"bad"})">${ret>=0?"+":""}${ret.toFixed(1)}%</span>`],["Closed trades",C.length?`${C.length} · ${C.filter(r=>r.usd>0).length} won`:"none yet"],["Open now",String(A.open.length)]].map(([a,b])=>`<div class="card"><div class="mute">${a}</div><div class="big" style="font-size:20px">${b}</div></div>`).join("")}</div>
 <div class="card" style="margin-top:10px">${isTeam?`<div class="mute">Team now: ${A.teamRules.length?A.teamRules.map(r=>`<div style="margin-top:4px"><b>Rule #${r.no}</b>: ${esc(r.text)}</div>`).join(""):"no team yet"}</div>
 <div class="mute" style="margin-top:6px;font-size:13px">Started ${esc(A.born)} UTC with $${A.start}. Each signal uses 5% of the account as margin at ${D.lev}x; when 2-3 team rules agree on the same trade, the position grows (up to 15%). Max 6 trades at once, profits compound. The team is re-picked every run.</div>`:
 `<div class="mute">Trading now: ${A.leaderNo?`<b>Rule #${A.leaderNo}</b>`:"no rule yet (waiting for a champion with 30+ trades after its exams)"}</div>${A.leaderText?`<div style="margin-top:4px">${esc(A.leaderText)}</div>`:""}
 <div class="mute" style="margin-top:6px;font-size:13px">Started ${esc(A.born)} UTC with $${A.start}. Each trade uses 10% of the account as margin at ${D.lev}x leverage, max 3 trades at once, profits compound. The best rule is re-picked every run from the newest results, and only used for hours that come after it was picked.</div>`}</div>
 <div class="card" style="margin-top:10px">${svg}</div>
 <details class="card" style="margin-top:10px" ${A.open.length?"open":""}><summary>Open positions: <b>${A.open.length}</b> · unrealised ${money(A.open.reduce((x,r)=>x+(r.unreal||0),0))}</summary><div class="scroll"><table>${A.open.length?H1+"<th>now</th><th>$ so far</th></tr>"+openRows:'<tr><td class="mute">None.</td></tr>'}</table></div></details>
 <details class="card" style="margin-top:10px"><summary>Closed trades: ${stats(C)}</summary><div class="scroll"><table>${C.length?H1+"<th>exit</th><th>how it ended</th><th>move</th><th>$ result</th><th>account after</th></tr>"+closedRows:'<tr><td class="mute">None yet.</td></tr>'}</table></div></details>
 <details class="card" style="margin-top:10px"><summary>Rule changes: ${A.leaders.length}</summary><ul>${A.leaders.map(l=>`<li>${esc(l.at)} → <b>${isTeam?"":"Rule #"}${esc(l.rule)}</b>: ${esc(l.why)}<br><span class=mute>${esc(l.text)}</span></li>`).join("")||"<li class=mute>none</li>"}</ul></details>`};
renderAcct(D.account,"box",false);renderAcct(D.team,"tbox",true);
// health
(()=>{const h=(Date.now()-Date.parse(D.built.replace(" UTC","Z").replace(" ","T")))/36e5;const de=D.runs[0]?.dataEnd;const dd=de?(Date.now()-Date.parse(de.replace(" ","T")+"Z"))/36e5:0;
 $("health").innerHTML=h>4?`<div class="card" style="border-color:var(--bad);color:var(--bad);margin-top:14px"><b>⚠ The machine has not run for ${Math.round(h)} hours</b> (it should run every hour). Something may be stuck: tell Claude.</div>`:
 dd>72?`<div class="card" style="border-color:var(--warn);color:var(--warn);margin-top:14px"><b>⚠ Market data is ${Math.round(dd/24)} days old</b>: the data source may have changed. Tell Claude.</div>`:
 `<div class="card" style="border-color:var(--good);margin-top:14px"><span style="color:var(--good)">●</span> Healthy: last run ${Math.max(0,Math.round(h*60))} min ago, runs every hour. Market data up to ${esc(de||"?")} UTC.</div>`})();
// analyst
(()=>{const A=D.analyst;if(!A){$("analyst").innerHTML='<p class="mute">The analyst starts once there are at least 2 winning rules.</p>';return}
 $("analyst").innerHTML=`<div class="card"><div class="mute">Written ${esc(A.at)} UTC by ${esc(A.model)}</div>
 ${A.why?.length?"<h3 style='font-size:15px;margin:10px 0 4px'>Why the winners might win</h3><ul>"+A.why.map(w=>`<li><b>${esc(w.rule)}</b>: ${esc(w.reason)}</li>`).join("")+"</ul>":""}
 ${A.merges?.length?"<h3 style='font-size:15px;margin:10px 0 4px'>Merges it proposed (each is tested like any other idea)</h3><ul>"+A.merges.map(m=>`<li>${esc(m.why)}${m.valid?"":" <span class=mute>(could not be built, skipped)</span>"}</li>`).join("")+"</ul>":"<p class=mute>No merge made sense this time.</p>"}
 ${A.wild?.length?"<h3 style='font-size:15px;margin:10px 0 4px'>Wild ideas (gambling concepts, sentiment extremes)</h3><ul>"+A.wild.map(m=>`<li>${esc(m.why)}${m.valid?"":" <span class=mute>(could not be built, skipped)</span>"}</li>`).join("")+"</ul>":""}</div>`})();
// gambling lab
(()=>{const L=D.lab;if(!L){$("lab").innerHTML='<p class="mute">Appears once the best rule has 20+ finished trades.</p>';return}
 $("lab").innerHTML=`<p class="mute">Rule #${L.rule}'s ${L.n} trades (final exam + live), replayed with different betting systems: $500 start, ${D.lev}x leverage, one trade at a time. "Shuffled" = the same trades in 500 random orders, because the order of wins and losses is luck. A betting system can never change the odds of each trade; it only changes the ride.</p>
 <div class="scroll"><table><tr><th>betting system</th><th>real order: final $</th><th>worst drop</th><th>shuffled: typical final $</th><th>bad luck (5%)</th><th>good luck (95%)</th><th>chance of going broke</th></tr>
 ${L.rows.map(r=>`<tr><td>${esc(r.system)}</td><td>$${r.real} (${money(r.real-500)})</td><td>${r.realDD}%</td><td>$${r.median}</td><td>$${r.bad5}</td><td>$${r.good95}</td><td style="color:var(--${r.ruin>5?"bad":"ink"})">${r.ruin}%</td></tr>`).join("")}</table></div>`})();
(()=>{const B=D.blocks||[],R=D.blocksRetired||[];
 $("blocks").innerHTML=`<p class="mute">The AI writes new measurements as small formulas (it can't run code). The machine checks and computes each one, tests it in up to 40 standard ways, keeps the ones that produce good rules and retires the useless ones after 60 tests. Exits are invented the same way: take-profit, stop-loss, trailing stop and holding time are dials the machine sets itself.</p>
 <details class="card" open><summary>In use: <b>${B.length}</b></summary>${B.length?"<div class=scroll><table><tr><th>name</th><th>formula</th><th>why</th><th>tests</th><th>best result</th></tr>"+B.map(b=>`<tr><td>${esc(b.name||b.id)}</td><td><code>${esc(b.expr)}</code></td><td>${esc(b.why)}</td><td>${b.tests||0}</td><td>${esc(b.best||"-")}</td></tr>`).join("")+"</table></div>":'<p class="mute">None yet: the first ones are invented at the next run.</p>'}</details>
 <details class="card" style="margin-top:10px"><summary>Retired: ${R.length}</summary>${R.map(b=>`<div class="nums"><code>${esc(b.expr)}</code> · ${b.tests} tests · best: ${esc(b.best)}</div>`).join("")}</details>`})();
$("twins").innerHTML=(D.twins||[]).map(t=>`<div class="rule"><span class="tag">twin of #${t.of}</span> ${esc(t.text)}</div>`).join("")||'<p class="mute">None.</p>';
let F="all";const draw=()=>{const a=T.filter(r=>F=="all"||r.type==F).slice(0,200);
$("trades").innerHTML=a.length?"<tr><th>rule</th><th>coin</th><th>side</th><th>entered</th><th>entry</th><th>take-profit</th><th>stop-loss</th><th>exit</th><th>how it ended</th><th>move</th><th>$ at "+D.lev+"x</th><th></th></tr>"+a.map(r=>`<tr><td>#${String(r.no??"?").replace(/,/g,", #")}</td><td>${esc(r.coin)}</td><td>${r.side}</td><td>${when(r.t)}</td><td>${fmt(r.entry)}</td>${r.tp==null?`<td colspan=2 class=mute>none, exits on time</td>`:`<td>${fmt(r.tp)}</td><td>${fmt(r.sl)}</td>`}<td>${r.how=="open"?"now "+fmt(r.exitPx):fmt(r.exitPx)+"<br><span class=mute>"+when(r.exitT)+"</span>"}</td><td>${esc(r.how)}</td><td>${r.pct>0?"+":""}${r.pct}%</td><td>${money(r.usd)}</td><td><span class="tag ${r.type=="live"?"champion":r.type=="open"?"validated":""}">${r.type}</span></td></tr>`).join(""):'<tr><td class="mute">No trades yet. Trades appear here as soon as a rule becomes a champion.</td></tr>'};
$("filt").innerHTML=["all","live","open","exam"].map(f=>`<button data-f="${f}" style="margin-right:6px;padding:4px 10px;border-radius:99px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer">${f}</button>`).join("");
document.querySelectorAll("#filt button").forEach(b=>b.onclick=()=>{F=b.dataset.f;draw()});draw();
$("champs").innerHTML=D.champions.length?D.champions.map(rule).join(""):'<p class="mute">None yet.</p>';
$("val").innerHTML=D.validated.length?D.validated.map(rule).join(""):'<p class="mute">None yet.</p>';
$("near").innerHTML=D.near.map(rule).join("")||'<p class="mute">None.</p>';
$("died").innerHTML=D.died_live.map(rule).join("")||'<p class="mute">None.</p>';
$("lessons").innerHTML=D.lessons.length?"<ul>"+D.lessons.map(l=>`<li>${esc(l)}</li>`).join("")+"</ul>":'<p class="mute">The AI researcher writes lessons once a week (needs the GROQ_API_KEY secret).</p>';
$("fam").innerHTML="<tr><th>family</th><th>tests</th><th>best t</th><th>survivors</th></tr>"+D.families.map(f=>`<tr><td>${esc(f[0])}</td><td>${f[1]}</td><td>${f[2]}</td><td>${f[3]}</td></tr>`).join("");
$("runs").innerHTML="<tr><th>run (UTC)</th><th>tested</th><th>result</th><th>new validated</th><th>secs</th></tr>"+D.runs.map(r=>`<tr><td>${esc(r.at)}</td><td>${r.tested}</td><td>${esc(Object.entries(r.counts).map(([a,b])=>a+" "+b).join(", "))}</td><td>${r.validated}</td><td>${r.secs}</td></tr>`).join("");
</script></body></html>"""

def _trades(mem, J, log=print):
    """every trade each champion took: 'exam' = the last 4 months before it was promoted (data it never saw while being chosen),
       'live' = after promotion. Paper account: $MARGIN per trade at LEVERAGE x; a loss can never exceed the margin (liquidation)."""
    rows = []
    for t in mem["tests"].values():
        if t["status"] != "champion": continue
        try: log_ = J.trade_log(t["h"], J.hold0, limit=80)
        except Exception as e: log(f"trade log failed: {e}"); continue
        for x in log_:
            usd = max(-C.PAPER_MARGIN, C.PAPER_MARGIN * C.LEVERAGE * x["ret"])
            rows.append({**x, "no": t.get("no"), "type": "open" if x["how"] == "open" else ("live" if x["t"] > t.get("promotedAt", 0) else "exam"),
                         "pct": round(x["ret"] * 100, 2), "usd": round(usd, 2)})
    # several champions are often near-copies of one idea and fire the same trade: count it once, list all its rules
    merged = {}
    for r in rows:
        key = (r["coin"], r["t"], r["side"], r.get("exitT"), round(r["ret"], 6))
        if key in merged: merged[key]["no"] = f'{merged[key]["no"]},{r["no"]}'
        else: merged[key] = dict(r)
    rows = sorted(merged.values(), key=lambda r: -r["t"])
    return rows[:500]

def write(mem, J=None, log=print):
    os.makedirs(C.DOCS_DIR, exist_ok=True)
    d = _summary(mem); d["built"] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    d["trades"] = _trades(mem, J, log) if J is not None else []; d["margin"] = C.PAPER_MARGIN; d["lev"] = C.LEVERAGE
    for key in ("account", "team"):
        A = mem.get(key)
        if not A: continue
        lead = mem["tests"].get(A.get("leader") or "", {})
        tm = [mem["tests"][k] for k in (A.get("team") or []) if k in mem["tests"]]
        d[key] = {"teamRules": [{"no": t.get("no"), "text": t.get("plain") or t["text"]} for t in tm],"born": A["born"], "t0": A["t0"], "cash": A["cash"], "equity": A.get("equity", A["cash"]), "start": 500.0,
                        "open": [{k: v for k, v in p.items() if k not in ("sd", "_w")} for p in A["open"]], "closed": A["closed"][::-1][:300],
                        "curve": A["curve"], "leaders": A["leaders"][::-1], "leaderNo": lead.get("no"), "leaderText": lead.get("plain") or lead.get("text")}
    d["analyst"] = mem.get("analyst")
    d["blocks"] = [{k: b.get(k) for k in ("id", "name", "expr", "why", "tests", "best", "at", "by")} for b in mem.get("blocks", [])]
    d["blocksRetired"] = (mem.get("blocks_retired") or [])[::-1][:40]
    d["twins"] = [{"text": t.get("plain") or t["text"], "of": t.get("twinOf")} for t in mem["tests"].values() if t["status"] == "twin"][:40]
    d["lab"] = None
    try:
        from . import lab
        lead = mem["tests"].get((mem.get("account") or {}).get("leader") or "")
        if lead and J is not None:
            tl = sorted([x for x in J.trade_log(lead["h"], J.hold0, limit=2000) if x["how"] != "open"], key=lambda x: x["t"])
            seq, free = [], -1                     # one trade at a time: the next trade must start after the previous one ended
            for x in tl:
                if x["t"] >= free: seq.append(x["ret"]); free = x["exitT"]
            res = lab.run(seq)
            if res: d["lab"] = {**res, "rule": lead.get("no")}
    except Exception as e: log(f"lab failed: {e}")
    blob = json.dumps(d, separators=(",", ":")).replace("</", "<\\/")
    with open(f"{C.DOCS_DIR}/index.html", "w", encoding="utf-8") as f: f.write(PAGE.replace("__DATA__", blob))
    with open(f"{C.DOCS_DIR}/summary.json", "w", encoding="utf-8") as f: json.dump(d, f)
    log(f"report: {C.DOCS_DIR}/index.html")

def _send(text, log=print):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: return
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=30,
                          json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
        if not r.ok: log(f"telegram: HTTP {r.status_code}")
    except Exception as e: log(f"telegram: {e}")

def alert(certified, log=print):
    """the ONLY message this machine ever sends: a rule passed the undeniable bar"""
    url = os.environ.get("RM_PAGE_URL", "")
    for t in certified:
        L = t.get("live") or {}
        msg = (f"🟢 <b>Research Machine: a rule passed every test</b>\n\n{html.escape(t['text'])}\n\n"
               f"Live paper record: {L.get('n')} trades, avg {L.get('mean')}% per trade, win {L.get('hit')}%, t {L.get('t')}\n\n"
               + "\n".join("✔ " + html.escape(c) for c in t.get("certChecks", {})) + (f"\n\n{url}" if url else ""))
        _send(msg, log)
