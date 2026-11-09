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
    slim = lambda t: {k: t.get(k) for k in ("text", "src", "gen", "why", "disc", "val", "hold", "live", "signals", "promotedOn", "proven", "certified", "certChecks", "status", "no", "plain", "twinOf", "evidence")}
    by_text = {t["text"]: t for t in mem["tests"].values()}
    ev = [[e, (by_text.get(x) or {}).get("plain") or x, (by_text.get(x) or {}).get("no")] for e, x in mem["events"][:12]]
    return {"born": mem["born"], "total": mem["total_tests"], "runs": mem["runs"][:30], "events": ev, "lessons": mem.get("lessons", []),
            "ai": mem.get("ai", {}), "champions": [slim(t) for t in pick("champion")], "validated": [slim(t) for t in pick("validated")][:40],
            "near": [slim(t) for t in pick("near")][:40], "died_live": [slim(t) for t in pick("dead_live")][:20],
            "families": [[k, v["n"], round(v["best"], 2), v["ok"]] for k, v in fam[:80]], "nfam": len(fam), "status": status}

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research Machine</title><style>
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6a64;--line:#e4e2da;--good:#1f7a4d;--bad:#b23b2e;--warn:#9a6a00;--acc:#2f5fd0;--soft:#efede6}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--card:#1c1c1a;--ink:#ecebe6;--mute:#9b9a93;--line:#2c2c29;--good:#4cc28a;--bad:#ff7a6b;--warn:#e0b04a;--acc:#7ea2ff;--soft:#242422}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:18px 16px 60px}h1{font-size:24px;margin:0}h2{font-size:17px;margin:26px 0 8px}h3{font-size:15px;margin:14px 0 6px}
.mute{color:var(--mute)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.big{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums}
.rule{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0}.rule b{font-weight:600}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);margin-right:6px}
.champion{color:var(--good);border-color:var(--good)}.validated{color:var(--acc);border-color:var(--acc)}.near{color:var(--warn);border-color:var(--warn)}.dead{color:var(--bad);border-color:var(--bad)}
.nums{font-size:13px;color:var(--mute);margin-top:4px}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.scroll{overflow-x:auto}details{margin:8px 0}summary{cursor:pointer;color:var(--acc)}ul{padding-left:20px}code{font-size:12px}
.top{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
.pill{display:inline-flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--line);border-radius:99px;padding:6px 14px;font-size:14px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--good)}.cd{font-variant-numeric:tabular-nums;font-weight:650}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:16px 0 4px;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:5;border-bottom:1px solid var(--line)}
.tabs button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:7px 14px;border-radius:99px;cursor:pointer;font:inherit;font-size:14px}
.tabs button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
section[data-tab]{display:none}section[data-tab].on{display:block}
.story{font-size:17px;line-height:1.6}.story b{font-weight:650}
.help{font-size:13px;color:var(--mute);margin:-4px 0 10px}
.mini{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}
/* exchange-style positions */
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;counter-reset:st}
.flow .card{position:relative;padding-top:34px}.flow .card:before{counter-increment:st;content:counter(st);position:absolute;top:10px;left:14px;width:20px;height:20px;border-radius:50%;background:var(--acc);color:#fff;font-size:12px;font-weight:700;display:grid;place-items:center}
.flow .card>b{display:block;margin-bottom:4px}.flow .card p{margin:0;font-size:13.5px;color:var(--mute)}
.grades{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.grades .card{border-top:4px solid var(--gc)}.grades h4{margin:0 0 6px;font-size:16px;color:var(--gc)}
.grades ul,.gl ul{margin:6px 0 0;padding-left:18px;font-size:13.5px}.grades li,.gl li{margin:3px 0}
.gl{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}.gl .card>b{display:block}.gl .card p{margin:4px 0 0;font-size:13.5px;color:var(--mute)}
.howh{margin:18px 0 8px;font-size:15px}
.cands{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:8px}
.cand{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}.cand.go{border-color:var(--good)}
.cand .ch{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.cand .ch .odds{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:700}
.cand .ch .odds small{font-weight:400;color:var(--mute);font-size:12px}.cand .lv{font-size:12.5px;color:var(--mute);margin:6px 0 4px;font-variant-numeric:tabular-nums}
.exam{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:8px;margin-top:8px}.exam .card{padding:10px 12px}
.exam .kv{display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;font-size:13.5px;margin-top:6px}.exam .kv span{color:var(--mute)}
.rm h3{margin:16px 0 6px;font-size:15.5px}.rm h3:first-child{margin-top:0}.rm ul{list-style:none;padding-left:0;margin:4px 0}.rm li{padding:4px 0 4px 26px;position:relative;font-size:14px;border-bottom:1px solid var(--line)}
.rm li:before{content:"○";position:absolute;left:4px;color:var(--mute)}.rm li.done:before{content:"✓";color:var(--good);font-weight:700}.rm li.done{color:var(--mute)}.rm p{font-size:13.5px;margin:6px 0}
.xp{background:var(--card);border:1px solid var(--line);border-radius:14px;margin-top:12px;overflow:hidden}
.xbar{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:2px 14px;padding:12px 14px;border-bottom:1px solid var(--line);background:var(--soft)}
.xbar div span{display:block;font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.4px}.xbar div b{font-variant-numeric:tabular-nums;font-size:15px}
.xtabs{display:flex;gap:2px;padding:0 10px;border-bottom:1px solid var(--line);overflow-x:auto}.xtabs button{background:none;border:0;border-bottom:2px solid transparent;padding:10px 10px 8px;font:inherit;font-size:13.5px;color:var(--mute);cursor:pointer;white-space:nowrap}
.xtabs button.on{color:var(--ink);border-bottom-color:var(--acc);font-weight:650}.xbody{padding:4px 12px 12px}.xbody .pos{border-radius:10px}
.pos{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:10px 0;position:relative;overflow:hidden}
.pos:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--sidec)}
.pos.L{--sidec:var(--good)}.pos.S{--sidec:var(--bad)}
.ph{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px}.sym{font-weight:700;font-size:16px;letter-spacing:.2px}.sym small{font-weight:500;color:var(--mute);font-size:12px;margin-left:2px}
.bdg{font-size:11.5px;font-weight:650;padding:2px 8px;border-radius:6px;background:var(--soft);color:var(--mute)}
.bdg.L{background:color-mix(in srgb,var(--good) 16%,transparent);color:var(--good)}.bdg.S{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}
.bdg.A{background:color-mix(in srgb,var(--acc) 16%,transparent);color:var(--acc)}
.pnlbox{margin-left:auto;text-align:right;font-variant-numeric:tabular-nums}.pnlbox .v{font-size:19px;font-weight:700;display:block;line-height:1.2}.pnlbox .r{font-size:12.5px;font-weight:600}
.pg{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px 14px;margin-top:10px}
.pg div span{display:block;font-size:11.5px;color:var(--mute);text-transform:uppercase;letter-spacing:.4px}.pg div b{font-weight:600;font-variant-numeric:tabular-nums;font-size:14px}
.liq b{color:var(--warn)}
.track{margin-top:12px}.rail{position:relative;height:8px;border-radius:99px;background:linear-gradient(90deg,color-mix(in srgb,var(--bad) 35%,transparent),var(--soft) 50%,color-mix(in srgb,var(--good) 35%,transparent))}
.rail .ent{position:absolute;top:-3px;width:2px;height:14px;background:var(--mute)}.rail .mk{position:absolute;top:-4px;width:16px;height:16px;margin-left:-8px;border-radius:50%;background:var(--card);border:3px solid var(--ink);transition:left .6s}
.tl{display:flex;justify-content:space-between;font-size:11.5px;color:var(--mute);margin-top:5px;font-variant-numeric:tabular-nums}
.ev{font-size:12.5px;color:var(--mute);margin-top:8px}.ev b{color:var(--ink);font-weight:600}
.hist td,.hist th{white-space:nowrap}.hist th{font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--mute);font-weight:600}
.fam{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:99px;margin:1px 2px;border:1px solid var(--line)}.fam.y{color:var(--good);border-color:var(--good)}.fam.n{color:var(--bad);border-color:var(--bad)}
.meter{height:6px;background:var(--soft);border-radius:99px;position:relative;min-width:90px}.meter i{position:absolute;left:0;top:0;bottom:0;border-radius:99px;background:var(--acc)}.meter u{position:absolute;top:-3px;width:2px;height:12px;background:var(--ink)}
</style></head><body><main>
<div class="top"><div><h1>Research Machine</h1><div class="mute" id="sub" style="font-size:13px"></div></div>
<div class="pill" id="statuspill"><span class="dot" id="sdot"></span><span id="stext"></span><span class="mute">·</span><span>next run in <span class="cd" id="cd">–</span></span></div></div>
<div id="health"></div>
<nav class="tabs" id="tabs"><button data-t="home">Overview</button><button data-t="accounts">Accounts</button><button data-t="rules">Rules</button><button data-t="research">How it thinks</button><button data-t="data">Trades &amp; data</button><button data-t="roadmap">Build board</button><button data-t="help">Help</button></nav>

