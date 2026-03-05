# KalshiQuant — Real-Time Prediction Market Intelligence Dashboard

A Bloomberg Terminal-style dashboard for Kalshi prediction markets, featuring ML-powered trading signals, fee-aware position sizing, HMM regime detection, and real-time analytics. Built for the DevonomicsV1 hackathon (Mar 20-26, 2026).

![Dashboard Screenshot](docs/screenshot-dashboard.png)

## Features

- **Real-time WebSocket streaming** — Live price feeds from Kalshi API with 30s broadcast cycle
- **5-component adaptive fair value model** — Base rate, orderbook, cross-market, time decay, and sentiment signals with inverse-error weight adaptation
- **XGBoost price prediction** with Platt calibration and walk-forward validation
- **HMM regime detection** — Hidden Markov Model with Gaussian emissions across 5 market regimes (Trending, Mean-Reverting, High-Volatility, Convergence, Stale)
- **Fee-aware Kelly sizing** — Dynamic Kalshi fee calculation with half-Kelly position sizing and per-position caps
- **Cross-market arbitrage scanner** — Detects mispricings across correlated series
- **Implied volatility surface** — Computes market-implied probability distributions from strike series
- **Monte Carlo backtest validation** — 10,000 bootstrap resamples with confidence bands and cluster-adjusted P(profit)
- **Simulated portfolio management** — Category allocation, VaR tracking, hedge suggestions
- **Economic consensus + AI sentiment** — Claude-powered probability estimation with 1h cache

## Architecture

```
Kalshi API (REST + WebSocket)
        |
        v
+-------------------+      +-------------------+
|  Data Pipeline    |      |  Engine           |
|  - Liquidity      |----->|  - MarketState    |
|  - Quality filter |      |  - Orderbook      |
|  - Features       |      |  - WebSocket mgr  |
+-------------------+      |  - Scheduler      |
        |                  +-------------------+
        v                          |
+-------------------+              v
|  ML Models        |      +-------------------+
|  - Fair Value     |----->|  FastAPI Server   |
|  - XGBoost        |      |  - REST endpoints |
|  - Regime (HMM)   |      |  - WS broadcast   |
|  - Risk/Kelly     |      +-------------------+
|  - Ensemble       |              |
|  - Vol Surface    |              v
+-------------------+      +-------------------+
                           |  Next.js Frontend |
                           |  - 6-panel layout |
                           |  - Real-time WS   |
                           |  - SVG charts     |
                           +-------------------+
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, asyncio, WebSockets |
| ML | scikit-learn, XGBoost, hmmlearn, statsmodels, pandas, numpy |
| Frontend | Next.js 14, TypeScript, Tailwind CSS |
| API | Kalshi Trade API v2 (REST + WSS) |

## Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- Kalshi API credentials (key ID + RSA private key)

### Backend

```bash
# Clone and enter project
git clone <repo-url>
cd kalshi-dashboard

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your Kalshi API credentials:
#   KALSHI_API_KEY_ID=your-key-id
#   KALSHI_PRIVATE_KEY_PATH=./private_key.pem
#   ANTHROPIC_API_KEY=sk-... (optional, for AI sentiment)

# Run data pipeline (first time)
python pipeline/scored_markets.py
python pipeline/features.py

# Start backend server
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd dashboard
npm install
npm run dev
# Opens at http://localhost:3000
```

### Run Backtest

```bash
python models/backtest.py
# Results saved to signals/backtest_results.json
```

## Methodology

### Signal Generation
1. **Feature engineering**: 30+ features per market (price, volume, orderbook, momentum, volatility, time-to-expiry)
2. **Fair value estimation**: 5-component Bayesian model with adaptive weights — components compete and the best predictor gets more weight automatically
3. **ML confirmation**: XGBoost classifier with Platt-calibrated probabilities confirms or gates fair value signals
4. **Regime-aware sizing**: HMM detects current market regime; position sizing adapts (e.g., skip TRENDING markets where our models underperform)
5. **Risk management**: Half-Kelly sizing with fee-aware net edge, 6% single-position cap, 60% total deployment limit, correlation-adjusted VaR

### Backtest Results
- Walk-forward expanding window (4 folds, no lookahead)
- Dynamic Kalshi fee model: `fee = ceil(0.07 * P * (1-P) * 100) / 100` per side
- Bootstrap validation: 10,000 resamples with cluster-adjusted confidence
- All metrics net of transaction fees

## Project Structure

```
kalshi-dashboard/
├── pipeline/          # Data ingestion and processing
│   ├── scored_markets.py
│   ├── features.py
│   └── sentiment.py
├── models/            # ML models and backtester
│   ├── fair_value.py
│   ├── price_predictor.py
│   ├── regime_detector.py
│   ├── risk_model.py
│   ├── ensemble.py
│   ├── backtest.py
│   └── vol_surface.py
├── engine/            # Real-time infrastructure
│   ├── market_state.py
│   ├── orderbook.py
│   ├── scheduler.py
│   └── ws_manager.py
├── server/            # FastAPI REST + WS server
│   ├── main.py
│   ├── routes.py
│   └── schemas.py
├── dashboard/         # Next.js frontend
│   └── src/
│       ├── components/
│       └── lib/
├── data/              # Raw and processed data (gitignored)
└── signals/           # Generated signals and backtest results (gitignored)
```

## License

MIT
