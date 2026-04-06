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
  strategy_params?: {
    stop_loss_pct: number;
    take_profit_ratio: number;
    kelly_fraction: number;
    max_hold_hours: number;
  };
  minutes_to_release?: number;
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
  signal_source?: string;
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

// ── Execution Engine Types ──────────────────────────────────────────────────

export interface Position {
  ticker: string;
  direction: string;
  entry_price: number;
  entry_time: string;
  contracts: number;
  remaining_contracts: number;
  entry_cost: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  fees_paid: number;
  signal_persistence_at_entry: number;
  regime_at_entry: string;
  edge_at_entry: number;
  meta_quality_at_entry: number;
  kelly_fraction_at_entry: number;
  status: "OPEN" | "PARTIAL" | "CLOSED";
  exit_reason: string;
  exit_price: number;
  exit_time: string;
  title: string;
  category: string;
  strategy_at_entry: string;
  hold_time_minutes: number;
  pnl_pct: number;
}

export interface PositionSummary {
  open_positions: number;
  total_deployed: number;
  total_unrealized: number;
  total_realized: number;
  portfolio_heat: number;
  bankroll: number;
  today_pnl: number;
}

export interface ExecutionStatus {
  running: boolean;
  paused: boolean;
  portfolio_heat: number;
  open_positions: number;
  total_realized: number;
  total_unrealized: number;
}

export interface PositionsData {
  open: Position[];
  summary: PositionSummary;
}

// ── Strategy Types ────────────────────────────────────────────────────────

export interface StrategyParam {
  name: string;
  value: number;
  min: number;
  max: number;
}

export interface StrategySignalsConfig {
  fair_value: boolean;
  regime_classifier: boolean;
  sentiment_score: boolean;
  momentum: boolean;
  mean_reversion: boolean;
  volume_signal: boolean;
  weights: Record<string, number>;
}

export interface StrategyRiskLimits {
  max_position_size: number;
  max_daily_loss: number;
  max_open_positions: number;
  kelly_fraction: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  min_edge: number;
  min_confidence: number;
  min_tradability: number;
}

export interface Strategy {
  id: string;
  name: string;
  type: string;
  status: string;
  description: string;
  markets: string[];
  parameters: StrategyParam[];
  signals_config: StrategySignalsConfig;
  risk_limits: StrategyRiskLimits;
  pnl: number;
  trades_today: number;
  win_rate: number;
  created_at: string;
  updated_at: string;
}

// ── Risk Engine Types ─────────────────────────────────────────────────────

export interface PortfolioRisk {
  total_capital: number;
  deployed: number;
  deployed_pct: number;
  cash: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_pnl: number;
  var95: number;
  var99: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  win_rate: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  best_day: number;
  worst_day: number;
  largest_win: number;
  largest_loss: number;
  open_positions: number;
  heat: number;
  exposure_by_category: { category: string; amount: number; pct: number; over_limit: boolean }[];
  kill_switch: boolean;
}

export interface CorrelationMatrix {
  tickers: string[];
  indices: string[];
  matrix: Record<string, Record<string, number>>;
}

export interface PnlCalendarDay {
  date: string;
  pnl: number;
  has_data: boolean;
}

export interface PnlCalendar {
  daily: Record<string, number>;
  weeks: PnlCalendarDay[][];
}

export interface EquityCurvePoint {
  ts: string;
  equity: number;
  pnl: number;
}

export interface EquityCurve {
  points: EquityCurvePoint[];
  drawdown: { ts: string; drawdown_pct: number }[];
}

// ── Per-Market Risk ───────────────────────────────────────────────────────

export interface MarketRisk {
  ticker: string;
  var95: number;
  var99: number;
  max_loss_1ct: number;
  prob_win: number;
  prob_loss: number;
  expected_value: number;
  kelly_pct: number;
  half_kelly_pct: number;
  sharpe_7d: number;
  sortino_7d: number;
  max_drawdown: number;
  corr_sp500: number;
  corr_btc: number;
  liquidity_risk: string;
}

// ── Analytics Types ───────────────────────────────────────────────────────

export interface PnLPoint {
  ts: string;
  cumulative_pnl: number;
  unrealized: number;
}

export interface DrawdownData {
  max_drawdown_pct: number;
  max_drawdown_dollars: number;
  current_drawdown_pct: number;
  current_drawdown_dollars: number;
  drawdown_duration_minutes: number;
  drawdown_curve: { ts: string; drawdown_pct: number }[];
}

export interface AttributionEntry {
  pnl: number;
  trades: number;
  win_rate?: number;
}

export interface WinLossData {
  total_wins: number;
  total_losses: number;
  avg_win: number;
  avg_loss: number;
  largest_win: number;
  largest_loss: number;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  current_streak: number;
  current_streak_type: "win" | "loss" | "none";
  win_distribution: number[];
  loss_distribution: number[];
}

export interface SectorHeatmapEntry {
  category: string;
  pnl: number;
  trades: number;
  win_rate: number;
  avg_hold_minutes: number;
}

export interface AnalyticsData {
  pnl_curve: PnLPoint[];
  drawdown: DrawdownData;
  attribution: {
    by_category: Record<string, AttributionEntry>;
    by_regime: Record<string, AttributionEntry>;
    by_strategy: Record<string, AttributionEntry>;
    by_hour: Record<string, { pnl: number; trades: number }>;
  };
  sector_heatmap: SectorHeatmapEntry[];
  win_loss: WinLossData;
}

