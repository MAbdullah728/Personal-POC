# AI Crypto Trading Bot (Binance Spot + Futures) — Architecture & Build Plan

## Context

The user wants an AI-driven trading bot for a single Binance account, trading both Spot and USDT-M Futures, combining LLM-based news/fundamental reasoning, rule-based technical strategy, and an AI-tuned/feedback-improving component, gated by an 85% confidence threshold. Futures trading leans toward scalping with aggressive (10x+) leverage; spot mixes scalping and day trades held up to ~4-5 hours. The bot must run progressively — backtest → paper trade → feedback-driven improvement → eventually live — under manual on/off control for specific sessions (not unattended 24/7), starting on the user's local machine (~5-7 hrs/day), with a low-priority Next.js dashboard and local structured logs for monitoring. Given real leveraged capital is the eventual goal, safety/risk correctness is treated as the top priority throughout, not an afterthought.

This plan lays out the full system architecture and a phased roadmap, but **only Phase 0 (project scaffolding + Binance testnet connectivity + config + logging) is being built now.** Every later phase is deliberately staged behind a review checkpoint — this is too consequential a system to build in one uninterrupted pass.

## System Architecture (recommended, applies across all phases)

**Single Python process (modular monolith), async-first — not microservices.** Scale is one account/one operator/part-time runtime; a distributed architecture would add network/ordering failure modes to a system that controls leveraged capital, for no real benefit here. Clean internal module boundaries keep it splittable later if ever needed.

- **Concurrency:** `asyncio` for all I/O (Binance websockets, REST, LLM calls, news polling) + APScheduler for periodic jobs (bias refresh, retraining, kill-switch heartbeat).
- **Internal comms:** critical path (signal → fusion → risk gate → order) is a **plain synchronous call chain** — never pub/sub — so a risk check can never be silently skipped by delivery semantics. A lightweight async event bus is used only for non-critical fan-out (dashboard stream, logging, retrain triggers).
- **Persistence:** SQLite (WAL, via SQLAlchemy) for operational state (orders, positions, trades, bias cache, kill-switch flags, param versions); Parquet files for historical market data (backtesting); structured JSON logs (rotating files) as the audit trail — every bias/signal/fusion/risk-gate decision (incl. *why rejected*) gets one log line. No Postgres/Timescale needed at this scale.

**Major components:**
1. **Data ingestion** — Binance websockets (klines, bookTicker, markPrice/funding) + REST fallback; news/sentiment pollers (Binance news, FinancialJuice, X).
2. **Bias engine (LLM layer)** — periodic per-symbol job (~every 10-15 min, plus a rate-limited event-triggered early refresh), calls an LLM with **enforced structured output** (recommend Claude API — consult the `claude-api` skill when this phase is implemented), returns `{regime, directional_bias, confidence, volatility_flag, key_drivers, ttl_seconds}`. Cached with a hard TTL; **missing/expired bias = confidence 0, never "neutral 50."**
3. **Fast signal engine (technical/ML)** — runs on candle close (and tick/orderbook events for scalp timing); rule-based indicators + a lightweight trained model (gradient-boosted tree, not a heavy deep net — inference must be sub-second locally); outputs direction + technical confidence.
4. **Confidence fusion/gate** — pure function. Requires bias non-stale, `volatility_flag=false`, **direction agreement** (no averaging opposing signals), and combined confidence via **geometric mean** `sqrt(technical_confidence * bias_confidence) >= 85`, with a floor of 65 on each individual confidence (prevents one very strong + one weak signal from clearing the bar).
5. **Risk & position-sizing engine** — the **hard, non-bypassable gate**: `authorize_and_size(trade_intent) -> ApprovedOrder | Rejected` is the *only* path to the OMS. AI layers propose; this disposes. Covered in detail below.
6. **Execution/OMS** — the only module allowed to call Binance's trading endpoints; manages entry+protective-order lifecycle, reconciles against Binance's account/position stream.
7. **Backtesting engine** — vectorized replay over historical Parquet klines, reusing the *same* fusion/risk code as live (not a reimplementation).
8. **Paper-trading simulator** — runs the real pipeline end-to-end against live market data, swapping only the OMS for a `SimulatedOMS` — so paper logs are valid input to the feedback loop.
9. **Feedback loop** — offline, periodic, **not online RL** (see rationale below).
10. **Local control API + Next.js dashboard** — FastAPI on localhost (session start/stop, positions, P&L, trade history, kill-switch), minimal Next.js UI on top (explicitly low priority per user).

