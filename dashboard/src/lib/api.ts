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
};