<section data-tab="home">
<div class="card story" id="story" style="margin-top:12px"></div>
<div id="hcheck"></div>
<h2>Signals now</h2><div id="signals"></div>
<div class="grid" style="margin-top:12px" id="kpi"></div>
<h2>The paper accounts</h2>
<div class="mini" id="minis"></div>
<h2>What happened recently</h2><div id="events"></div>
</section>

<section data-tab="accounts">
<h2>The referee: one trade a day, every kind of evidence</h2>
<p class="help">One brain instead of many rules. For each of the ~40 most liquid coins it asks "will this reach +2% before its stop?", long and short. Seven independent kinds of evidence vote; it trades only when the combined odds are clearly above break-even <b>and</b> at least two kinds agree. At most one trade a day; most days it may skip. At +2% half is taken and the rest becomes risk-free; the rest is held while the evidence says the move continues.</p>
<div id="rbox"></div>
<h2>Monthly report</h2><div id="monthly"></div>
<h2>$500 account: trades only the best rule</h2>
<p class="help">It always trades the single rule with the best record on data it never saw while being chosen. It switches when a better one appears.</p>
<div id="box"></div>
<h2>$500 team account: several different rules together</h2>
<p class="help">Up to 5 rules that trade <i>different</i> situations. When two or three agree on the same trade, it puts in more money.</p>
<div id="tbox"></div>
<h2>$700 all-in account: no leverage, one coin at a time</h2>
<p class="help">Your idea: the whole account goes into one coin for 1-3 days, aiming for 5-10% moves, then rotates to the next pick. It trades the best "rotation" rule that has passed every exam.</p>
<div id="abox"></div>
</section>

<section data-tab="rules">
<h2>Champions</h2><p class="help">Rules that passed every exam. They are paper-traded and must keep earning to stay.</p><div id="champs"></div>
<h2>Waiting for the final exam</h2><p class="help">Passed the first two exams; they still need to pass the newest 4 months.</p><div id="val"></div>
<details><summary>Near misses (looked good on old data, not yet on newer data)</summary><div id="near"></div></details>
<details><summary>Twins (near-copies merged into a better champion)</summary><div id="twins"></div></details>
<details><summary>Dropped champions (failed with live trades)</summary><div id="died"></div></details>
</section>

<section data-tab="research">
<h2>How the referee decides</h2><div id="howref"></div>
<h2>The analyst: why winners win, and which merges make sense</h2><div id="analyst"></div>
<h2>Invented building blocks</h2><div id="blocks"></div>
<h2>What it has learned</h2><div id="lessons"></div>
</section>

<section data-tab="data">
<h2>Research trades (every champion rule, separate from the accounts)</h2>
<p class="mute" id="acctnote"></p>
<div class="grid" id="acct"></div>
<details class="card" style="margin-top:10px"><summary id="rsum"></summary>
<div style="margin:10px 0" id="filt"></div>
<div class="scroll"><table id="trades"></table></div></details>
<h2>Runs</h2><div class="scroll"><table id="runs"></table></div>
<details><summary>Idea families explored</summary><div class="scroll"><table id="fam"></table></div></details>
</section>

<section data-tab="roadmap"><h2>Build board</h2><p class="mute">What is built, what comes next, and what is waiting for evidence. Updated with every build.</p><div id="roadmap" class="card rm"></div></section>
<section data-tab="help">
<h2>How to read this page</h2>
<div class="card">
<p><b>What the machine does:</b> every hour it invents and tests trading ideas on 6 years of hourly data for about 50 coins. Nearly all of them fail. The few that pass become <b>champions</b> and are traded with <b>paper money</b> (never real money).</p>
<p><b>The three exams:</b> (1) <i>discovery</i>: the older years, where ideas may be found; (2) <i>validation</i>: the next year, which the idea must also win, and beat random trading; (3) <i>final exam</i>: the newest 4 months, used once. Only then is a rule a champion.</p>
<p><b>Live vs exam trades:</b> <i>exam</i> trades happened before a rule was promoted (a test). <i>Live</i> trades happened after: the real proof.</p>
<p><b>"t" numbers:</b> how far a result is from luck. Around 2 is "probably not luck", 3+ is strong. Win rate alone can fool you.</p>
<p><b>Why trades appear a day late:</b> the data comes from Binance's free archive, published once a day. Each trade still uses the real price of the hour it fired, so the results are exact; they just show up later.</p>
<p><b>Twins:</b> two rules that trade almost the same moments; only the better one is kept.</p>
<p><b>Invented building blocks:</b> new measurements and exits the AI designed itself. They're tested like everything else and retired if useless.</p>
<p><b>When to worry:</b> the status at the top turns red if the machine hasn't run for more than 4 hours.</p>
<p class="mute">Paper only. Not financial advice. Most ideas die: that is the machine working honestly.</p></div>
</section>
</main><script>
const D=__DATA__;const $=id=>document.getElementById(id);const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const st=s=>s&&s.n?`${s.n} trades · avg ${s.mean>0?"+":""}${s.mean}% · win ${s.hit}% · t ${s.t}${s.avgWin!=null?` · avg win +${s.avgWin}% / avg loss ${s.avgLoss}% (payoff ${s.payoff})`:""}${s.levMean!=null?` · <b>as traded (${s.lev}x): ${s.levMean>0?"+":""}${s.levMean}% of margin per trade${s.liq?`, ${s.liq} liquidations`:""}</b>`:""}`:"no trades yet";
$("sub").textContent=(()=>{const de=D.runs[0]?.dataEnd;if(!de)return`Paper-trading research lab · running since ${D.born} UTC`;const a=new Date(de.replace(" ","T")+"Z"),b=new Date(a.getTime()+36e5),f=d=>d.toISOString().slice(11,16);
 return `Paper-trading research lab · running since ${D.born} UTC · newest complete hour: ${de.slice(0,10)} ${f(a)}–${f(b)} UTC (each hourly run uses the hour that just closed)`})();