### Why the feedback loop is offline batch optimization, not online RL
Part-time sessions (5-7 hrs/day) yield only dozens of trade outcomes per session — too little signal for online policy-gradient RL without mostly learning noise. The real decision space here (thresholds, stop distance, R:R, sizing curve, indicator weights) is a bounded, human-defined parameter set — closer to a contextual-bandit/hyperparameter-search problem. So: log every trade with full context (bias snapshot, signal snapshot + features, param version, realized R-multiple, slippage, MAE/MFE) → run a batch search (Optuna) over parameters at end-of-session/nightly → validate any "challenger" config via walk-forward backtest + replay against held-out paper logs → promote only as a **versioned, git-committed config bump** requiring a minimum number of new paper sessions before live eligibility. This gets the "improves from feedback" behavior safely and auditably. True RL, if wanted later, should be scoped narrowly (e.g. exit-timing only), not entries.

### Risk/safety engine (the most important part of this system)
The user explicitly chose **aggressive 10x+ futures leverage** combined with scalping and tight stops — a real liquidation risk if not compensated architecturally. Non-negotiable design responses:
- **Fixed R:R** computed atomically at order-construction time (entry+stop+target together).
- **No naked positions**: entry and protective stop are one atomic operation from the risk engine's view; if stop placement fails post-fill, the fallback is an **immediate emergency market close**, not a retry.
- **Real liquidation math**: pull tiered maintenance-margin brackets live from `/fapi/v1/leverageBracket` (never hardcode a flat %); maintain live distance-to-liquidation per position with a hard safety margin that accounts for realistic slippage, not just "stop is numerically before liquidation."
- **Wallet-size-based auto-tapering**: `leverage_and_size(equity, high_water_mark, recent_volatility) -> (max_leverage, max_position_size)` — full 10x+ only near equity high-water-mark under normal volatility; tapers automatically on drawdown or volatility spikes.
- **Isolated margin per position** (not cross) as the default, so one bad scalp can't drain the whole futures wallet.
- **Kill-switches enforced inside the risk engine itself** (not just dashboard suggestions): manual session stop, session max-loss halt, consecutive-loss circuit breaker, liquidation-proximity emergency stop, data/connectivity heartbeat (stale feed blocks new entries).
- This module gets the highest test coverage in the codebase, including property-based tests (Hypothesis) for sizing/liquidation math.

### Binance-specific gotchas the design already accounts for
Spot and USDT-M Futures are **separate wallets/APIs** even on one account (no Unified Margin, to keep liquidation math simple) — leverage tapering computed per-wallet. Spot Testnet and Futures Testnet are **separate testnets** with separate base URLs/keys — the client wrapper treats network (spot/futures × testnet/mainnet) as an explicit config axis. Separate, minimally-scoped API keys per market (no withdrawal permission ever; IP-whitelisted once the local IP is stable). Market data via websockets only, never REST polling in a hot loop (rate-limit risk). Spot uses OCO orders for stop+target; **Futures has no true OCO** — independent `STOP_MARKET`/`TAKE_PROFIT_MARKET` (`reduceOnly=true`) with manual cancel-the-other-on-fill reconciliation. Funding rate doesn't move liquidation price directly but drains margin balance every 8h while a position spans a funding timestamp — relevant for the up-to-5h spot/futures holds.

## Strategy & indicator design (technical layer)

**Support/resistance detection** (used for both entries and stop placement): swing high/low pivots via N-bar fractals, prior session/day high-low, round-number levels, and volume profile (POC/HVN/LVN) computed from recent klines to find where real trading interest has clustered. For futures scalps, order-book depth clustering (resting liquidity walls) adds a shorter-horizon S/R signal if depth snapshots are available. These levels drive both "trade off this level" entries and "stop beyond this level + ATR buffer" placement — they are not a cosmetic overlay, they're structural inputs to the strategies below.

**Core indicator set:**
- Trend/momentum: EMA(9/21/50) stack for trend direction, MACD for momentum confirmation, RSI(14) for overbought/oversold + divergence.
- Volatility: ATR(14) for stop distance sizing and as an input to the bias engine's `volatility_flag` threshold; Bollinger Bands for squeeze/expansion regime detection.
- Volume: session VWAP as a fair-value reference, relative volume spikes to confirm breakouts, volume profile for S/R (above).
- Order flow (futures scalping, if depth/trade-stream data is used): order-book imbalance ratio, aggressive taker buy/sell ratio.

