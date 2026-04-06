# KalshiQuant - Prediction Market Trading Terminal

> Institutional-grade quantitative trading system for Kalshi prediction markets. Bloomberg Terminal meets event-driven trading.

![Python](https://img.shields.io/badge/Python-3.11-blue) ![Next.js](https://img.shields.io/badge/Next.js-14-black) ![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## What Is This?

A full-stack quantitative trading platform that analyzes prediction markets on [Kalshi](https://kalshi.com), generates trading signals using ML models and external data, and executes trades through an autonomous AI agent (QuantBrain).

**This is NOT a toy.** It includes:
- Real-time WebSocket streaming from Kalshi API
- Fair value models driven by external data (CoinGecko, Yahoo Finance, FRED)
- Walk-forward backtesting with fee-aware Kelly sizing
- Autonomous trading agent with reinforcement learning
- Bloomberg-style 9-tab dashboard with live risk monitoring

## Architecture

```
Kalshi WS --> MarketState --> LiveFeatures --> Ensemble --> QuantBrain --> Trade
                |                                            |
                |-- OrderbookStore                           |-- 11-step pre-trade checklist
                |-- ExternalFeeds (BTC, SPX, Fed, Gas)       |-- Thesis-driven reasoning
                                                             |-- RL policy (tabular Q-learning)
                                                             |-- Calibration tracker
```

## Dashboard

| Key | Tab | Description |
|-----|-----|-------------|
| F1 | **Dashboard** | Market scanner, price chart, signal detail, trade blotter |
| F2 | **Risk Engine** | VaR, correlations, P&L calendar, equity curve, position heatmap |
| F3 | **Strategy Lab** | Configure strategies, risk limits, backtest integration |
| F4 | **Backtest** | Walk-forward results, Monte Carlo, equity curve, trade log |
| F5 | **Sentiment** | Signal agreement, regime distribution, arbitrage scanner |
| F6 | **Morning Brief** | Overnight P&L, live news, opportunities, alerts |
| F7 | **Trade Journal** | Full trade history with filters, notes, signal context |
| F8 | **Performance** | Monthly returns, rolling Sharpe, drawdown, attribution |
| F9 | **Quant Brain** | Autonomous agent: theses, RL policy, lessons learned |

## Tech Stack

**Backend:** Python 3.11, FastAPI, asyncio, WebSockets, SQLite
**ML:** scikit-learn, XGBoost, statsmodels, hmmlearn
**Frontend:** Next.js 14, TypeScript, Tailwind CSS, lightweight-charts
**Data:** Kalshi REST/WS API, CoinGecko, Yahoo Finance, FRED

## Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/kalshi-dashboard.git
cd kalshi-dashboard

# Python deps
pip install -r requirements.txt

# Frontend deps
cd dashboard && npm install && cd ..

# Configure
cp .env.example .env
# Add your Kalshi API key and RSA private key path

# Run backend
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000

# Run frontend (separate terminal)
cd dashboard && npm run dev
```

Open **http://localhost:3000**

## How Alpha Is Generated

Traditional approaches use market price history as "fair value" -- circular. We use **external data**:

| Market | Source | Method |
|--------|--------|--------|
| Bitcoin (KXBTC) | CoinGecko spot + vol | Lognormal P(BTC > strike) |
| S&P 500 (KXINX) | Yahoo Finance + VIX | Digital option pricing |
| Fed Rate (KXFED) | FRED / CME FedWatch | Rate path probability |
| Gas (KXAAAG) | FRED weekly average | Mean-reversion model |

External data carries **50% weight** in the fair value model -- it's the primary edge source.

## QuantBrain Agent

Every trade goes through an 11-step checklist:

1. Edge real and large enough?
2. Fees eating the edge?
3. Regime supports the trade?
4. Edge stable or decaying?
5. Contrarian check (extreme prices)
6. Time decay analysis
7. Portfolio capacity (heat, positions)
8. Sentiment alignment
9. XGBoost prediction agreement
10. External data model agreement
11. RL policy check (Q-learning)

Generates a **TradeThesis** with conviction score, confidence reasons, risk factors, and invalidation criteria. Learns from outcomes via tabular Q-learning.

### Go-Live Gates (paper trading until ALL pass)

| Metric | Threshold |
|--------|-----------|
| Settled trades | >= 50 |
| Cumulative P&L | > $0 |
| Win rate | > 52% |
| Brier score | < 0.20 |
| Model alpha | > 0 (better than market) |
| Max drawdown | < 15% |

## Risk Management

- Half-Kelly with calibrated win probabilities
- Auto kill-switch (5% drawdown / 3% hourly loss)
- Correlation-adjusted VaR with binary jump risk
- Smart alerts: edge decay, signal flip, expiration, concentration
- Fee gate: rejects trades where fees > 40% of gross edge

## Project Structure

```
kalshi-dashboard/
  app/           # Kalshi API client (REST + WS auth)
  analysis/      # Liquidity filter, statistical quality, calibration
  data/          # External feeds (CoinGecko, Yahoo Finance, FRED)
  engine/        # Real-time engine, QuantBrain, execution, positions
  models/        # Fair value, XGBoost, HMM regime, risk, ensemble, backtest
  pipeline/      # Data cleaning, sentiment
  server/        # FastAPI, routes, WebSocket manager, risk engine, alerts
  dashboard/     # Next.js 14 frontend (9-tab Bloomberg terminal)
```

## Disclaimer

Research/educational project. Prediction market trading involves substantial risk. Defaults to paper trading -- will not execute real orders unless `LIVE_TRADING=true`.

## License

MIT
