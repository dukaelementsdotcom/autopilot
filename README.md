# Research Machine

An autonomous, free, paper-only research lab for crypto. Every day it:

1. **Downloads** fresh public data from Binance's free archive (data.binance.vision) for 21 coins: hourly candles with real taker-buy volume, open interest, the crowd's long/short ratio, and top traders' positions. Only new files are downloaded.
2. **Invents ideas** by itself, from five sources:
   - **scan**: a systematic sweep of every measurement, extreme, direction and exit (tens of thousands of ideas, worked through over weeks)
   - **evolve**: mutations of ideas that survived or nearly survived
   - **combine**: two survivors that must agree (the "confirmation" idea)
   - **AI researcher**: a free Groq model reads the machine's own notes and proposes new hypotheses, each with a market mechanism
   - **explore**: random rules, so it also tries things nobody would think of
3. **Judges each idea honestly** on three separate stretches of time:
   - **discovery**: the old years
   - **validation**: the next year. The idea must hold up there and beat random entries.
   - **final exam**: the newest 4 months, used once. Only ideas that pass all three become **champions**.
4. **Paper-trades the champions** on data that did not exist when they were promoted. After 30 live trades, a losing champion is **dropped**, and one with a strong record is marked **proven**.
5. **Forgets the detail but keeps the knowledge.** Dead ideas are folded into family statistics ("we tried 400 open-interest ideas; best t 3.1; 2 survivors"). A fingerprint of every idea tested is kept, so nothing is ever retested. Memory stays a few MB forever.
6. **Reports**: it writes a dashboard web page, It sends **one kind of message only**: when a rule passes the "undeniable" checklist (see below). Otherwise it stays silent; check the dashboard whenever you like.

It costs nothing: GitHub Actions, GitHub Pages, the Binance public archive and the Groq free tier. It never touches an exchange account and needs no exchange keys.

---

## Deploy (about 15 minutes, once)

### 1. Create the repository
1. Make a free account at github.com.
2. Click **+ → New repository**:
   - Name: `research-machine`
   - Visibility: **Public**. On a free account, public repos get unlimited Actions minutes and free Pages. Your keys are still hidden, because secrets are never shown.
3. Click **uploading an existing file**. Drag **all the contents** of this folder in, including the hidden `.github` folder.
   - If your computer hides it, upload the rest first. Then use **Add file → Create new file**, name it `.github/workflows/research.yml`, and paste in that file's content.
4. Click **Commit changes**.

### 2. Add your 3 secrets (Settings → Secrets and variables → Actions → New repository secret)
| Name | What it is | Where to get it |
|---|---|---|
| `GROQ_API_KEY` | The AI researcher (optional but recommended) | console.groq.com → API Keys → **Create a NEW key**. The old one was shared in chat, so delete it. |
| `TELEGRAM_BOT_TOKEN` | Your bot (optional) | In Telegram, message **@BotFather**, send `/newbot`, and copy the token |
| `TELEGRAM_CHAT_ID` | Where messages go (optional) | Send any message to your new bot, then message **@userinfobot** to get your id |

Never paste these anywhere else. If one leaks, delete it and make a new one.

### 3. Turn on the dashboard
Go to **Settings → Pages → Build and deployment → Source: GitHub Actions**.

### 4. Press the button once
Go to **Actions**. If asked, click "I understand… enable workflows". Then open **research → Run workflow**.
- The first run downloads about 6 years of data and takes about 15–25 minutes. After that, each daily run takes about 5–10 minutes.
- From then on it runs **by itself every day at 03:17 UTC**, forever, until you stop it.

Your dashboard: `https://<your-github-name>.github.io/research-machine/`. The run log also shows the link.

**To stop it:** Actions → research → ⋯ → **Disable workflow**. To resume, enable it again; the machine remembers everything.

---

## How to read it
- **Champion**: passed discovery, validation, the random-entry test and the final exam, and is now earning a live paper record. It is still not proof; it is a candidate.
- **Proven live**: 30+ live paper trades with a statistically positive result.
- **Dropped**: it lost money live and was removed. This is the machine protecting you.
- **Near miss**: looked good in the old data but not convincing yet in the new data.
- **Most ideas die.** Expect thousands of tests per champion. A quiet week means the bar is working, not that the machine is broken.

## Storage housekeeping (automatic)
- **Memory**: one file on the `state` branch, replaced each day as a single commit, so history never piles up. Old dead ideas are folded into family counters, with about 4,000 detailed records at most.
- **Market data**: about 100 MB in the GitHub Actions cache. GitHub deletes old cache copies automatically. If the cache is ever lost, the next run re-downloads it.
- **Heartbeat**: once a month the workflow makes a tiny commit, because GitHub pauses schedules in repos with no activity for 60 days.

## Run it on your own computer (optional)
```
pip install -r requirements.txt
python -m rm.run          # one research day; the page appears in docs/index.html
```

## Honest limits
- The data is about 1 day behind, because the archive publishes daily. These are research signals, not a live trading bot.
- Fees are included (0.1% per trade). Funding and slippage on small coins are not, so live results will be a bit worse.
- Testing thousands of ideas finds some by luck. The validation year, the random-entry test, the untouched final exam and the live record exist to catch those, but no method is perfect. Paper first. Never risk money you can't lose.

## The only Telegram message: "undeniable" edge
Every champion is stress-tested each week. A message is sent only when **all** of these are true at once:
- **Live record:** at least 100 live paper trades over at least 90 days, with live t ≥ 3. These are trades on data that didn't exist when the rule was chosen.
- **Luck bar:** t over the whole history is at least √(2·ln N), where N is every idea the machine has ever tried. The more it searches, the higher the bar gets.
- **Every year:** profitable in every calendar year.
- **Every market:** profitable in uptrends, downtrends, calm markets and wild markets.
- **Most coins:** profitable on at least 70% of the coins it trades.
- **Costs:** still profitable with double fees.
- **Robustness:** nearby variants (slightly different thresholds) are also profitable, so it isn't one lucky setting.

You can see each champion's checklist (✔/✘) on the dashboard.
