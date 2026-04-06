Read ONLY these files: models/backtest.py, models/ensemble.py, dashboard/src/components/Header.tsx, signals/backtest_results.json. Don't read anything else.
CONTEXT: KalshiQuant prediction market dashboard. Backtest produces 20 trades, +$111 net P&L, 68% P(profit). Dynamic Kalshi fees: ceil(0.07 × P × (1-P) × 100)/100 per side. Strategy is convergence trading on near-expiry markets where fair value diverges from price. Everything works, we're polishing for hackathon demo.
DO THESE 5 THINGS:

backtest.py — After trades are computed, build a trade_attribution list. Each trade gets: trade_number, ticker, direction, entry_price, exit_price, exit_reason, gross_pnl, fee_paid, net_pnl, regime, hold_hours, is_winner (net_pnl > 0). Save to signals/trade_attribution.json. Add fee_efficiency = total_net_pnl / total_gross_pnl to the metrics dict.
backtest.py — For convergence trades, before adding to trades list: if estimated_fee > 0.40 × estimated_gross_profit, skip the trade. This kills fee-heavy losers.
Header.tsx backtest modal — Add above the metric cards, three bullet strategy explanation:
'Finds markets where price diverges from fair value by more than transaction costs.'
'Primary alpha: convergence trading near expiry with fee-aware Kelly sizing.'
'Generated +$X net on Y trades. Z% probability of profit (cluster-adjusted Monte Carlo).'
Pull X, Y, Z from the backtest results JSON.
Header.tsx backtest modal — Add fee_efficiency as a new metric card labeled 'Fee Efficiency' with hint 'Net P&L as % of gross — higher means less fee drag'. Add a Trade Log section below the P&L chart showing the trade_attribution table with green/red row backgrounds.
Header.tsx backtest modal — Below metrics, add small gray text: 'Annualized: ~X%' calculated as (net_pnl / 10000) / (test_days / 365) × 100. Add context: 'S&P 500 avg ~10% | Risk-free ~5%'.

Re-run backtest after change #2. Show updated numbers# KALSHI PREDICTIVE MARKET ANALYZER — COMPREHENSIVE BUILD PLAN
## DevonomicsV1 Hackathon (Mar 20–26, 2026)
### Claude Code Execution Blueprint

---

## PROJECT IDENTITY

**Name:** KalshiQuant — Real-Time Prediction Market Intelligence Dashboard
**Tagline:** Institutional-grade analytics for prediction markets. Bloomberg Terminal meets event-driven trading.
**Hackathon:** DevonomicsV1 (Devpost) — Fintech / Productivity / Social Good
**Timeline:** 7 days (Mar 20–26, 2026)
**Submission:** Project + screenshots + demo video (1–3 min) + GitHub link

**Judging Weights:**
- Creativity: 30 pts
- Functionality: 30 pts
- Real-world application: 30 pts
- UI/UX: 7 pts
- Screenshots + GitHub submitted on time: 3 pts

---

## PREREQUISITE: WHAT EXISTS ALREADY

Kevin has already built (in a separate session):
- Kalshi API key secured and authenticated
- Basic data pulling from Kalshi REST API working
- Some initial market data retrieval code

**FIRST TASK FOR CLAUDE CODE:** Read the existing project files to understand current state before building anything new.

---

## PHASE 0: PROJECT RECONNAISSANCE (Claude Code Session 1)

**Goal:** Understand what exists, what data we can actually get, and what is tradeable.

### Step 0.1 — Read Existing Codebase

Prompt for Claude Code:

    Read all files in this project. Summarize:
    1. What API calls are already working
    2. What data structures we are getting back
    3. What authentication method is being used (RSA-PSS vs token)
    4. Current tech stack (Python? JS? What framework?)
    5. Any existing frontend code
    List every file and its purpose.

### Step 0.2 — Verify API Connectivity and Enumerate Available Data

Prompt for Claude Code:

    Create a script called api_health_check.py that:
    1. Authenticates with Kalshi API using our existing credentials
    2. Hits every relevant endpoint and logs response status + sample data:
       - GET /trade-api/v2/markets (list all markets)
       - GET /trade-api/v2/markets?status=open (active only)
       - GET /trade-api/v2/events (list events)
       - GET /trade-api/v2/series (list series)
       - GET /trade-api/v2/markets/{ticker}/orderbook (orderbook depth)
       - GET /trade-api/v2/portfolio/positions (our positions if any)
    3. For each endpoint log: HTTP status, response time ms, record count, sample keys
    4. Save full output to data/api_audit.json
    Print a summary table at the end.