**Named strategies to implement first** (each is a self-contained module behind a common interface `evaluate(candles, orderbook) -> SignalCandidate(direction, confidence, stop_price, target_price, reason)`, so adding more later doesn't touch fusion/risk code):
1. **Futures scalp — EMA/VWAP pullback continuation**: trend defined by EMA9/21/50 stack order; entry on a pullback to VWAP or EMA21 with RSI recovering through 50 in trend direction; stop beyond the most recent swing point + ATR buffer; fixed R:R target.
2. **Futures scalp — Range S/R bounce**: price reaches a well-tested S/R level (multiple prior touches / volume-profile HVN) with RSI divergence against the level; counter-trend scalp, tight stop just beyond the level.
3. **Spot day trade — Breakout continuation**: price clears a session/multi-day resistance (or breaks support, for shorts) with a volume spike confirming it; target a measured move, stop back below/above the breakout level.
4. **Spot day trade — Mean reversion at range extreme**: inside a defined trading range, fade the extreme with RSI/Bollinger Band confirmation, target mid-range or the opposite band.

Each strategy outputs a raw technical confidence (0-100) based on how many of its confirming conditions are met; this is the `technical_confidence` that feeds the fusion gate in section B above.

## Sentiment / fundamental layer detail

- News/social items (Binance news, FinancialJuice, X) are turned into **structured evidence** before reaching the LLM — title/summary/timestamp/source, filtered to symbol relevance — never a raw text dump into the prompt.
- A cheap **lexicon-based pre-filter** (e.g. VADER or a crypto-tuned wordlist) discards clearly irrelevant/neutral items before they cost LLM tokens; the LLM does the real aggregation into `regime`/`directional_bias`/`confidence`/`key_drivers`, rather than the code hand-rolling sentiment scoring.
- **Recency-weighted**: only items within a rolling window (e.g. last 30-60 min) count as "current" for a given bias refresh; a sudden cluster of high-sentiment items is exactly what triggers the rate-limited early-refresh described in section B.
- `volatility_flag` is set true around scheduled high-impact events surfaced through these same sources (macro releases, major listings/delistings, etc.) — this is the mechanism that makes the fusion gate hard-abstain through news risk instead of trading into it.

## What you need to provide

- **Phase 0 (now)**: nothing yet beyond confirming the plan — you'll generate **Binance Spot Testnet** and **Binance Futures Testnet** API keys yourself from Binance's respective testnet portals once the scaffolding exists, and paste them into a git-ignored `.env` file I'll create a template for. Keys are never typed into chat.
- **Phase 3 (bias engine)**: an Anthropic API key (for Claude, used with structured output for the bias schema); a decision on X/Twitter API access (free tier is very restricted — may need a paid tier or a reduced-scope fallback, to confirm when we get there); confirmation of how FinancialJuice content is actually accessible (RSS vs. other) given it has no official public API on the free tier.
- **Phase 1 onward**: your preferred watchlist symbols (default assumption: BTCUSDT + ETHUSDT to start, expandable later) and any strong preferences on the specific indicator parameters above (defaults given if you don't have preferences yet).
- **Phase 8 (mainnet live)**: mainnet API keys, trade-only permissions, no withdrawal, IP-whitelisted, plus your intended starting live capital so the manual global size/leverage caps for the first live sessions can be set sensibly.

## Repo structure (monorepo)

```
trading-bot/
  backend/
    pyproject.toml
    src/tradingbot/
      config/                 # versioned YAML: risk params, symbols, LLM prompts
      data_ingestion/         # binance_ws.py, binance_rest.py, news/ (twitter_x, binance_news, financialjuice)
      bias_engine/            # llm_client.py, prompts.py, cache.py
      signal_engine/          # indicators.py, ml_model.py, rules.py
      fusion/                 # gate.py
      risk_engine/            # sizing.py, liquidation.py, kill_switches.py, gate.py
      execution/              # oms.py, simulated_oms.py, reconciliation.py
      backtest/               # engine.py, data_loader.py
      feedback_loop/          # trade_logger.py, retrain.py, param_search.py
      api/                    # server.py (FastAPI)
      persistence/            # models.py, db.py (SQLAlchemy)
      logging_setup.py
      main.py
    tests/
    data/historical/          # Parquet klines
    data/operational.db       # SQLite
    logs/
  dashboard/                  # Next.js app (later phase)
  configs/risk/v1.yaml
  configs/strategy/v1.yaml
  README.md
```

## Phased roadmap (for context — only Phase 0 is in scope for this build pass)

| Phase | Deliverable | Verify |
|---|---|---|
| **0 (now)** | Repo skeleton, `structlog` rotating-file logging, YAML config loader, Binance client wrapper connecting to **both** Spot Testnet and Futures Testnet, fetch account balance, subscribe to one symbol's kline websocket | Run `main.py`; see logged testnet balances (spot + futures) and live candle closes; no trading logic yet |
| 1 | Fast technical signal engine + backtest engine (no LLM yet) | Backtest BTCUSDT/ETHUSDT over a few months; plausible trade log + win rate/avg R/max DD |
| 2 | Risk & sizing engine (real liquidation brackets, wallet-based tapering, kill-switches), fully unit/property-tested, wired into backtest | Tests green; backtest shows leverage auto-taper after simulated drawdown; every entry has a stop |
| 3 | Bias engine (LLM + news ingestion) + fusion gate | 1hr testnet dry-run, log-only (no orders); verify refresh cadence, TTL expiry, gate math on real data |
| 4 | OMS live-wired on **testnet** (real orders, fake money) | Full session; every position gets a resting stop within ms; kill-switches actually cancel/flatten when forced |
| 5 | Paper trading against **mainnet market data** (SimulatedOMS, no real orders) | Several real-market-hours sessions; realistic fills (testnet liquidity is not representative) |
| 6 | Feedback loop: trade logger schema, Optuna param search, walk-forward validation, versioned promotion | Run retrain job on accumulated paper logs; confirm challenger report + no silent hot-swap |
| 7 | Next.js dashboard + local FastAPI control layer | Start/stop paper session from UI, live P&L updates, manual kill-switch trigger from UI actually halts engine |
| 8 | Mainnet live-toggle, small capital, extra manual global caps on top of automatic ones | Short closely-watched live sessions; compare live fills/slippage vs. paper assumptions before increasing size |

## Open items to resolve before Phase 3 (not blockers for Phase 0)
- **X/Twitter API**: the free tier is very restricted (largely post-only, no search) — real sentiment pulling likely needs a paid tier or a fallback/reduced scope. Will confirm feasibility when we build the news pollers.
- **FinancialJuice**: no official public API on the free tier — need to check what's actually accessible (RSS feed vs. scraping) and respect their ToS before building against it.
- **LLM provider for the bias engine**: recommend the Claude API (structured output support fits the safety-critical schema requirement) — will use the `claude-api` skill for implementation specifics in Phase 3.

## Phase 0 — what gets built right now

1. `backend/pyproject.toml` with dependencies: `python-binance` (or `binance-connector`), `asyncio`/`websockets`, `structlog`, `pyyaml`, `pydantic` (config/schema validation), `pytest`.
2. `backend/src/tradingbot/logging_setup.py` — `structlog` configured to write structured JSON to rotating files under `backend/logs/`.
3. `backend/src/tradingbot/config/` — YAML config loader (pydantic models) for: which network per market (spot-testnet/futures-testnet), symbols to watch, API key env-var names (keys themselves stay in a git-ignored `.env`, never committed).
4. `backend/src/tradingbot/data_ingestion/binance_rest.py` + `binance_ws.py` — thin async wrapper connecting to **both** Spot Testnet and Futures Testnet: fetch account balance on each, subscribe to one symbol's (e.g. BTCUSDT) kline websocket on both markets, log each candle close via `logging_setup`.
5. `backend/src/tradingbot/main.py` — wires config → logging → clients → runs until interrupted (Ctrl+C clean shutdown).
6. `backend/tests/` — a couple of unit tests for the config loader (no live network calls in tests).
7. `.gitignore` (env files, `logs/`, `data/operational.db`, `__pycache__`), `README.md` stub, and initialize this as a git repository (it currently is not one).
8. **User-provided inputs needed to actually run Phase 0**: Binance **Spot Testnet** and **Futures Testnet** API keys (separate signup/keys per Binance's testnet portals) — I'll pause and ask for these (or confirm where to put them in `.env`) once the scaffolding is written, rather than needing them before writing code.

### Verification for Phase 0
Run `python -m tradingbot.main` (or equivalent) with testnet keys in `.env`; confirm structured log output shows: successful connection + fetched balance for both Spot Testnet and Futures Testnet, and a stream of kline-close log lines for the watched symbol on both markets. No orders are placed in this phase — it's connectivity + plumbing only.
