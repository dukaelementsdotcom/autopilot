// The same live relay as a tiny always-free web service (e.g. Render, region Frankfurt). No dependencies.
// GET /api/live?coins=BTC,ETH&pages=1&kl=72  ->  same JSON as the Vercel relay.   GET /where -> which country Binance sees us in
const http = require("http");
const handler = require("./live.js");
http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://x");
  if (u.pathname === "/health") { res.end("ok"); return; }
  if (u.pathname === "/where") {
    try { const r = await fetch("https://fapi.binance.com/fapi/v1/time"); const g = await fetch("https://ipinfo.io/json").then((x) => x.json()).catch(() => ({}));
      res.end(JSON.stringify({ binance: r.status, country: g.country, city: g.city, org: g.org })); }
    catch (e) { res.end(JSON.stringify({ error: String(e) })); }
    return;
  }
  if (u.pathname !== "/api/live") { res.statusCode = 404; res.end("not found"); return; }
  req.query = Object.fromEntries(u.searchParams);
  try { await handler(req, res); } catch (e) { res.statusCode = 500; res.end(JSON.stringify({ error: String(e) })); }
}).listen(process.env.PORT || 10000);