const A1=D.account,A2=D.team,fmtUsd=x=>"$"+(x??0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const pct=A=>A?100*(A.equity/A.start-1):0, sgn=x=>(x>=0?"+":"")+x.toFixed(1)+"%";
const k=[["Ideas tested",D.total.toLocaleString(),"since the start"],["Champions",D.champions.length,"rules good enough to paper-trade"],
 ["Best-rule account",A1?fmtUsd(A1.equity):"–",A1?sgn(pct(A1))+" since start":""],["Team account",A2?fmtUsd(A2.equity):"–",A2?sgn(pct(A2))+" since start":""]];
$("kpi").innerHTML=k.map(([a,b,c])=>`<div class="card"><div class="mute">${a}</div><div class="big">${b}</div><div class="mute" style="font-size:13px">${c}</div></div>`).join("");
(()=>{const c1=A1?A1.closed.length:0,w1=A1?A1.closed.filter(r=>r.usd>0).length:0,o1=A1?A1.open.length:0;
 const lead=A1&&A1.leaderNo?`It is currently trading <b>Rule #${A1.leaderNo}</b>: ${esc(A1.leaderText||"")}`:"It is waiting for a rule with enough of a track record.";
 const lastRun=D.runs[0];
 $("story").innerHTML=`So far the machine has tested <b>${D.total.toLocaleString()} ideas</b>; <b>${D.champions.length}</b> passed every exam.
 The best-rule account is at <b>${A1?fmtUsd(A1.equity):"$500"}</b> (${sgn(pct(A1))}) after <b>${c1}</b> closed trade${c1==1?"":"s"}${c1?` (${w1} won)`:""}${o1?`, with ${o1} open now`:""}.
 The team account is at <b>${A2?fmtUsd(A2.equity):"$500"}</b> (${sgn(pct(A2))}).<br><span style="font-size:15px">${lead}</span>
 <div class="mute" style="font-size:13px;margin-top:8px">Last run tested ${lastRun?lastRun.tested.toLocaleString():0} ideas${lastRun&&lastRun.validated?`, ${lastRun.validated} passed validation`:""}. ${c1<30?"Judge the accounts only after 30+ closed trades; early results are mostly luck.":""}</div>`;
 const mini=(A,title)=>{if(!A)return`<div class="card"><b>${title}</b><p class="mute">Starts on the next run.</p></div>`;
  const p=A.curve,W=300,H=60;let path="";if(p.length>1){const xs=p.map(q=>q[0]),ys=p.map(q=>q[1]),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys,A.start),y1=Math.max(...ys,A.start);
   path=p.map((q,i)=>(i?"L":"M")+((q[0]-x0)/(x1-x0||1)*W).toFixed(1)+","+(H-(q[1]-y0)/(y1-y0||1)*H).toFixed(1)).join("")}
  const cl=A.closed,w=cl.filter(r=>r.usd>0).length;
  return `<div class="card"><div style="display:flex;justify-content:space-between"><b>${title}</b><span style="color:var(--${A.equity>=A.start?"good":"bad"})">${sgn(pct(A))}</span></div>
  <div class="big">${fmtUsd(A.equity)}</div><div class="mute" style="font-size:13px">${cl.length} closed · ${w} won · ${cl.length-w} lost · ${A.open.length} open</div>
  ${path?`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:60px;margin-top:6px"><path d="${path}" fill="none" stroke="var(--${p[p.length-1][1]>=A.start?"good":"bad"})" stroke-width="2"/></svg><div class="mute" style="font-size:12px">closed trades only${A.open.length?"; open trades are counted in the value above":""}</div>`:'<div class="mute" style="font-size:13px;margin-top:6px">Chart appears after the first closed trade.</div>'}
  <a href="#accounts" data-go="accounts" style="font-size:13px;color:var(--acc)">See trades →</a></div>`};
 $("minis").innerHTML=mini(D.referee&&D.referee.acct,"The referee (1 trade/day, 10x)")+mini(A1,"Best-rule account (10x)")+mini(A2,"Team account (10x)")+mini(D.allin,"All-in account ($700, no leverage)")})();
(()=>{const fmt=x=>x==null?"–":(x>=100?x.toLocaleString(undefined,{maximumFractionDigits:2}):Number(x.toPrecision(5)));const when=s=>s?new Date(s*1000).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}):"–";
 const S=D.signals||[],L=D.live||{};const age=D.newest?Math.round((Date.now()/1000-D.newest-3600)/60):null;
 const fresh=L.coins?`Live data from Binance (via our own relay server), newest complete hour closed ${age!=null?Math.max(0,age-60)+" min ago":"?"}. These are signals from the research rules that the best-rule account trades, <b>not</b> the referee's decisions (those are on the Accounts tab).`:`<span style="color:var(--warn)">The live relay is not connected yet: data is about a day behind, so these signals are too late to trade.</span>`;
 $("signals").innerHTML=`<p class="mute" style="font-size:13px">${fresh} Entries fired in the last 3 hours by the rules the accounts are trading. Paper only: this is not financial advice.</p>`+
 (S.length?`<div class="scroll"><table><tr><th>rule</th><th>coin</th><th>side</th><th>fired</th><th>entry</th><th>stop-loss</th><th>take-profit</th><th>max hold</th></tr>`+S.map(x=>`<tr><td>#${x.no??x.rule}</td><td><b>${esc(x.coin)}</b></td><td>${x.side}</td><td>${when(x.t)}</td><td>${fmt(x.entry)}</td><td>${x.sl?fmt(x.sl):(x.trail?"trailing "+(x.trail*100).toFixed(1)+"% below the best price":"time exit only")}</td><td>${x.tp?fmt(x.tp):(x.trail?"none: rides the trail":"none")}</td><td>${x.hold}h${x.flips?' <span class="tag near" title="this rule gave the opposite side on this coin within the last 3 hours">flips</span>':""}</td></tr>`).join("")+"</table></div>":'<p class="mute">No signals in the last 3 hours.</p>')})();
// tabs (remembered per browser)
const tabs=[...document.querySelectorAll("#tabs button")];const show=t=>{tabs.forEach(b=>b.classList.toggle("on",b.dataset.t==t));document.querySelectorAll("section[data-tab]").forEach(x=>x.classList.toggle("on",x.dataset.tab==t));try{localStorage.setItem("tab",t)}catch(e){}};
tabs.forEach(b=>b.onclick=()=>{show(b.dataset.t);history.replaceState(null,"","#"+b.dataset.t)});
document.addEventListener("click",e=>{const g=e.target.closest("[data-go]");if(g){e.preventDefault();show(g.dataset.go);window.scrollTo(0,0)}});
let first=location.hash.slice(1);try{first=first||localStorage.getItem("tab")}catch(e){};show(tabs.some(b=>b.dataset.t==first)?first:"home");
// status + countdown to the next run (runs at minute 17 of every hour, UTC; GitHub may start a few minutes late)
const builtMs=Date.parse(D.built.replace(" UTC","Z").replace(" ","T"));
const tick=()=>{const now=Date.now(),ago=(now-builtMs)/6e4;const n=new Date(now);const nx=new Date(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate(),n.getUTCHours(),17,0));if(nx<=n)nx.setUTCHours(nx.getUTCHours()+1);
 const sec=Math.round((nx-n)/1000),late=ago>70;
 $("cd").textContent=sec>3300&&ago>8?"running now…":`${Math.floor(sec/60)}:${String(sec%60).padStart(2,"0")}`;
 $("stext").innerHTML=late?`<b style="color:var(--bad)">Late</b>: last run ${Math.round(ago/60*10)/10} h ago`:`Healthy · last run ${ago<1?"just now":Math.round(ago)+" min ago"}`;
 $("sdot").style.background=`var(--${ago>240?"bad":late?"warn":"good"})`};tick();setInterval(tick,1000);