### Step 0.3 — Map the Data Universe

Prompt for Claude Code:

    Using the markets endpoint, pull ALL open markets. For each market extract and save to CSV:
    - ticker, title, event_ticker, series_ticker
    - category, status
    - yes_price, no_price, yes_bid, yes_ask
    - volume, open_interest, liquidity (sum of orderbook depth)
    - close_time, expiration_time
    - last_price, previous_yes_price, previous_price
    Save as data/market_universe.csv
    Print summary stats: total markets, markets by category, avg volume, median spread.

---

## PHASE 1: DATA QUALITY AUDIT & LIQUIDITY FILTERING (Claude Code Session 2)

**Goal:** Determine which markets have enough data quality and liquidity to trade. Kill dry markets immediately.

### Step 1.1 — Liquidity & Volume Analysis

Prompt for Claude Code:

    Create analysis/liquidity_filter.py that reads data/market_universe.csv and:

    1. VOLUME FILTER: Remove markets with volume < 100 contracts traded
    2. SPREAD FILTER: Calculate bid-ask spread. Remove where spread > 10 cents
    3. ORDERBOOK DEPTH: For remaining markets fetch orderbook from API.
       Calculate total dollar depth within 5 cents of mid on both sides.
       Remove markets with < $500 total depth (too thin)
    4. OPEN INTEREST: Remove markets with open_interest < 50
    5. TIME TO EXPIRY: Remove expiring within 2h AND > 90 days out

    Output three files:
    - data/tradeable_markets.csv (pass ALL filters)
    - data/rejected_markets.csv (failed + reason)
    - data/liquidity_report.json (summary stats)

    Print: X of Y markets pass (Z% rejection rate)
    Print top 20 most liquid sorted by volume.

### Step 1.2 — Statistical Tradability Check

Prompt for Claude Code:

    Create analysis/statistical_quality.py that takes data/tradeable_markets.csv and:

    1. For each market fetch historical price data
    2. Run statistical tests:

       a) VARIANCE CHECK:
          - Rolling 1h, 4h, 24h price variance
          - Flag < 2 cent movement in 24h as STALE

       b) MEAN REVERSION vs MOMENTUM:
          - Augmented Dickey-Fuller test for stationarity
          - Hurst exponent (H < 0.5 = mean reverting, H > 0.5 = trending)
          - Determines WHICH ML strategy per market

       c) AUTOCORRELATION:
          - Lag-1 through lag-10 autocorrelation
          - Significant autocorrelation = predictable = tradeable

       d) INFORMATION RATIO:
          - Does new info move the price?
          - Non-reactive markets = untradeable

    3. Assign TRADABILITY SCORE (0-100):
       - Volume weight: 25%
       - Spread tightness: 20%
       - Price variance: 20%
       - Autocorrelation significance: 20%
       - Orderbook depth: 15%

    4. Output: data/scored_markets.csv — keep only score >= 40
    Print top 15 by score with metrics.

### Step 1.3 — Data Cleaning Pipeline

