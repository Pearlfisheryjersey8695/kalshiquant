// ── API Response Types (matches server/schemas.py) ──────────────────────────

export interface Market {
  ticker: string;
  price: number;
  yes_bid: number;
  yes_ask: number;
  volume: number;
  open_interest: number;
  last_update_ts: string;
  title: string;
  category: string;
  tradability_score: number;
  expiration_time: string;
}

export interface DecayPoint {
  minutes: number;
  edge: number;
}

export interface Signal {
  ticker: string;
  title: string;
  category: string;
  current_price: number;
  fair_value: number;
  edge: number;
  net_edge: number;
  fee_impact: number;
  meta_quality: number;
  predicted_change: number;
  direction: "BUY_YES" | "BUY_NO" | "HOLD";
  confidence: number;
  regime: string;
  strategy: string;
  price_prediction_1h: number;
  prediction_confidence: number;
  recommended_contracts: number;
  risk: {
    kelly_fraction: number;
    size_dollars: number;
    contracts: number;
    stop_loss: number;
    take_profit: number;
    true_max_loss: number;
    stop_loss_amount: number;
    max_gain: number;
    risk_reward: number;
    net_edge: number;
    fee_impact: number;
    total_fees: number;
  };
  hedge: {
    ticker: string;
    direction: string;
    correlation: number;
  } | null;
  reasons: string[];
  volume: number;
  open_interest: number;
  tradability_score: number;
  expiration_time: string | null;
  decay_curve?: DecayPoint[];
  consensus_edge?: number;
  consensus_prob?: number;
  sentiment_edge?: number;
  ai_prob?: number;
  ai_edge?: number;
  ai_reasoning?: string;
  regime_probs?: Record<string, number>;
  fv_weights?: Record<string, number>;
}

export interface SignalsEnvelope {
  generated_at: string;
  portfolio_value: number;
  total_signals: number;
  signals: Signal[];
}

export interface HistoryPoint {
  ts: string;
  price: number;
  yes_bid: number;
  yes_ask: number;
  volume: number;
}

export interface FeedEvent {
  seq: number;
  ts: string;
  event_type: "PRICE_MOVE" | "SIGNAL_CHANGE" | "REGIME_CHANGE" | "TRADE" | "CONNECTION" | "ERROR";
  ticker: string;
  message: string;
  data: Record<string, unknown>;
}

export interface Portfolio {
  balance: number;
  positions: PortfolioPosition[];
}

export interface PortfolioPosition {
  ticker: string;
  market_exposure: number;
  total_traded: number;
  realized_pnl: number;
  resting_orders_count: number;
  fees_paid: number;
  [key: string]: unknown;
}

export interface RiskData {
  var_95: number;
  positions: {
    ticker: string;
    contracts: number;
    current_price: number;
  }[];
}

export interface ArbitrageOpportunity {
  type: string;
  prefix: string;
  buy_ticker: string;
  sell_ticker: string;
  buy_price: number;
  sell_price: number;
  edge: number;
  description: string;
}

export interface CorrelationEntry {
  t1: string;
  t2: string;
  corr: number;
}

export interface DivergenceAlert {
  t1: string;
  t2: string;
  correlation: number;
  spread: number;
  signal: string;
}

export interface CorrelationData {
  tickers: string[];
  matrix: CorrelationEntry[];
  divergences: DivergenceAlert[];
}

export interface RegimePerformance {
  trades: number;
  wins: number;
  win_rate: number;
  net_pnl: number;
  avg_fee_drag: number;
  avg_edge: number;
  total_edge: number;
  total_fees: number;
}

export interface VolSurfaceStrike {
  ticker: string;
  strike: number;
  market_prob: number;
  theoretical_prob: number;
}

export interface VolSurfacePDF {
  strike_low: number;
  strike_high: number;
  strike_mid: number;
  density: number;
  probability: number;
}

export interface VolSurfaceMispricing {
  ticker: string;
  strike: number;
  market_prob: number;
  theoretical_prob: number;
  mispricing: number;
  direction: string;
}

export interface VolSurface {
  prefix: string;
  strikes: VolSurfaceStrike[];
  implied_pdf: VolSurfacePDF[];
  theoretical_mean: number;
  theoretical_std: number;
  mispricings: VolSurfaceMispricing[];
  n_strikes: number;
}

export interface AlphaAttribution {
  ir: number;
  cumulative_pnl: number;
  mean_return: number;
  std_return: number;
  trades: number;
  status: string;
}