// ── Kalshi REST API Types ────────────────────────────────────────────────
export interface KalshiMarket {
  ticker: string;
  event_ticker: string;
  series_ticker: string;
  title: string;
  subtitle: string;
  status: string;
  category: string;
  yes_bid: number;
  yes_ask: number;
  no_bid: number;
  no_ask: number;
  last_price: number;
  volume: number;
  volume_24h: number;
  open_interest: number;
  close_time: string;
  expiration_time: string;
  result: string;
  [key: string]: unknown;
}

export interface KalshiOrderLevel {
  price: number;
  quantity: number;
}

export interface KalshiOrderbook {
  orderbook: {
    yes: KalshiOrderLevel[];
    no: KalshiOrderLevel[];
  };
  ticker: string;
}

export interface KalshiTrade {
  ticker: string;
  trade_id: string;
  count: number;
  yes_price: number;
  no_price: number;
  taker_side: string;
  created_time: string;
}

// ── Fund Manager Types ───────────────────────────────────────────────

export interface NewsItem {
  title: string;
  source: string;
  url: string;
  snippet: string;
  category: string;
  relevance: number;
  published: string;
}

export interface MarketContext {
  category: string;
  market_count: number;
  avg_volume: number;
  high_conviction_count: number;
  summary: string;
}

export interface MorningBrief {
  date: string;
  time: string;
  overnight_pnl: number;
  overnight_trades: number;
  biggest_winner: { ticker: string; pnl: number } | null;
  biggest_loser: { ticker: string; pnl: number } | null;
  positions_at_risk: PositionAtRisk[];
  top_opportunities: Opportunity[];
  recent_alerts: FeedEvent[];
  expiring_today: ExpiringMarket[];
  portfolio: PositionSummary;
  open_positions_count: number;
  news?: NewsItem[];
  market_context?: MarketContext[];
}

export interface PositionAtRisk {
  ticker: string;
  direction: string;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  edge_at_entry: number;
  current_edge: number;
  hours_to_expiry: number;
  risk_flags: string[];
  hold_time_minutes: number;
}

export interface Opportunity {
  ticker: string;
  title: string;
  direction: string;
  edge: number;
  net_edge: number;
  confidence: number;
  regime: string;
  recommended_contracts: number;
}

export interface ExpiringMarket {
  ticker: string;
  title: string;
  price: number;
  expiration_time: string;
  has_position: boolean;
}

export interface JournalEntry {
  ticker: string;
  direction: string;
  entry_price: number;
  exit_price: number;
  entry_time: string;
  exit_time: string;
  contracts: number;
  remaining_contracts: number;
  entry_cost: number;
  realized_pnl: number;
  fees_paid: number;
  edge_at_entry: number;
  net_edge_at_entry: number;
  regime_at_entry: string;
  regime_at_exit: string;
  strategy_at_entry: string;
  exit_reason: string;
  confidence_at_entry: number;
  fair_value_at_entry: number;
  meta_quality_at_entry: number;
  kelly_fraction_at_entry: number;
  hold_time_minutes: number;
  pnl_pct: number;
  regime_changed: boolean;
  journal_notes: string;
  title: string;
  category: string;
  status: string;
}

export interface JournalSummary {
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl: number;
  avg_win: number;
  avg_loss: number;
  best_trade: number;
  worst_trade: number;
  avg_hold_minutes: number;
  exit_reasons: Record<string, number>;
  by_strategy: Record<string, { trades: number; pnl: number; wins: number; win_rate: number }>;
  by_regime: Record<string, { trades: number; pnl: number; wins: number; win_rate: number }>;
}

export interface Alert {
  seq: number;
  ts: string;
  level: "INFO" | "WARN" | "CRITICAL";
  ticker: string;
  message: string;
}

export interface PositionRiskCard {
  ticker: string;
  title: string;
  direction: string;
  category: string;
  deployed: number;
  pct_of_book: number;
  unrealized_pnl: number;
  edge_at_entry: number;
  current_edge: number;
  edge_decay: number;
  hours_to_expiry: number;
  hold_time_minutes: number;
  risk_score: number;
  regime: string;
}

export interface PositionHeatmap {
  positions: PositionRiskCard[];
  total_deployed: number;
  category_concentration: Record<string, { deployed: number; count: number; pct_of_book: number; over_limit: boolean; tickers: string[] }>;
  expiry_buckets: Record<string, { count: number; tickers: string[] }>;
}

// ── QuantBrain Types ─────────────────────────────────────────────────────

export interface BrainDecision {
  cycle: number;
  ts: string;
  elapsed_ms: number;
  opportunities_evaluated: number;
  theses_generated: number;
  entries_executed: number;
  skipped: number;
  open_positions: number;
  heat: number;
  rl_exploration: number;
  rl_total_experiences: number;
}

export interface BrainLesson {
  ticker: string;
  direction: string;
  pnl: number;
  lesson: string;
  regime: string;
  conviction: number;
  edge_realized: number;
  thesis_correct: boolean;
}

export interface BrainStatus {
  active: boolean;
  cycle_count: number;
  recent_decisions: BrainDecision[];
  pending_theses: Record<string, Record<string, unknown>>;
  rl_stats: {
    total_experiences: number;
    q_states: number;
    exploration_rate: number;
    n_updates: number;
  };
  rl_performance: Record<string, { q_trade: number; count: number }>;
  lessons_learned: BrainLesson[];
}