Prompt for Claude Code:

    Create pipeline/data_cleaner.py:

    1. MISSING DATA: Forward-fill gaps < 5 min. Flag > 5 min as DATA_GAP.
    2. OUTLIER DETECTION: Z-score + Bollinger flags (don't remove, just flag)
    3. NORMALIZATION: Cents to 0.0-1.0 probability
    4. FEATURE ENGINEERING per snapshot:
       - mid_price, spread, spread_pct
       - bid_depth_5c, ask_depth_5c
       - volume_1h, volume_24h
       - price_momentum_1h, price_momentum_4h
       - time_to_expiry_hours
       - orderbook_imbalance = (bid - ask depth) / (bid + ask depth)
       - volatility_1h
    5. Output: data/clean_features.parquet

---

## PHASE 2: ML MODEL PIPELINE — QUANT ENGINE (Claude Code Session 3)

**Goal:** Sophisticated models providing real alpha.

### Step 2.1 — Model Architecture

    models/
      __init__.py
      base.py              # Abstract BaseModel + ModelRegistry
      features.py          # Feature pipeline
      fair_value.py        # Bayesian fair value estimation
      price_predictor.py   # XGBoost direction predictor
      regime_detector.py   # HMM regime classification
      risk_model.py        # Kelly + VaR + hedging
      ensemble.py          # Combined signal generator
      backtest.py          # Walk-forward backtesting
      saved/               # Model artifacts
      metrics/             # Performance metrics

### Step 2.2 — Fair Value Model (CORE)

    Bayesian-inspired fair value estimation:

    1. BASE RATE PRIOR: Historical base rates for recurring events
    2. ORDERBOOK SIGNAL: Imbalance + VWAP divergence from mid
    3. CROSS-MARKET SIGNAL: Correlation divergences = mispricings
    4. TIME DECAY: Convergence curve modeling

    fair_value = w1*base_rate + w2*orderbook + w3*cross_market + w4*time_decay
    edge = fair_value - market_price
    If |edge| > 5 cents: TRADE SIGNAL

### Step 2.3 — Short-Term Price Predictor

    XGBoost/LightGBM classifier: price direction next 1h

    Features: orderbook_imbalance (5 lags), volume_acceleration,
    spread_change, momentum (5m/15m/1h/4h), time_to_expiry,
    hour_of_day, day_of_week, volatility_regime, cross_market_momentum

    Target: +1 (up > 2c), -1 (down > 2c), 0 (flat)
    Walk-forward CV, Optuna tuning, Brier score calibration

### Step 2.4 — Regime Detection

    5 REGIMES:
    1. TRENDING: momentum strategy
    2. MEAN_REVERTING: contrarian strategy
    3. HIGH_VOLATILITY: reduce size, widen stops
    4. CONVERGENCE: time decay strategy
    5. STALE: DO NOT TRADE

    Method: HMM with Gaussian emissions OR rolling stats heuristic
    Regime determines which strategy ensemble uses.

### Step 2.5 — Risk Model & Position Sizing

    1. Half-Kelly position sizing
    2. Limits: 10% single market, 25% category, 60% total, 40% reserve
    3. Correlation-adjusted VaR
    4. Stop-loss 15%, take-profit at 2:1, time-based exits
    5. Hedging: correlated opposites, same-market NO, spread trades

### Step 2.6 — Ensemble Signal Generator

    Per market output:
    {
      ticker, title, current_price, fair_value, edge,
      direction (BUY_YES/BUY_NO/HOLD/SELL),
      confidence (0-1), regime, strategy,
      price_prediction_1h, recommended_size,
      max_loss, risk_reward, hedge {ticker, direction, size, correlation},
      reasons [...], alerts [...]
    }
    Top 10 by |edge| * confidence * liquidity -> signals/latest_signals.json

---

## PHASE 3: REAL-TIME ENGINE (Claude Code Session 4)

### Step 3.1 — WebSocket Stream

    Connect: wss://api.elections.kalshi.com/trade-api/ws/v2
    Auth: RSA-PSS headers
    Channels: ticker, orderbook_delta, trade, market_lifecycle_v2

    Architecture:
    WebSocket -> asyncio.Queue -> [Snapshot Updater | Feature Calc | Signal Gen]

    Auto-reconnect, heartbeat, SQLite persistence, reload on restart.

### Step 3.2 — Scheduler

    30s:  Update snapshots + orderbook metrics
    5min: Recompute features + run models + push signals
    1h:   Re-filter liquidity + regime detection + correlation
    24h:  Retrain models + P&L report + archive data

---

## PHASE 4: DASHBOARD UI — BLOOMBERG AESTHETIC (Claude Code Session 5)

### Tech Stack
- Next.js 14 + TypeScript + Tailwind (dark)
- lightweight-charts (TradingView) for candlesticks
- Recharts for gauges/donuts
- WebSocket client for real-time

### Color Palette
- BG: #0a0a0f | Surface: #12121a | Border: #1e1e2e
- Text: #e0e0e0 / #888899
- Green: #00d26a | Red: #ff3b3b | Blue: #3b82f6 | Amber: #f59e0b
- Fonts: JetBrains Mono (prices) + IBM Plex Sans (labels)

### 6-Panel Layout

    +-------------------------------------------------------------------+
    | HEADER: KalshiQuant | Portfolio $X,XXX | P&L +$XX | [*] LIVE      |
    +---------------------------+---------------------------------------+
    | PANEL 1: MARKET SCANNER   | PANEL 2: PRICE CHART                  |
    | Sortable table, live      | TradingView candlestick + volume      |
    | prices, color-coded       | + fair value overlay + orderbook depth |
    | GREEN=buy RED=sell        | Click Panel 1 row to load chart       |
    +---------------------------+---------------------------------------+
    | PANEL 3: SIGNAL DETAILS   | PANEL 4: RISK DASHBOARD               |
    | Fair value vs price bar   | VaR gauge, exposure donut,            |
    | Edge, confidence gauge    | correlation heatmap, Kelly sizing,    |
    | Regime badge, reasoning   | hedge suggestions                     |
    +---------------------------+---------------------------------------+
    | PANEL 5: TRADE BLOTTER    | PANEL 6: LIVE FEED                    |
    | Positions + live P&L      | Scrolling: price moves, signals,      |
    | Entry/current/gain-loss   | regime changes, market events         |
    | Action: hold/exit/add     | Color-coded + timestamped             |
    +---------------------------+---------------------------------------+

### Panel Components

    1. MarketScanner.tsx - Virtualized table, sort/filter/search, flash prices
    2. PriceChart.tsx - lightweight-charts candles, fair value line, depth chart
    3. SignalDetails.tsx - Edge display, confidence gauge, regime, reasoning
    4. RiskDashboard.tsx - VaR gauge, donut, heatmap, limit bars
    5. TradeBlotter.tsx - Position table, P&L, action badges
    6. LiveFeed.tsx - Auto-scroll events, color-coded, virtual scroll

### Backend API (FastAPI)

    REST:
    GET /api/markets, /api/markets/{ticker}, /api/signals,
    /api/signals/{ticker}, /api/portfolio, /api/risk, /api/history/{ticker}

    WebSocket:
    WS /ws/prices, /ws/signals, /ws/feed

---

## PHASE 5: INTEGRATION & POLISH (Claude Code Session 6)

### End-to-End Test
    Test: API -> filter -> features -> models -> signals -> risk -> WS -> UI

### Demo Mode
    Cached data fallback, 10x replay, 3 scenarios:
    a) Calm market b) Volatility spike c) Cross-market divergence

