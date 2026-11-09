// The live relay (+ on our own server, a minute-by-minute recorder). No dependencies.
// GET /api/live?coins=BTC,ETH&pages=1&kl=72  ->  hourly live data for the research machine
// GET /where -> can Binance be reached from here      GET /rec/status, /rec/get?day=YYYY-MM-DD&kind=depth|funding|liq  (key needed)
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");
const zlib = require("zlib");
const readline = require("readline");
const handler = require("./live.js");
const KEY = process.env.RELAY_KEY || "";
const okKey = (req, u) => !KEY || req.headers["x-relay-key"] === KEY || u.searchParams.get("key") === KEY;

// ── RECORDER: only on our own server (it has a key); Render/Vercel never run it ─────────────────────────
// Binance gives no free history for liquidations or order books, so we record them ourselves from today on.
const REC = { on: !!KEY, dir: process.env.REC_DIR || path.join(__dirname, "data"), started: Date.now(), writes: { depth: 0, funding: 0, liq: 0 },
              last: {}, errors: 0, lastError: null, ws: "off", coolUntil: 0 };
const COINS = "BTC ETH SOL XRP BNB ADA DOGE AVAX LINK LTC DOT BCH TRX UNI ATOM NEAR FIL ETC AAVE ARB OP SUI APT INJ TIA SEI WLD FET HBAR XLM ALGO ICP VET CRV LDO IMX STX RUNE ZEC 1000PEPE 1000SHIB ORDI WIF JUP ONDO PENDLE KAS ENA TON TAO HYPE".split(" ");
const FAPI = "https://fapi.binance.com";
const day = (t) => new Date(t).toISOString().slice(0, 10);
function write(kind, rows) {
  if (!rows.length) return;
  try {
    const d = path.join(REC.dir, day(Date.now())); fs.mkdirSync(d, { recursive: true });
    fs.appendFileSync(path.join(d, kind + ".jsonl"), rows.map((r) => JSON.stringify(r)).join("\n") + "\n");
    REC.writes[kind] += rows.length; REC.last[kind] = Date.now();
  } catch (e) { REC.errors++; REC.lastError = String(e).slice(0, 200); }
}
async function getJSON(p) {
  if (Date.now() < REC.coolUntil) throw new Error("cooling down");
  const ctl = new AbortController(); const tm = setTimeout(() => ctl.abort(), 15000);
  try {
    const r = await fetch(FAPI + p, { signal: ctl.signal });
    if (r.status === 418 || r.status === 429) { REC.coolUntil = Date.now() + 600000; throw new Error("rate limit " + r.status); }
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } finally { clearTimeout(tm); }
}
// order-book depth around the mid price, every minute (weight 2 per coin: ~100 of Binance's 2400 per minute)
async function depthTick() {
  const t = Math.floor(Date.now() / 60000) * 60; const rows = [];
  for (const c of COINS) {
    try {
      const b = await getJSON(`/fapi/v1/depth?symbol=${c}USDT&limit=50`);
      const bids = b.bids.map(([p, q]) => [+p, +q]), asks = b.asks.map(([p, q]) => [+p, +q]);
      if (!bids.length || !asks.length) continue;
      const mid = (bids[0][0] + asks[0][0]) / 2, within = (side, pct) => side.reduce((s, [p, q]) => s + (Math.abs(p / mid - 1) <= pct ? p * q : 0), 0);
      rows.push({ t, c, mid: +mid.toPrecision(8), spr: +((asks[0][0] - bids[0][0]) / mid * 1e4).toFixed(2),
                  b02: Math.round(within(bids, 0.002)), a02: Math.round(within(asks, 0.002)), b1: Math.round(within(bids, 0.01)), a1: Math.round(within(asks, 0.01)) });
    } catch (e) { REC.errors++; REC.lastError = `depth ${c}: ${String(e).slice(0, 120)}`; if (String(e).includes("rate limit") || String(e).includes("cooling")) break; }
    await new Promise((s) => setTimeout(s, 250));                    // spread the requests over ~15 s
  }
  write("depth", rows);
}
// funding rate + mark/index price for every coin, every 5 minutes (one request)
async function fundingTick() {
  try {
    const all = await getJSON("/fapi/v1/premiumIndex"); const t = Math.floor(Date.now() / 60000) * 60; const want = new Set(COINS.map((c) => c + "USDT"));
    write("funding", all.filter((x) => want.has(x.symbol)).map((x) => ({ t, c: x.symbol.slice(0, -4), fr: +x.lastFundingRate, mark: +x.markPrice, index: +x.indexPrice, next: Math.floor(x.nextFundingTime / 1000) })));
  } catch (e) { REC.errors++; REC.lastError = "funding: " + String(e).slice(0, 120); }
}
// forced liquidations (live stream only on Binance): summed per minute and coin, long vs short
const liqBuf = new Map();
// Binance split its futures streams in 2026: liquidations now live under /market (the old /ws address connects but stays silent)
const LIQ_URLS = ["wss://fstream.binance.com/market/ws/!forceOrder@arr", "wss://fstream.binance.com/ws/!forceOrder@arr"];
let liqUrl = 0, liqWs = null;
REC.liqMsgs = 0; REC.liqLastMsg = null; REC.liqUrl = null;
function startLiq() {
  if (typeof WebSocket === "undefined") { REC.ws = "unavailable (Node " + process.version + ")"; return; }
  let ws; const url = LIQ_URLS[liqUrl % LIQ_URLS.length];
  try { ws = new WebSocket(url); liqWs = ws; REC.ws = "connecting"; REC.liqUrl = url; } catch (e) { REC.ws = "error " + String(e).slice(0, 80); setTimeout(startLiq, 60000); return; }
  ws.onopen = () => { REC.ws = "connected"; REC.liqOpened = Date.now(); };
  ws.onmessage = (m) => {
    try {
      REC.liqMsgs++; REC.liqLastMsg = Date.now();
      const o = JSON.parse(typeof m.data === "string" ? m.data : String(m.data)).o; if (!o || !o.s.endsWith("USDT")) return;
      const c = o.s.slice(0, -4), v = +o.ap * +o.z || +o.p * +o.q;
      const e = liqBuf.get(c) || { long: 0, short: 0, n: 0 };
      if (o.S === "SELL") e.long += v; else e.short += v;              // a SELL liquidation closes a long position
      e.n++; liqBuf.set(c, e);
    } catch (e) {}
  };
  ws.onclose = () => { if (liqWs === ws) { REC.ws = "reconnecting"; setTimeout(startLiq, 5000); } };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}
