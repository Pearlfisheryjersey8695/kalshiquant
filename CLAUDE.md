# KalshiQuant

Real-time trading dashboard for Kalshi prediction markets.
Hackathon: DevonomicsV1 Mar 20-26 2026

## Tech Stack
Backend: Python 3.11, FastAPI, asyncio, WebSockets
ML: scikit-learn, XGBoost, statsmodels, pandas
Frontend: Next.js 14, TypeScript, Tailwind, lightweight-charts

## Kalshi API
Base: https://api.elections.kalshi.com/trade-api/v2
WS: wss://api.elections.kalshi.com/trade-api/ws/v2
Auth: RSA-PSS signed requests
Note: elections subdomain = ALL markets not just elections

## Pipeline
1. API raw data
2. Liquidity filter (kill dry markets)
3. Statistical quality (ADF Hurst autocorrelation)
4. Clean + feature engineering
5. ML models (fair value + predictor + regime)
6. Risk (Kelly VaR hedging)
7. Ensemble signals
8. Dashboard 6-panel terminal