### Submission Checklist
    SCREENSHOTS: Full dashboard, scanner, chart+fair value, risk, signals, feed
    VIDEO (2min): Intro -> Scanner -> Signal deep-dive -> Risk -> Feed -> Close

---

## PROJECT STRUCTURE

    kalshi-dashboard/
      CLAUDE.md              # Claude Code context
      README.md              # Devpost docs
      .env                   # Keys (gitignored)
      requirements.txt / package.json
      data/                  # market_universe, tradeable, scored, features
      pipeline/              # data_fetcher, data_cleaner, liquidity_filter
      analysis/              # liquidity_filter, statistical_quality
      models/                # base, features, fair_value, price_predictor,
                             # regime_detector, risk_model, ensemble, backtest
      engine/                # realtime_stream, scheduler, demo_mode
      server/                # FastAPI main, routes, schemas, ws_manager
      app/                   # Next.js pages
      components/            # 6 panel components + ui/
      signals/               # latest_signals.json
      tests/                 # integration + unit
      scripts/               # health check, seed data

---

## EXECUTION ORDER

    Session 1 (2-3h): Phase 0 — Read code, API audit, market universe
    Session 2 (3-4h): Phase 1 — Liquidity filter, stats tests, cleaning
    Session 3 (4-6h): Phase 2 — ML models (fair value, predictor, regime, risk, ensemble)
    Session 4 (2-3h): Phase 3 — WebSocket stream, engine, scheduler
    Session 5 (4-6h): Phase 4 — Dashboard UI (6 panels) + backend
    Session 6 (2-3h): Phase 5 — Integration test, demo, screenshots

    Total: 20-25 hours across 7 days

---

## WHY THIS WINS

    Creativity (30): Nobody builds quant-grade prediction market terminals at hackathons.
    Functionality (30): Real ML pipeline: data -> clean -> stats -> model -> signal -> risk.
    Real-world (30): Kalshi $200M+ revenue. No good retail analytics tool exists.
    UI/UX (7): Dark terminal with real-time WebSocket. Looks professional.
    On time (3): Follow checklist.