// watchdog: a market-wide liquidation stream is never silent for 20 minutes; if it is, reconnect (and try the other address)
setInterval(() => {
  if (!REC.on || REC.ws !== "connected") return;
  const quietSince = REC.liqLastMsg || REC.liqOpened || Date.now();
  if (Date.now() - quietSince > 20 * 60000) {
    REC.lastError = "liquidation stream silent 20 min on " + REC.liqUrl + ", switching"; REC.errors++;
    liqUrl++; const old = liqWs; liqWs = null; try { old && old.close(); } catch (e) {} startLiq();
  }
}, 60000);
function flushLiq() {
  const t = Math.floor(Date.now() / 60000) * 60 - 60; const rows = [];
  for (const [c, e] of liqBuf) rows.push({ t, c, long: Math.round(e.long), short: Math.round(e.short), n: e.n });
  liqBuf.clear(); write("liq", rows);
}
function safeEvery(ms, fn) {           // never overlaps itself, never throws
  let busy = false;
  const run = async () => { if (busy) return; busy = true; try { await fn(); } catch (e) { REC.errors++; REC.lastError = String(e).slice(0, 200); } finally { busy = false; } };
  run(); return setInterval(run, ms);
}
if (REC.on) {
  try { fs.mkdirSync(REC.dir, { recursive: true }); } catch (e) {}
  safeEvery(60000, depthTick); safeEvery(300000, fundingTick); setInterval(flushLiq, 60000); startLiq();
}
// ── HOUSEKEEPING: the disk can never fill up ──────────────────────────────────────────────────────────
// finished days are gzipped (~8x smaller), days older than KEEP_DAYS are deleted, and if free space ever drops
// below 2 GB the oldest days go first. GitHub keeps the hourly summaries forever, so nothing important is lost.
const KEEP_DAYS = +(process.env.REC_KEEP_DAYS || 120);
function dirMB(d) { let s = 0; try { for (const f of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, f.name); s += f.isDirectory() ? dirMB(p) : fs.statSync(p).size / 1e6; } } catch (e) {} return s; }
function diskFreeGB() { try { const st = fs.statfsSync(REC.dir); return (st.bavail * st.bsize) / 1e9; } catch (e) { return null; } }
function housekeep() {
  try {
    const today = day(Date.now()); const days = fs.readdirSync(REC.dir).filter((d) => /^\d{4}-\d\d-\d\d$/.test(d)).sort();
    for (const d of days) {
      if (d >= today) continue;
      for (const f of fs.readdirSync(path.join(REC.dir, d))) {
        if (!f.endsWith(".jsonl")) continue;
        const p = path.join(REC.dir, d, f);
        fs.writeFileSync(p + ".gz", zlib.gzipSync(fs.readFileSync(p))); fs.unlinkSync(p);
      }
    }
    const cutoff = day(Date.now() - KEEP_DAYS * 86400000);
    for (const d of days) if (d < cutoff) fs.rmSync(path.join(REC.dir, d), { recursive: true, force: true });
    let left = fs.readdirSync(REC.dir).filter((d) => /^\d{4}-\d\d-\d\d$/.test(d)).sort();
    while (left.length > 2 && (diskFreeGB() ?? 99) < 2) { fs.rmSync(path.join(REC.dir, left.shift()), { recursive: true, force: true }); }
    REC.housekept = new Date().toISOString().slice(0, 16);
  } catch (e) { REC.errors++; REC.lastError = "housekeeping: " + String(e).slice(0, 150); }
}
if (REC.on) { setTimeout(housekeep, 30000); setInterval(housekeep, 6 * 3600000); }
function sysStats() {
  return { diskFreeGB: diskFreeGB() == null ? null : +diskFreeGB().toFixed(2), recMB: +dirMB(REC.dir).toFixed(1), memFreeMB: Math.round(os.freemem() / 1e6),
           memTotalMB: Math.round(os.totalmem() / 1e6), rssMB: Math.round(process.memoryUsage().rss / 1e6), load1: +os.loadavg()[0].toFixed(2),
           load15: +os.loadavg()[2].toFixed(2), cpus: os.cpus().length, keepDays: KEEP_DAYS, housekept: REC.housekept || null };
}