const EV={promoted:["became a champion","champion"],demoted:["dropped: lost money live","dead"],"failed re-exam":["dropped: failed a re-exam on newer data","dead"],proven:["proven with live trades","champion"],certified:["passed the undeniable-edge checklist","champion"]};
$("events").innerHTML=D.events.length?D.events.map(e=>`<div class="rule"><span class="tag ${(EV[e[0]]||["",""])[1]}">${esc((EV[e[0]]||[e[0]])[0])}</span>${e[2]?`<b>Rule #${e[2]}</b> · `:""}${esc(e[1])}</div>`).join(""):'<p class="mute">Nothing promoted or dropped yet. Early on this is normal: the bar is high on purpose.</p>';
const rule=t=>`<div class="rule">${t.no?`<span class="tag champion">Rule #${t.no}</span>`:""}<span class="tag ${t.status.startsWith("dead")?"dead":t.status}">${esc(t.status)}</span><span class="tag">${esc(t.src)}${t.gen?" · gen "+t.gen:""}</span>${t.proven?'<span class="tag champion">proven live</span>':""}${t.certified?'<span class="tag champion">★ CERTIFIED</span>':""}
<div style="margin-top:6px;font-size:16px"><b>${esc(t.plain||t.text)}</b></div>${t.plain?`<details><summary>exact rule</summary><div class="mute">${esc(t.text)}</div></details>`:""}${t.why?`<div class="mute">${esc(t.why)}</div>`:""}
<div class="nums">discovery: ${st(t.disc)}<br>validation: ${st(t.val)}${t.val?.p_random!=null?` · random entries beat it ${Math.round(t.val.p_random*100)}% of the time`:""}${t.hold?`<br>final exam: ${st(t.hold)}`:""}${t.live?`<br><b>live paper since ${esc(t.promotedOn)}: ${st(t.live)}</b>`:""}</div>
${t.certChecks?`<details><summary>undeniable-edge checklist</summary><ul>${Object.entries(t.certChecks).map(([a,b])=>`<li>${b?"✔":"✘"} ${esc(a)}</li>`).join("")}</ul></details>`:""}
${t.evidence?`<div class="nums">evidence by year (trades · avg as traded): ${Object.entries(t.evidence).map(([y,v])=>`<span style="color:var(--${v[1]>=0?"good":"bad"})">${y}: ${v[0]} · ${v[1]>0?"+":""}${v[1]}%</span>`).join(" &nbsp; ")}${(()=>{const e=Object.entries(t.evidence),n=e.reduce((a,[,v])=>a+v[0],0),old=e.filter(([y])=>+y<2023).reduce((a,[,v])=>a+v[0],0);return n&&old/n<0.15?' <span class="tag near">young evidence: mostly 2023+</span>':""})()}</div>`:""}
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
// ── exchange-style positions (shared by every account) ─────────────
const fx=x=>{if(x==null||!isFinite(x))return"–";const a=Math.abs(x);return a>=1000?x.toLocaleString(undefined,{minimumFractionDigits:1,maximumFractionDigits:1}):a>=100?x.toFixed(2):a>=1?x.toFixed(4):(+x.toPrecision(4)).toString()};
const usd=x=>(x<0?"−":"")+"$"+Math.abs(x).toFixed(2);
const age=t=>{const h=(Date.now()/1000-t)/3600;return h<1?Math.max(1,Math.round(h*60))+"m":h<48?h.toFixed(1)+"h":(h/24).toFixed(1)+"d"};
const posOf=(p,lev)=>{const L=p.lev||lev,sd=p.side=="LONG"?1:-1,avg=p.avgEntry||p.entry,notional=p.notional||(p.margin*L);
 return {...p,L,sd,avg,notional,tp:p.target??p.tp,sl:p.stop??p.sl,mark:p.mark||p.livePx||avg,liq:L>1?avg*(1-sd*(1/L-0.005)):null}};
const pnlOf=(q,px)=>Math.max(-q.margin,q.notional*(q.sd*(px/q.avg-1))-q.notional*0.001);
const railPos=(q,px)=>{if(!q.tp||!q.sl)return null;const f=q.sd>0?(px-q.sl)/(q.tp-q.sl):(q.sl-px)/(q.sl-q.tp);return Math.max(0,Math.min(1,f))};
const posCard=(p,lev,acct)=>{const q=posOf(p,lev),pn=pnlOf(q,q.mark),roe=pn/q.margin*100,c=pn>=0?"good":"bad",m=railPos(q,q.mark),en=railPos(q,q.avg);
 const tpTxt=q.tp?fx(q.tp)+(q.half?" ✓ half taken":""):(q.trail?`trailing ${(q.trail*100).toFixed(1)}%`:"–");
 return `<div class="pos ${q.sd>0?"L":"S"}" data-coin="${esc(q.coin)}" data-sd="${q.sd}" data-avg="${q.avg}" data-notional="${q.notional}" data-margin="${q.margin}" data-acct="${acct}" data-tp="${q.tp||""}" data-sl="${q.sl||""}">
 <div class="ph"><span class="sym">${esc(q.coin)}USDT<small>Perp</small></span><span class="bdg ${q.sd>0?"L":"S"}">${q.sd>0?"Long":"Short"} ${q.L}x</span><span class="bdg">${q.L>1?"Isolated":"Spot-like"}</span>${q.grade?`<span class="bdg A" title="model probability of reaching +2% before the stop">Grade ${q.grade} · ${Math.round(q.prob*100)}%</span>`:""}${q.rule?`<span class="bdg">Rule #${esc(q.rule)}</span>`:""}${q.added?'<span class="bdg A">+ added</span>':""}
 <span class="pnlbox"><span class="v upnl" style="color:var(--${c})">${pn>=0?"+":""}${usd(pn)}</span><span class="r roe" style="color:var(--${c})">ROE ${roe>=0?"+":""}${roe.toFixed(2)}%</span></span></div>
 <div class="pg"><div><span>Size</span><b>${fx(q.notional/q.avg)} ${esc(q.coin)}</b><div class="mute" style="font-size:12px">${usd(q.notional)}</div></div>
 <div><span>Entry price</span><b>${fx(q.avg)}</b></div><div><span>Mark price</span><b class="mark">${fx(q.mark)}</b></div>
 <div class="liq"><span>Liq. price</span><b>${q.liq?fx(q.liq):"none"}</b></div><div><span>Margin</span><b>${usd(q.margin)}</b></div>
 <div><span>TP / SL</span><b style="color:var(--good)">${tpTxt}</b> <b style="color:var(--bad)">${q.sl?fx(q.sl):"–"}</b></div><div><span>Open for</span><b>${age(q.t)}</b><div class="mute" style="font-size:12px">${when(q.t)}</div></div></div>
 ${m!=null?`<div class="track"><div class="rail"><span class="ent" style="left:${(en*100).toFixed(1)}%" title="entry"></span><span class="mk" style="left:${(m*100).toFixed(1)}%"></span></div><div class="tl"><span>SL ${fx(q.sl)}</span><span>entry ${fx(q.avg)}</span><span>TP ${fx(q.tp)}</span></div></div>`:""}
 ${q.card?`<div class="ev">Why: ${q.card.for.length?"<b>"+q.card.for.map(esc).join(", ")+"</b> pointed this way":"no single family stood out"}${q.card.against.length?"; against: "+q.card.against.map(esc).join(", "):""} · the 50 most similar past moments reached +2% first ${q.card.similar}% of the time${q.card.rules&&q.card.rules.length?" · champion rules firing too: "+q.card.rules.map(esc).join(" "):""}${q.cont!=null?` · <b>exit model now: ${Math.round(q.cont*100)}% chance the move continues</b>`:""}</div>`:""}</div>`};
const exPanel=(A,lev,acct,extra)=>{const unr=A.open.reduce((x,p)=>{const q=posOf(p,lev);return x+pnlOf(q,q.mark)},0),mu=A.open.reduce((x,p)=>x+(p.margin||0),0),eq=A.cash+unr;
 const C=A.closed.slice().sort((a,b)=>(b.exitT||0)-(a.exitT||0)),pm=C.filter(r=>r.pm);
 const tabs=[["pos",`Positions (${A.open.length})`],["hist",`Trade history (${C.length})`],...(pm.length?[["pm",`Post-mortems (${pm.length})`]]:[]),["curve","Equity"]];
 return `<div class="xp" id="xp-${acct}"><div class="xbar"><div><span>Total equity</span><b class="acctval" data-acct="${acct}" data-cash="${A.cash}">${usd(eq)}</b></div><div><span>Unrealized PnL</span><b class="xunr" data-acct="${acct}" style="color:var(--${unr>=0?"good":"bad"})">${unr>=0?"+":""}${usd(unr)}</b></div><div><span>Margin in use</span><b>${usd(mu)}</b></div><div><span>Available</span><b>${usd(A.cash-mu)}</b></div><div><span>Realized (all time)</span><b style="color:var(--${A.cash>=A.start?"good":"bad"})">${A.cash>=A.start?"+":""}${usd(A.cash-A.start)}</b></div></div>
 <div class="xtabs">${tabs.map(([k,t],i)=>`<button data-x="${k}" class="${i?"":"on"}">${t}</button>`).join("")}</div>
 <div class="xbody" data-xp="pos">${posList(A,lev,acct)}</div>
 <div class="xbody" data-xp="hist" hidden><p class="mute" style="font-size:13px">${stats(A.closed)}</p>${histTable(C,lev)}</div>
 ${pm.length?`<div class="xbody" data-xp="pm" hidden>${pm.slice(0,20).map(r=>`<p style="font-size:13.5px"><b>${esc(r.coin)} ${r.side}</b> <span class="mute">${when(r.t)}</span>: ${esc(r.pm)}</p>`).join("")}</div>`:""}
 <div class="xbody" data-xp="curve" hidden>${curveSvg(A)}</div></div>`};
document.addEventListener("click",e=>{const b=e.target.closest(".xtabs button");if(!b)return;const P=b.closest(".xp");P.querySelectorAll(".xtabs button").forEach(x=>x.classList.toggle("on",x===b));P.querySelectorAll("[data-xp]").forEach(d=>d.hidden=d.dataset.xp!==b.dataset.x)});
const posList=(A,lev,acct)=>A.open.length?A.open.map(p=>posCard(p,lev,acct)).join(""):'<div class="card mute">No open positions.</div>';
const histTable=(C,lev)=>C.length?`<div class="scroll"><table class="hist"><tr><th>Symbol</th><th>Entry → Exit</th><th>Size</th><th>Realized PnL</th><th>ROE</th><th>How it ended</th><th>Opened</th><th>Closed</th></tr>${C.map(r=>{const L=r.lev||lev,sd=r.side=="LONG"?1:-1,n=r.notional||(r.margin*L),roe=r.roe??(r.usd/r.margin*100),c=r.usd>=0?"good":"bad";
 return `<tr><td><b>${esc(r.coin)}USDT</b> <span class="bdg ${sd>0?"L":"S"}">${sd>0?"Long":"Short"} ${L}x</span>${r.grade?` <span class="bdg A">${r.grade}</span>`:""}${r.rule?` <span class="mute">#${esc(r.rule)}</span>`:""}</td><td>${fx(r.avgEntry||r.entry)} → ${fx(r.exitPx??r.exit)}</td><td>${usd(n)}</td><td style="color:var(--${c});font-weight:650">${r.usd>=0?"+":""}${usd(r.usd)}</td><td style="color:var(--${c})">${roe>=0?"+":""}${(+roe).toFixed(1)}%</td><td>${esc(r.how)}</td><td class="mute">${when(r.t)}</td><td class="mute">${when(r.exitT)}</td></tr>`}).join("")}</table></div>`:'<p class="mute">None yet.</p>';
const curveSvg=A=>{const p=A.curve;if(!p||p.length<2)return'<p class="mute">The equity chart appears after the first closed trade.</p>';
 const W=900,H=180,xs=p.map(q=>q[0]),ys=p.map(q=>q[1]),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys,A.start)*0.98,y1=Math.max(...ys,A.start)*1.02;
 const X=x=>((x-x0)/(x1-x0||1))*W,Y=y=>H-((y-y0)/(y1-y0||1))*H;const d=p.map((q,i)=>(i?"L":"M")+X(q[0]).toFixed(1)+","+Y(q[1]).toFixed(1)).join("");
 return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:180px"><line x1="0" x2="${W}" y1="${Y(A.start)}" y2="${Y(A.start)}" stroke="var(--line)" stroke-dasharray="4"/><path d="${d}" fill="none" stroke="var(--${A.equity>=A.start?"good":"bad"})" stroke-width="2"/></svg>`};
const kpis=(A,box,extra)=>`<div class="grid">${[["Account value",`<span class="acctval" data-acct="${box}" data-cash="${A.cash}">${usd(A.equity??A.cash)}</span>`],["Since start",`<span class="acctret" data-acct="${box}" data-start="${A.start}" style="color:var(--${(A.equity??A.cash)>=A.start?"good":"bad"})">${sgn(100*((A.equity??A.cash)/A.start-1))}</span>`],["Closed trades",A.closed.length?`${A.closed.length} · ${A.closed.filter(r=>r.usd>0).length} won`:"none yet"],["Open now",String(A.open.length)],...(extra||[])].map(([a,b])=>`<div class="card"><div class="mute">${a}</div><div class="big" style="font-size:20px">${b}</div></div>`).join("")}</div>`;
const shadowBox=R=>{const S=R.shadow||{},O=R.override||{};const cr=R.callRate,cs=R.callRateBySide||{};
 const rate=cr?`<div class="grid" style="margin-bottom:8px">${[["Grade A calls / day (7 days)",cr.A],["Grade B calls / day (7 days)",cr.B],["A calls: long / short",`${cs.long?.A??0} / ${cs.short?.A??0}`]].map(([a,b])=>`<div class="card"><div class="mute">${a}</div><div class="big" style="font-size:20px">${b}</div></div>`).join("")}</div>`:"";const sides=["long","short"].filter(n=>S[n]||O[n]);
 if(!sides.length)return rate+'<p class="mute">Every grade-A/B call on both sides is scored 48 hours later against what the price really did. The first results appear about two days after the brain is trained. After 25 calls, this live record can switch a side on or off by itself.</p>';
 return rate+`<div class="scroll"><table><tr><th>side</th><th>calls scored</th><th>hit +2% first</th><th>predicted</th><th>break-even</th><th>avg result</th><th>waiting</th><th>live verdict</th></tr>${sides.map(n=>{const x=S[n]||{},o=O[n];
  return `<tr><td><b>${n}</b></td><td>${x.n??0}</td><td style="color:var(--${(x.hit??0)>=(x.be??1)?"good":"bad"});font-weight:650">${x.hit!=null?Math.round(x.hit*100)+"%":"–"}</td><td>${x.pred!=null?Math.round(x.pred*100)+"%":"–"}</td><td>${x.be!=null?Math.round(x.be*100)+"%":"–"}</td><td>${x.avg!=null?(x.avg>0?"+":"")+x.avg+"%":"–"}</td><td>${x.open??0}</td><td>${o?`<span class="tag ${o.live?"champion":"dead"}">${o.live?"switched ON":"switched OFF"}</span> <span class="mute" style="font-size:12px">${esc(o.why)}</span>`:'<span class="mute">needs 25 calls</span>'}</td></tr>`}).join("")}</table></div>
 ${(R.shadowRecent||[]).length?`<details style="margin-top:6px"><summary class="mute">Latest scored calls</summary><table>${R.shadowRecent.map(x=>`<tr><td>${when(x.t)}</td><td><b>${esc(x.coin)}</b> ${x.side}</td><td>grade ${x.grade} · ${Math.round(x.prob*100)}%</td><td class="mute">${x.px?`${fx(x.px)} · SL ${fx(x.px*(1-(x.side=="long"?1:-1)*x.stop))} · TP ${fx(x.px*(1+(x.side=="long"?1:-1)*0.02))}`:""}</td><td style="color:var(--${x.y?"good":"bad"})">${x.y?"reached +2% first":"stop / no target"}</td><td>${x.ret>0?"+":""}${(x.ret*100).toFixed(2)}%</td></tr>`).join("")}</table></details>`:""}`};
const RG={"up-calm":"rising, calm","up-wild":"rising, wild","down-calm":"falling, calm","down-wild":"falling, wild"};
const adaptBox=R=>{const A=R.adapt||{},O=R.regimeOverride||{};const now=R.regimeNow;
 const head=`<p>Market regime now (from Bitcoin): <b>${esc(RG[now]||now||"unknown")}</b>. The referee re-shapes itself every hour from its live record: it raises or relaxes its bar when its odds prove too optimistic or too cautious, sizes up or down with its live edge (0.5x to 1.25x, never above plan), and switches each side on or off <i>separately for each market regime</i> once it has 15 live calls there. Margin per trade is capped at 20% of the account, easing to 10% as the account grows from 2x to 4x its start.</p>`;
 const rows=["long","short"].map(n=>{const a=A[n];const regs=Object.entries(O[n]||{}).map(([g,v])=>`<span class="fam ${v.live?"y":"n"}" title="${v.n} calls, hit ${Math.round(v.hit*100)}% vs ${Math.round(v.be*100)}%, avg ${v.avg}%">${esc(RG[g]||g)}: ${v.live?"on":"off"}</span>`).join("");
  return `<tr><td><b>${n}</b></td><td>${a?esc(a.why):'<span class="mute">needs 20 scored calls</span>'}</td><td>${regs||'<span class="mute">needs 15 calls per regime</span>'}</td></tr>`}).join("");
 return head+`<div class="scroll"><table><tr><th>side</th><th>self-calibration and size</th><th>on/off by regime</th></tr>${rows}</table></div>`};
const monthly=list=>{const acc={};list.forEach(([name,A])=>{(A?.closed||[]).forEach(r=>{if(!r.exitT)return;const m=new Date(r.exitT*1000).toISOString().slice(0,7);const k=m+"|"+name;
  const o=acc[k]||(acc[k]={m,name,n:0,w:0,usd:0,first:null});o.n++;if(r.usd>0)o.w++;o.usd+=r.usd;if(o.first==null||r.exitT<o.first.exitT)o.first=r})});
 const rows=Object.values(acc).sort((a,b)=>b.m.localeCompare(a.m)||a.name.localeCompare(b.name));if(!rows.length)return'<p class="mute">The first month appears after the first closed trade.</p>';
 return `<div class="scroll"><table><tr><th>month</th><th>account</th><th>trades</th><th>won</th><th>result</th><th>month return</th></tr>${rows.map(o=>{const startEq=(o.first.after??0)-(o.first.usd??0),ret=startEq>0?o.usd/startEq*100:null;
  return `<tr><td>${o.m}</td><td>${esc(o.name)}</td><td>${o.n}</td><td>${o.w} (${Math.round(o.w/o.n*100)}%)</td><td style="color:var(--${o.usd>=0?"good":"bad"})">${o.usd>=0?"+":""}${usd(o.usd)}</td><td style="color:var(--${(ret??0)>=0?"good":"bad"});font-weight:650">${ret!=null?(ret>=0?"+":"")+ret.toFixed(1)+"%":"–"}</td></tr>`}).join("")}</table></div><p class="mute" style="font-size:12.5px">Goal for comparison: about +20% a month.</p>`};
// ── the referee ────────────────────────────────────────────────────
(()=>{const R=D.referee||{},A=R.acct,M=R.model;if(!A){$("rbox").innerHTML='<p class="mute">The referee starts on the next run.</p>';return}
 const ex=(n,s)=>s?`<div class="card"><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><b style="font-size:16px">${n}</b>${s.live===false||s.off?'<span class="tag dead">watching only</span>':'<span class="tag champion">trading</span>'}<span style="margin-left:auto;font-weight:700;color:var(--${(s.exam?.monthly??0)>=0?"good":"bad"})">${s.exam?.monthly!=null?(s.exam.monthly>0?"+":"")+s.exam.monthly+"% / month":"–"}</span></div>
 ${s.live===false||s.off?`<div class="mute" style="font-size:12.5px;margin-top:4px">${esc(s.why||"no edge on the validation year")}</div>`:""}
 <div class="kv"><span>Exam trades</span><b>${s.exam?.trades??0}</b><span>Win rate</span><b>${s.exam?.win??"–"}%</b><span>Avg move per trade</span><b>${s.exam?.avg!=null?(s.exam.avg>0?"+":"")+s.exam.avg+"%":"–"}</b><span>Trades per month</span><b>${s.exam?.perMonth??"–"}</b><span>Normal hit rate</span><b>${Math.round((s.base||0)*100)}%</b><span>Safety margin</span><b>+${Math.round((s.margin||0)*100)} pts</b></div></div>`:"";
 const W=M?Object.entries(M.long?.weights||{}).map(([f,w])=>`${esc(f)} ${w>0?"+":""}${w}`).join(" · "):"";
 const look=R.look,lk=look&&look.rows?.length?`<div class="cands">${look.rows.map(x=>{const why=x.off?"side watching only":x.unfamiliar?"unfamiliar situation":x.grade?"Grade "+x.grade:x.prob<x.need?"odds too low":"not enough agreement";
  const sd=x.side=="LONG"?1:-1,lv=x.px&&x.stopPct?`entry ~${fx(x.px)} · SL ${fx(x.px*(1-sd*x.stopPct))} (−${(x.stopPct*100).toFixed(1)}%) · TP ${fx(x.px*(1+sd*0.02))} (+2%)`:"";
  return `<div class="cand ${x.grade&&!x.off?"go":""}"><div class="ch"><b>${esc(x.coin)}</b><span class="bdg ${sd>0?"L":"S"}">${sd>0?"Long":"Short"}</span>${x.grade&&!x.off?`<span class="tag champion">${why}</span>`:`<span class="mute" style="font-size:12.5px">${why}</span>`}<span class="odds">${Math.round(x.prob*100)}% <small>needs ${Math.round(x.need*100)}%</small></span></div>
  <div class="meter" style="margin:7px 0 2px"><i style="width:${Math.min(100,x.prob*100)}%"></i><u style="left:${Math.min(100,x.need*100)}%"></u></div>${lv?`<div class="lv">${lv}</div>`:""}
  <div>${x.for.map(f=>`<span class="fam y">${esc(f)}</span>`).join("")}${x.against.map(f=>`<span class="fam n">${esc(f)}</span>`).join("")}</div></div>`}).join("")}</div>`:'<p class="mute">Appears after the next run with a trained model.</p>';
 $("rbox").innerHTML=kpis(A,"rbox",[["Brain",M?`<span style="font-size:14px">trained ${esc(M.trainedAt)}</span>`:'<span style="font-size:14px" class="mute">not trained yet</span>']])+
 (A.paused?`<div class="card" style="border-color:var(--bad);margin-top:10px;color:var(--bad)"><b>Kill switch:</b> ${esc(A.paused)}</div>`:"")+exPanel(A,10,"rbox")+
 `<div class="card" style="margin-top:10px"><b>Its honest final exam</b> <span class="mute" style="font-size:13px">(the newest 4 months, which it never saw while learning; one trade a day, sized by risk, simple exits)</span>
  ${M?`<div class="exam">${ex("Long",M.long)}${ex("Short",M.short)}</div><div class="mute" style="font-size:12.5px;margin-top:6px">How much it trusts each kind of evidence (long side): ${W}. Retrained weekly on the newest data. ${esc(R.trainNote||"")}</div>`:`<p class="mute">${esc(R.trainNote||"The first training happens on the next run with enough time (about 10-20 minutes).")}</p>`}</div>
 <h3>What it sees right now ${look?`<span class="mute" style="font-weight:400;font-size:13px">(hour of ${when(look.t)}, ${look.scanned} coins scanned)</span>`:""}</h3>${lk}
 <h3>Live scorecard: every A/B call, traded or not</h3>${shadowBox(R)}
 <h3>How it is adapting right now</h3>${adaptBox(R)}
`})();
// ── the $500 account ─────────────────────────────────────────────
const renderAcct=(A,box,isTeam)=>{if(!A){$(box).innerHTML='<p class="mute">The account starts on the next run.</p>';return}
 const ret=100*(A.equity/A.start-1);const C=A.closed;
 $(box).innerHTML=kpis(A,box)+`
 <div class="card" style="margin-top:10px">${isTeam?`<div class="mute">Team now: ${A.teamRules.length?A.teamRules.map(r=>`<div style="margin-top:4px"><b>Rule #${r.no}</b>: ${esc(r.text)}</div>`).join(""):"no team yet"}</div>
 <div class="mute" style="margin-top:6px;font-size:13px">Started ${esc(A.born)} UTC with $${A.start}. Each signal uses 5% of the account as margin at ${D.lev}x; when 2-3 team rules agree on the same trade, the position grows (up to 15%). Max 6 trades at once, profits compound. The team is re-picked every run.</div>`:
 `<div class="mute">Trading now: ${A.leaderNo?`<b>Rule #${A.leaderNo}</b>`:"no rule yet (waiting for a champion with 30+ trades after its exams)"}</div>${A.leaderText?`<div style="margin-top:4px">${esc(A.leaderText)}</div>`:""}
 <div class="mute" style="margin-top:6px;font-size:13px">Started ${esc(A.born)} UTC with $${A.start}. ${A.lev==1?"The whole account goes into one trade at a time, no leverage, profits compound.":`Each trade uses 10% of the account as margin at ${A.lev||D.lev}x leverage, max 3 trades at once, profits compound.`} The best rule is re-picked every run from the newest results, and only used for hours that come after it was picked.</div>`}</div>
 ${exPanel(A,A.lev||D.lev,box)}
 <details class="card" style="margin-top:10px"><summary>Rule changes: ${A.leaders.length}</summary><ul>${A.leaders.map(l=>`<li>${esc(l.at)} → <b>${isTeam?"":"Rule #"}${esc(l.rule)}</b>: ${esc(l.why)}<br><span class=mute>${esc(l.text)}</span></li>`).join("")||"<li class=mute>none</li>"}</ul></details>`};
renderAcct(D.account,"box",false);renderAcct(D.team,"tbox",true);renderAcct(D.allin,"abox",false);
// live prices for open positions, straight from Binance in YOUR browser, every 5 seconds (display only; the books update hourly)
(()=>{const cards=[...document.querySelectorAll(".pos")];if(!cards.length)return;
 const note=document.createElement("div");note.className="mute";note.style.fontSize="12px";note.textContent="Live prices: connecting to Binance…";
 const anchor=$("rbox")||$("box");anchor.parentNode.insertBefore(note,anchor);
 async function prices(){for(const url of["https://fapi.binance.com/fapi/v1/ticker/price","https://data-api.binance.vision/api/v3/ticker/price"]){
   try{const r=await fetch(url,{cache:"no-store"});if(!r.ok)continue;const j=await r.json();const m={};j.forEach(x=>m[x.symbol]=+x.price);return [m,url.includes("fapi")?"Binance futures":"Binance spot"]}catch(e){}}return [null,null]}
 const pick=(m,c)=>m[c+"USDT"]||(c.startsWith("1000")&&m[c.slice(4)+"USDT"]?m[c.slice(4)+"USDT"]*1000:null);
 async function tick(){const [m,src]=await prices();if(!m){note.textContent="Live prices unavailable right now (values below are from the last hourly run).";return}
  const tot={};cards.forEach(el=>{const px=pick(m,el.dataset.coin);if(!px)return;const q={sd:+el.dataset.sd,avg:+el.dataset.avg,notional:+el.dataset.notional,margin:+el.dataset.margin,tp:+el.dataset.tp||null,sl:+el.dataset.sl||null};
   const pn=pnlOf(q,px),roe=pn/q.margin*100,c=`var(--${pn>=0?"good":"bad"})`;tot[el.dataset.acct]=(tot[el.dataset.acct]||0)+pn;
   const u=el.querySelector(".upnl"),r=el.querySelector(".roe"),mk=el.querySelector(".mark"),dot=el.querySelector(".mk");
   u.textContent=(pn>=0?"+":"")+usd(pn);u.style.color=c;r.textContent=`ROE ${roe>=0?"+":""}${roe.toFixed(2)}%`;r.style.color=c;mk.textContent=fx(px);
   const f=railPos(q,px);if(dot&&f!=null)dot.style.left=(f*100).toFixed(1)+"%"});
  document.querySelectorAll(".xunr").forEach(v=>{const u=tot[v.dataset.acct];if(u==null)return;v.textContent=(u>=0?"+":"")+usd(u);v.style.color=`var(--${u>=0?"good":"bad"})`});
  const eqs={};document.querySelectorAll(".acctval").forEach(v=>{if(tot[v.dataset.acct]!=null){eqs[v.dataset.acct]=+v.dataset.cash+tot[v.dataset.acct];v.textContent=usd(eqs[v.dataset.acct])}});
  document.querySelectorAll(".acctret").forEach(v=>{const e=eqs[v.dataset.acct];if(e==null)return;const r=100*(e/+v.dataset.start-1);v.textContent=sgn(r);v.style.color=`var(--${r>=0?"good":"bad"})`});
  note.textContent=`Live prices from ${src}, updated ${new Date().toLocaleTimeString()} (every 5 s, in your browser). Trades are booked by the hourly run.`}
 tick();setInterval(tick,5000)})();
// health warnings (only shown when something is wrong)
(()=>{const h=(Date.now()-builtMs)/36e5;const de=D.runs[0]?.dataEnd;const dd=de?(Date.now()-Date.parse(de.replace(" ","T")+"Z"))/36e5:0;
 $("health").innerHTML=h>4?`<div class="card" style="border-color:var(--bad);color:var(--bad);margin-top:12px"><b>⚠ The machine has not run for ${Math.round(h)} hours</b> (it should run every hour). Something may be stuck: tell Claude.</div>`:
 dd>72?`<div class="card" style="border-color:var(--warn);color:var(--warn);margin-top:12px"><b>⚠ Market data is ${Math.round(dd/24)} days old</b>: the data source may have changed. Tell Claude.</div>`:
 (D.runs[0]?.problems||[]).length?`<div class="card" style="border-color:var(--warn);margin-top:12px"><b style="color:var(--warn)">⚠ The last run finished, but ${D.runs[0].problems.length} part(s) had a problem</b> (the rest worked). If this stays for a day, tell Claude.<ul class="mute" style="font-size:13px">${D.runs[0].problems.map(p=>`<li>${esc(p)}</li>`).join("")}</ul></div>`:""})();
try{$("monthly").innerHTML=monthly([["Referee",D.referee?.acct],["Best rule",D.account],["Team",D.team],["All-in",D.allin]])}catch(e){}
// how the referee decides (structured explainer, live numbers where available)
(()=>{const M=D.referee?.model||{},L=M.long||{},S=M.short||{},pts=x=>x!=null?"+"+Math.round(x*100)+" pts":"learned";
 const step=(t,d)=>`<div class="card"><b>${t}</b><p>${d}</p></div>`;
 const gl=(t,d)=>`<div class="card"><b>${t}</b><p>${d}</p></div>`;
 $("howref").innerHTML=`<p class="mute">Every hour it looks at the ~40 most liquid coins, both directions, and asks one question: <b>"will this reach +2% before its stop?"</b></p>
 <h3 class="howh">The decision, step by step</h3>
 <div class="flow">${[
  step("Shape the trade","Target is always +2%. The stop sits outside the coin's normal noise: 1.25× its usual daily move, kept between 1.5% and 4%."),
  step("Work out break-even","The win rate needed to not lose money with that shape: (stop + fees) ÷ (2% + stop). A 2% stop needs ~52%; a 3% stop ~62%."),
  step("Ask each kind of evidence","Positioning, flow, trend, volatility, leverage, real money, mood, invented measurements and similar past moments each give their own odds."),
  step("Combine them","A small combiner, trained on a separate year, decides how much to trust each kind. Result: the <b>odds</b> you see on the Accounts tab."),
  step("Raise the bar","<b>Needed = break-even + safety margin.</b> The margin is learned, not hand-set (now: long "+pts(L.margin)+", short "+pts(S.margin)+"), and moves with live results."),
  step("Grade and pick one","A, B or skip (below). It takes the first qualifying trade of the UTC day: at most one trade a day.")].join("")}</div>
 <h3 class="howh">What the grades mean</h3>
 <div class="grades">
  <div class="card" style="--gc:var(--good)"><h4>Grade A: full size</h4><ul><li>Odds ≥ needed</li><li>At least 2 kinds of evidence <b>for</b></li><li>At most 1 kind <b>against</b></li><li>Risk: a full loss costs ~4% of the account</li></ul></div>
  <div class="card" style="--gc:var(--acc)"><h4>Grade B: half size</h4><ul><li>Odds ≥ break-even + half the margin</li><li>At least 2 for, at most 2 against</li><li>Only after 18:00 UTC, if no A came that day</li><li>Risk: ~2% of the account</li></ul></div>
  <div class="card" style="--gc:var(--mute)"><h4>Skip</h4><ul><li>Odds too low, or not enough agreement</li><li>The side is "watching only"</li><li>Situation unlike anything in 6 years</li><li>Already traded today</li></ul></div></div>
 <p class="mute" style="font-size:13px">A kind of evidence counts <b>for</b> when its own odds are at least 3 points above normal, and <b>against</b> when at least 3 points below.</p>
 <h3 class="howh">Once a trade is open</h3>
 <div class="flow">${[
  step("Hard stop","Always set at entry. Isolated 10x: liquidation is ~9.5% away, far beyond the stop."),
  step("Half at +2%","Half the position is closed at the target and the stop moves to break-even: the rest is risk-free."),
  step("Let the rest run","An exit model asks every hour: '+1% more before −1%?' It holds and trails while yes, and closes when the move fades."),
  step("One add-on","If the trade is up 1% and the evidence strongly says it continues, it adds once and moves the stop to the entry.")].join("")}</div>
 <h3 class="howh">How it keeps itself honest</h3>
 <div class="gl">${[
  gl("Three exams","Learns on the older years, tunes on the next year, then is graded once on the newest 4 months it never saw. A side that fails becomes <i>watching only</i>."),
  gl("Live scorecard","Every A/B call, traded or not, is scored 48 hours later against what the price really did."),
  gl("Self-calibration","If its calls hit less often than predicted, it raises its own bar; if more often, it relaxes a little. Size moves 0.5×–1.25× with the live edge."),
  gl("Regime switch","Market regime from Bitcoin: rising or falling × calm or wild. Each side is switched on or off separately per regime once it has 15 live calls there."),
  gl("Kill switch","After 20 trades, if live hits fall far below what it predicted, it pauses itself until retrained."),
  gl("Weekly retraining","Retrains every week on the newest data, and whenever a new kind of evidence becomes available.")].join("")}</div>
 <details class="card" style="margin-top:10px"><summary>The kinds of evidence, in plain words</summary><div class="gl" style="margin-top:8px">${[
  gl("Positioning","How the crowd and the top traders are betting (long/short ratios, open interest)."),
  gl("Flow","Who is aggressively buying or selling right now (taker volume)."),
  gl("Trend","Where the price sits versus its recent ranges and averages."),
  gl("Volatility","How wild the coin is moving versus normal."),
  gl("Leverage","Futures vs spot price gap, and Hyperliquid's on-chain premium and funding: is leverage paying up?"),
  gl("Real money","Spot market's share of trading and spot buyers vs sellers: is the move backed by real buyers?"),
  gl("Mood","Bitcoin's move, Fear & Greed, Coinbase premium, options fear gauge, stablecoin inflows, time of day."),
  gl("Invented","Measurements the AI designed itself, kept only while they prove useful."),
  gl("Similar moments","The 50 most similar situations in 6 years: how often did they reach +2% first?")].join("")}</div></details>`})();
try{$("roadmap").innerHTML=D.roadmap||'<p class="mute">Not available.</p>'}catch(e){}
// self-check panel + downloadable health report
(()=>{const H=D.healthSummary;if(!H)return;const col={OK:"good",WARN:"warn",FAIL:"bad"},n=H.checks.filter(c=>c[1]=="OK").length;
 $("hcheck").innerHTML=`<details class="card" style="margin-top:10px" ${H.overall!="OK"?"open":""}><summary><b style="color:var(--${col[H.overall]})">● Self-check: ${H.overall}</b> <span class="mute">${n}/${H.checks.length} checks OK at ${esc(H.at)} UTC</span>
  <a href="health.json" download="health-${esc(H.at.replace(/[ :]/g,"-"))}.json" style="float:right" onclick="event.stopPropagation()">⬇ Download health report</a></summary>
  <table style="margin-top:8px">${H.checks.map(c=>`<tr><td style="color:var(--${col[c[1]]});font-weight:650;width:60px">${c[1]}</td><td><b>${esc(c[0])}</b><div class="mute" style="font-size:12.5px">${esc(c[2])}</div></td></tr>`).join("")}</table>
  <p class="mute" style="font-size:12.5px">The health report holds the last ~4 days of runs (timing, memory, data freshness per coin, relay and AI answers, problems, accounts). After a night away, download it and send it to Claude to check.</p></details>`})();
// analyst
(()=>{const A=D.analyst;if(!A){$("analyst").innerHTML='<p class="mute">The analyst starts once there are at least 2 winning rules.</p>';return}
 $("analyst").innerHTML=`<div class="card"><div class="mute">Written ${esc(A.at)} UTC by ${esc(A.model)}</div>
 ${A.why?.length?"<h3 style='font-size:15px;margin:10px 0 4px'>Why the winners might win</h3><ul>"+A.why.map(w=>`<li><b>${esc(w.rule)}</b>: ${esc(w.reason)}</li>`).join("")+"</ul>":""}
 ${A.merges?.length?"<h3 style='font-size:15px;margin:10px 0 4px'>Merges it proposed (each is tested like any other idea)</h3><ul>"+A.merges.map(m=>`<li>${esc(m.why)}${m.valid?"":" <span class=mute>(could not be built, skipped)</span>"}</li>`).join("")+"</ul>":"<p class=mute>No merge made sense this time.</p>"}
 ${A.wild?.length?"<h3 style='font-size:15px;margin:10px 0 4px'>Wild ideas (gambling concepts, sentiment extremes)</h3><ul>"+A.wild.map(m=>`<li>${esc(m.why)}${m.valid?"":" <span class=mute>(could not be built, skipped)</span>"}</li>`).join("")+"</ul>":""}</div>`})();
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

def _signals(mem, J, hours=3):
    """entries the best rule and the team rules fired in the newest hours, with the exact levels to use"""
    from .judge import scale_exit
    newest = int(J.t.max()); keys = []
    for k in [(mem.get("account") or {}).get("leader")] + list((mem.get("team") or {}).get("team") or []):
        if k and k in mem["tests"] and k not in keys and mem["tests"][k]["status"] == "champion": keys.append(k)
    out, seen_ = [], set()
    for k in keys:
        t = mem["tests"][k]
        for i, sd, ex in J.raw_entries(t["h"], t_lo=newest - hours * 3600):
            key = (str(J.coin[i]), int(J.t[i]), sd)
            if key in seen_: continue
            seen_.add(key); e = float(J.O[i])
            d = scale_exit(ex, J.dvol[i]) if isinstance(ex, dict) else ({"hold": C.EXITS[ex][1]} if C.EXITS[ex][0] == "ret" else {"tp": C.EXITS[ex][0], "sl": C.EXITS[ex][1], "hold": C.EXITS[ex][2]})
            if d is None: continue
            out.append({"rule": t.get("no"), "coin": key[0], "side": "LONG" if sd > 0 else "SHORT", "t": key[1], "entry": e,
                        "tp": e * (1 + sd * d["tp"]) if d.get("tp") else None, "sl": e * (1 - sd * d["sl"]) if d.get("sl") else None,
                        "trail": d.get("trail"), "hold": d["hold"], "plain": (t.get("plain") or t["text"])[:200]})
    out.sort(key=lambda x: -x["t"]); newest, sides = {}, {}
    for x in out: sides.setdefault((x["rule"], x["coin"]), set()).add(x["side"])
    for x in out:
        k = (x["rule"], x["coin"])
        if k not in newest: x["flips"] = len(sides[k]) > 1; newest[k] = x
    return list(newest.values())[:20]

def _roadmap():
    """ROADMAP.md -> small safe HTML (headings, check-list items, bold)"""
    import html as _h, re as _re
    try: lines = open("ROADMAP.md", encoding="utf-8").read().splitlines()
    except Exception: return ""
    out, inlist = [], False
    b = lambda t: _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _h.escape(t))
    for ln in lines:
        if ln.startswith("- "):
            if not inlist: out.append("<ul>"); inlist = True
            t = ln[2:]; done = t.startswith("[x]"); t = t[3:].strip() if t[:3] in ("[x]", "[ ]") else t
            out.append(f'<li class="{"done" if done else ""}">{b(t)}</li>'); continue
        if inlist: out.append("</ul>"); inlist = False
        if ln.startswith("## "): out.append(f"<h3>{b(ln[3:])}</h3>")
        elif ln.startswith("# ") or not ln.strip(): continue
        else: out.append(f"<p>{b(ln)}</p>")
    if inlist: out.append("</ul>")
    return "".join(out)

def write(mem, J=None, log=print):
    os.makedirs(C.DOCS_DIR, exist_ok=True)
    d = _summary(mem); d["built"] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    d["trades"] = _trades(mem, J, log) if J is not None else []; d["margin"] = C.PAPER_MARGIN; d["lev"] = C.LEVERAGE
    for key in ("account", "team", "allin"):
        A = mem.get(key)
        if not A: continue
        lead = mem["tests"].get(A.get("leader") or "", {})
        tm = [mem["tests"][k] for k in (A.get("team") or []) if k in mem["tests"]]
        d[key] = {"teamRules": [{"no": t.get("no"), "text": t.get("plain") or t["text"]} for t in tm],"born": A["born"], "t0": A["t0"], "cash": A["cash"], "equity": A.get("equity", A["cash"]), "start": A.get("start", 500.0), "lev": A.get("lev", 10),
                        "open": [{k: v for k, v in p.items() if k not in ("sd", "_w")} for p in A["open"]], "closed": A["closed"][::-1][:300],
                        "curve": A["curve"], "leaders": A["leaders"][::-1], "leaderNo": lead.get("no"), "leaderText": lead.get("plain") or lead.get("text")}
    R = mem.get("referee") or {}
    if R.get("acct"):
        A = R["acct"]
        d["referee"] = {"acct": {**{k: A.get(k) for k in ("born", "start", "cash", "equity", "paused", "curve")}, "open": [{k: v for k, v in p.items() if k not in ("units", "fams", "row")} for p in A["open"]],
                                 "closed": A["closed"][-300:], "decisions": A.get("decisions", [])[-30:]}, "model": R.get("model"), "look": R.get("look"), "trainNote": R.get("trainNote"),
                         "shadow": R.get("shadowStats"), "override": R.get("override"), "adapt": R.get("adapt"), "regimeOverride": R.get("regimeOverride"), "regimeNow": R.get("regimeNow"), "callRate": R.get("callRate"), "callRateBySide": R.get("callRateBySide"), "shadowRecent": [x for x in (R.get("shadow") or []) if x.get("y") is not None][-15:][::-1]}
    d["healthSummary"] = mem.get("healthSummary")
    d["roadmap"] = _roadmap()
    d["analyst"] = mem.get("analyst")
    d["live"] = mem.get("live"); d["newest"] = int(J.t.max()) if J is not None else None
    try: d["signals"] = _signals(mem, J) if J is not None else []
    except Exception as e: log(f"signals failed: {e}"); d["signals"] = []
    d["blocks"] = [{k: b.get(k) for k in ("id", "name", "expr", "why", "tests", "best", "at", "by")} for b in mem.get("blocks", [])]
    d["blocksRetired"] = (mem.get("blocks_retired") or [])[::-1][:40]
    d["twins"] = [{"text": t.get("plain") or t["text"], "of": t.get("twinOf")} for t in mem["tests"].values() if t["status"] == "twin"][:40]
    blob = json.dumps(d, separators=(",", ":")).replace("</", "<\\/")
    with open(f"{C.DOCS_DIR}/index.html", "w", encoding="utf-8") as f: f.write(PAGE.replace("__DATA__", blob))
    with open(f"{C.DOCS_DIR}/summary.json", "w", encoding="utf-8") as f: json.dump(d, f)
    log(f"report: {C.DOCS_DIR}/index.html")

def write_minimal(mem, problems):
    """fallback page if the full dashboard ever fails to build: the machine is still visible"""
    os.makedirs(C.DOCS_DIR, exist_ok=True); r = (mem.get("runs") or [{}])[0]
    body = "".join(f"<li>{html.escape(p)}</li>" for p in problems)
    with open(f"{C.DOCS_DIR}/index.html", "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Research Machine</title><body style='font-family:system-ui;max-width:800px;margin:30px auto;padding:0 16px'>"
                f"<h1>Research Machine</h1><p>The machine ran at {html.escape(str(r.get('at')))} UTC and tested {r.get('tested')} ideas, but the full dashboard could not be built:</p><ul>{body}</ul><p>Tell Claude.</p>")

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
