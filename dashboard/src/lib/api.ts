const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

export const api = {
  getMarkets: () => fetchJSON<import("./types").Market[]>("/api/markets"),
  getMarket: (ticker: string) => fetchJSON<import("./types").Market>(`/api/markets/${encodeURIComponent(ticker)}`),
  getSignals: () => fetchJSON<import("./types").SignalsEnvelope>("/api/signals"),
  getSignal: (ticker: string) => fetchJSON<import("./types").Signal>(`/api/signals/${encodeURIComponent(ticker)}`),
  getPortfolio: () => fetchJSON<import("./types").Portfolio>("/api/portfolio"),
  getRisk: () => fetchJSON<import("./types").RiskData>("/api/risk"),
  getHistory: (ticker: string, limit = 200) =>
    fetchJSON<import("./types").HistoryPoint[]>(`/api/history/${encodeURIComponent(ticker)}?limit=${limit}`),
  getFeed: (limit = 50) => fetchJSON<import("./types").FeedEvent[]>(`/api/feed?limit=${limit}`),
  getHealth: () => fetchJSON<{ status: string; tracked_markets: number; feed_events: number }>("/api/health"),
  getArbitrage: () => fetchJSON<import("./types").ArbitrageOpportunity[]>("/api/arbitrage"),
  getCorrelations: () => fetchJSON<import("./types").CorrelationData>("/api/correlations"),
  getSentiment: (ticker: string) =>
    fetchJSON<{ consensus_prob: number; consensus_edge: number; source: string; ai_prob: number; ai_edge: number; reasoning: string; sentiment_edge: number }>(
      `/api/sentiment/${encodeURIComponent(ticker)}`
    ),
  getVolSurface: (event: string) =>
    fetchJSON<import("./types").VolSurface>(`/api/vol-surface/${encodeURIComponent(event)}`),
  // Execution engine
  getPositions: () => fetchJSON<import("./types").PositionsData>("/api/positions"),
  getPositionsHistory: () => fetchJSON<{ closed: import("./types").Position[] }>("/api/positions/history"),
  getExecutionStatus: () => fetchJSON<import("./types").ExecutionStatus>("/api/execution/status"),
  pauseExecution: async () => { const res = await fetch(`${API_BASE}/api/execution/pause`, { method: "POST" }); if (!res.ok) throw new Error(`API /api/execution/pause: ${res.status}`); return res.json(); },
  resumeExecution: async () => { const res = await fetch(`${API_BASE}/api/execution/resume`, { method: "POST" }); if (!res.ok) throw new Error(`API /api/execution/resume: ${res.status}`); return res.json(); },
  closePosition: async (ticker: string) => {
    const res = await fetch(`${API_BASE}/api/positions/${encodeURIComponent(ticker)}/close`, { method: "POST" });
    if (!res.ok) throw new Error(`API /api/positions/${ticker}/close: ${res.status}`);
    return res.json();
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  getBacktest: () => fetchJSON<Record<string, unknown>>("/api/backtest"),
  getAnalytics: () => fetchJSON<import("./types").AnalyticsData>("/api/analytics"),
  getMarketRisk: (ticker: string) => fetchJSON<import("./types").MarketRisk>(`/api/market-risk/${encodeURIComponent(ticker)}`),
  // Risk engine
  getPortfolioRisk: () => fetchJSON<import("./types").PortfolioRisk>("/api/risk-engine/portfolio"),
  getRiskCorrelations: () => fetchJSON<import("./types").CorrelationMatrix>("/api/risk-engine/correlations"),
  getPnlCalendar: () => fetchJSON<import("./types").PnlCalendar>("/api/risk-engine/pnl-calendar"),
  getEquityCurve: () => fetchJSON<import("./types").EquityCurve>("/api/risk-engine/equity-curve"),
  toggleKillSwitch: async (activate: boolean) => {
    const res = await fetch(`${API_BASE}/api/risk-engine/kill-switch?activate=${activate}`, { method: "POST" });
    if (!res.ok) throw new Error(`API /api/risk-engine/kill-switch: ${res.status}`);
    return res.json();
  },
  // Strategies
  getStrategies: () => fetchJSON<import("./types").Strategy[]>("/api/strategies"),
  getStrategy: (id: string) => fetchJSON<import("./types").Strategy>(`/api/strategies/${id}`),
  createStrategy: async () => { const res = await fetch(`${API_BASE}/api/strategies`, { method: "POST" }); if (!res.ok) throw new Error(`API /api/strategies: ${res.status}`); return res.json(); },
  updateStrategy: async (id: string, data: Partial<import("./types").Strategy>) => {
    const res = await fetch(`${API_BASE}/api/strategies/${id}/update`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    if (!res.ok) throw new Error(`API /api/strategies/${id}/update: ${res.status}`);
    return res.json();
  },
  deleteStrategy: async (id: string) => { const res = await fetch(`${API_BASE}/api/strategies/${id}`, { method: "DELETE" }); if (!res.ok) throw new Error(`API /api/strategies/${id}: ${res.status}`); return res.json(); },
  // Kalshi REST API (direct)
  getKalshiMarkets: (limit = 100, status = "open", cursor?: string) =>
    fetchJSON<{ markets: import("./types").KalshiMarket[]; cursor: string | null }>(
      `/api/kalshi/markets?limit=${limit}&status=${status}${cursor ? `&cursor=${cursor}` : ""}`
    ),
  getKalshiMarket: (ticker: string) =>
    fetchJSON<{ market: import("./types").KalshiMarket }>(`/api/kalshi/markets/${encodeURIComponent(ticker)}`),
  getKalshiOrderbook: (ticker: string, depth = 20) =>
    fetchJSON<import("./types").KalshiOrderbook>(`/api/kalshi/orderbook/${encodeURIComponent(ticker)}?depth=${depth}`),
  getKalshiTrades: (ticker: string, limit = 100) =>
    fetchJSON<{ trades: import("./types").KalshiTrade[]; cursor: string | null }>(`/api/kalshi/trades/${encodeURIComponent(ticker)}?limit=${limit}`),
  searchKalshiMarkets: (q: string) =>
    fetchJSON<{ results: import("./types").KalshiMarket[]; total: number; query: string }>(`/api/kalshi/search?q=${encodeURIComponent(q)}`),
  bootstrapMarkets: async () => {
    const res = await fetch(`${API_BASE}/api/kalshi/bootstrap`, { method: "POST" });
    if (!res.ok) throw new Error(`API /api/kalshi/bootstrap: ${res.status}`);
    return res.json();
  },
  // Pipeline status
  getPipelineStatus: () => fetchJSON<Record<string, unknown>>("/api/pipeline/status"),
  refreshPipeline: async (mode = "light") => {
    const res = await fetch(`${API_BASE}/api/pipeline/refresh?mode=${mode}`, { method: "POST" });
    if (!res.ok) throw new Error(`Pipeline refresh failed: ${res.status}`);
    return res.json();
  },
  // Fund manager endpoints
  getMorningBrief: () => fetchJSON<import("./types").MorningBrief>("/api/morning-brief"),
  getJournal: (params?: Record<string, string>) => {
    const qs = params ? "?" + new URLSearchParams(params).toString() : "";
    return fetchJSON<import("./types").JournalEntry[]>(`/api/journal${qs}`);
  },
  getJournalSummary: () => fetchJSON<import("./types").JournalSummary>("/api/journal/summary"),
  addJournalNote: async (ticker: string, entryTime: string, note: string) => {
    const res = await fetch(`${API_BASE}/api/journal/${encodeURIComponent(ticker)}/${encodeURIComponent(entryTime)}/notes`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    });
    if (!res.ok) throw new Error(`API journal note: ${res.status}`);
    return res.json();
  },
  getAlerts: (limit = 50, level?: string) =>
    fetchJSON<import("./types").Alert[]>(`/api/alerts?limit=${limit}${level ? `&level=${level}` : ""}`),
  getAlertCount: () => fetchJSON<Record<string, number>>("/api/alerts/count"),
  getPositionHeatmap: () => fetchJSON<import("./types").PositionHeatmap>("/api/risk-engine/position-heatmap"),
  // Endpoints that exist but weren't wired
  getBenchmarks: () => fetchJSON<Record<string, { ticker: string; price: number; title: string; volume: number }>>("/api/benchmarks"),
  getSimPortfolio: () => fetchJSON<Record<string, unknown>>("/api/sim-portfolio"),
  getRegimes: () => fetchJSON<{ ticker: string; regime: string; regime_probs: Record<string, number> }[]>("/api/regimes"),
  getOrderbookHealth: () => fetchJSON<Record<string, unknown>>("/api/orderbook-health"),
  getTCA: () => fetchJSON<Record<string, unknown>>("/api/risk-engine/tca"),
  getBulkMarketRisk: () => fetchJSON<Record<string, Record<string, number>>>("/api/market-risk/bulk"),
  // QuantBrain
  getBrainStatus: () => fetchJSON<Record<string, unknown>>("/api/brain/status"),
  getBrainLessons: () => fetchJSON<Record<string, unknown>[]>("/api/brain/lessons"),
  getBrainTheses: () => fetchJSON<Record<string, unknown>>("/api/brain/theses"),
  getBrainPolicy: () => fetchJSON<Record<string, unknown>>("/api/brain/rl-policy"),
  getBrainDecisions: (limit = 20) => fetchJSON<Record<string, unknown>[]>(`/api/brain/decisions?limit=${limit}`),
  // External data feeds
  getExternalData: () => fetchJSON<Record<string, unknown>>("/api/external-data"),
  getExternalProbability: (ticker: string, hours = 24) =>
    fetchJSON<Record<string, unknown>>(`/api/external-data/probability/${encodeURIComponent(ticker)}?hours=${hours}`),
};