process.on("unhandledRejection", (e) => { REC.errors++; REC.lastError = "unhandled: " + String(e).slice(0, 150); });
process.on("uncaughtException", (e) => { REC.errors++; REC.lastError = "uncaught: " + String(e).slice(0, 150); });

http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://x");
  if (u.pathname === "/health") { res.end("ok"); return; }
  if (u.pathname === "/where") {
    try { const r = await fetch(FAPI + "/fapi/v1/time"); const g = await fetch("https://ipinfo.io/json").then((x) => x.json()).catch(() => ({}));
      res.end(JSON.stringify({ binance: r.status, country: g.country, city: g.city, org: g.org, node: process.version, recorder: REC.on })); }
    catch (e) { res.end(JSON.stringify({ error: String(e) })); }
    return;
  }
  if (!okKey(req, u)) { res.statusCode = 403; res.end("forbidden"); return; }    // everything below needs the key (on our own server)
  if (u.pathname === "/rec/status") {
    let days = []; try { days = fs.readdirSync(REC.dir).sort(); } catch (e) {}
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ ...REC, dir: undefined, days, node: process.version, uptimeMin: Math.round((Date.now() - REC.started) / 60000), sys: sysStats() })); return;
  }
  if (u.pathname === "/rec/get") {
    const d = u.searchParams.get("day") || "", kind = u.searchParams.get("kind") || "";
    if (!/^\d{4}-\d\d-\d\d$/.test(d) || !["depth", "funding", "liq"].includes(kind)) { res.statusCode = 400; res.end("bad request"); return; }
    const since = +(u.searchParams.get("since") || 0);
    const f = path.join(REC.dir, d, kind + ".jsonl"), fz = f + ".gz";
    const src = fs.existsSync(f) ? fs.createReadStream(f) : fs.existsSync(fz) ? fs.createReadStream(fz).pipe(zlib.createGunzip()) : null;
    if (!src) { res.statusCode = 404; res.end("none"); return; }
    // only rows newer than `since`, compressed on the way out (keeps data transfer tiny)
    const gz = /gzip/.test(req.headers["accept-encoding"] || "");
    res.setHeader("content-type", "application/x-ndjson"); if (gz) res.setHeader("content-encoding", "gzip");
    const out = gz ? zlib.createGzip() : res; if (gz) out.pipe(res);
    const rl = readline.createInterface({ input: src, crlfDelay: Infinity });
    rl.on("line", (l) => { if (!since) { out.write(l + "\n"); return; } const m = /"t":(\d+)/.exec(l); if (m && +m[1] >= since) out.write(l + "\n"); });
    rl.on("close", () => out.end()); src.on("error", () => out.end());
    return;
  }
  if (u.pathname !== "/api/live") { res.statusCode = 404; res.end("not found"); return; }
  req.query = Object.fromEntries(u.searchParams);
  try { await handler(req, res); } catch (e) { res.statusCode = 500; res.end(JSON.stringify({ error: String(e) })); }
}).listen(process.env.PORT || 10000);
