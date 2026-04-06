"use client";

import React, { createContext, useContext, useState, useCallback, useEffect, useMemo } from "react";
import { api } from "./api";
import { useWebSocket } from "./useWebSocket";
import type { Market, Signal, SignalsEnvelope, FeedEvent, Portfolio, RiskData, Position, PositionSummary, ExecutionStatus } from "./types";

// Real-time P&L computed client-side from latest WS prices
interface LivePnL {
  total: number;         // unrealized + realized
  unrealized: number;    // sum of all open positions
  realized: number;      // from server
  byPosition: { ticker: string; direction: string; pnl: number; contracts: number; entryPrice: number; currentPrice: number }[];
  lastUpdate: number;    // timestamp ms
  isStale: boolean;      // true if > 30s since last price update
}

interface DashboardState {
  markets: Market[];
  signals: Signal[];
  signalsMeta: { generated_at: string; portfolio_value: number; total_signals: number; signal_source?: string };
  feedEvents: FeedEvent[];
  portfolio: Portfolio | null;
  risk: RiskData | null;
  selectedTicker: string | null;
  setSelectedTicker: (ticker: string | null) => void;
  wsConnected: boolean;
  loading: boolean;
  connectionError: string | null;
  // Execution engine
  openPositions: Position[];
  closedPositions: Position[];
  positionSummary: PositionSummary | null;
  executionStatus: ExecutionStatus | null;
  toggleExecution: () => void;
  closePosition: (ticker: string) => void;
  // Real-time P&L (computed client-side)
  livePnL: LivePnL;
}

const DashboardContext = createContext<DashboardState | null>(null);

export function useDashboard() {
  const ctx = useContext(DashboardContext);
  if (!ctx) throw new Error("useDashboard must be used within DashboardProvider");
  return ctx;
}

export function DashboardProvider({ children }: { children: React.ReactNode }) {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [signalsMeta, setSignalsMeta] = useState<{ generated_at: string; portfolio_value: number; total_signals: number; signal_source?: string }>({ generated_at: "", portfolio_value: 10000, total_signals: 0 });
  const [feedEvents, setFeedEvents] = useState<FeedEvent[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [risk, setRisk] = useState<RiskData | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  // Execution engine state
  const [openPositions, setOpenPositions] = useState<Position[]>([]);
  const [closedPositions, setClosedPositions] = useState<Position[]>([]);
  const [positionSummary, setPositionSummary] = useState<PositionSummary | null>(null);
  const [executionStatus, setExecutionStatus] = useState<ExecutionStatus | null>(null);

  // Initial data load
  useEffect(() => {
    let loadedAny = false;
    const markLoaded = () => { if (!loadedAny) { loadedAny = true; setLoading(false); } };

    api.getMarkets().then((d) => { setMarkets(d); markLoaded(); }).catch((e) => console.error("Failed to load markets:", e));
    api.getSignals().then((env) => {
      setSignals(env.signals);
      setSignalsMeta({ generated_at: env.generated_at, portfolio_value: env.portfolio_value, total_signals: env.total_signals, signal_source: env.signal_source });
      markLoaded();
    }).catch((e) => console.error("Failed to load signals:", e));
    api.getFeed(100).then(setFeedEvents).catch((e) => console.error("Failed to load feed:", e));
    api.getPortfolio().then(setPortfolio).catch((e) => console.error("Failed to load portfolio:", e));
    api.getRisk().then(setRisk).catch((e) => console.error("Failed to load risk:", e));
    // Execution engine
    api.getPositions().then((d) => {
      setOpenPositions(d.open);
      setPositionSummary(d.summary);
    }).catch((e) => console.error("Failed to load positions:", e));
    api.getPositionsHistory().then((d) => setClosedPositions(d.closed)).catch((e) => console.error("Failed to load position history:", e));
    api.getExecutionStatus().then(setExecutionStatus).catch((e) => console.error("Failed to load execution status:", e));

    // Timeout: if nothing loaded after 8 seconds, show error
    const timeout = setTimeout(() => {
      if (!loadedAny) {
        setConnectionError("Cannot connect to server at localhost:8000");
        setLoading(false);
      }
    }, 8000);
    return () => clearTimeout(timeout);
  }, []);

  // WS: prices
  const { connected: priceConnected } = useWebSocket<{ type: string; data: Market[] }>({
    path: "/ws/prices",
    onMessage: useCallback((msg: { type: string; data: Market[] }) => {
      if (msg.type === "prices" && Array.isArray(msg.data)) {
        setMarkets(msg.data);
      }
    }, []),
  });

  // WS: signals
  useWebSocket<{ type: string; data: SignalsEnvelope }>({
    path: "/ws/signals",
    onMessage: useCallback((msg: { type: string; data: SignalsEnvelope }) => {
      if (msg.type === "signals" && msg.data?.signals) {
        setSignals(msg.data.signals);
        setSignalsMeta({
          generated_at: msg.data.generated_at,
          portfolio_value: msg.data.portfolio_value,
          total_signals: msg.data.total_signals,
          signal_source: msg.data.signal_source,
        });
      }
    }, []),
  });

  // WS: feed
  useWebSocket<{ type: string; data: FeedEvent | FeedEvent[] }>({
    path: "/ws/feed",
    onMessage: useCallback((msg: { type: string; data: FeedEvent | FeedEvent[] }) => {
      if (msg.type === "feed") {
        if (Array.isArray(msg.data)) {
          setFeedEvents(msg.data);
        } else {
          setFeedEvents((prev) => [msg.data as FeedEvent, ...prev].slice(0, 200));
        }
      }
    }, []),
  });

  // WS: positions (real-time P&L updates every 5s)
  useWebSocket<{ type: string; data: { open: Position[]; summary: PositionSummary } }>({
    path: "/ws/positions",
    onMessage: useCallback((msg: { type: string; data: { open: Position[]; summary: PositionSummary } }) => {
      if (msg.type === "positions" && msg.data) {
        setOpenPositions(msg.data.open || []);
        setPositionSummary(msg.data.summary || null);
      }
    }, []),
  });

  // ── REAL-TIME CLIENT-SIDE P&L ─────────────────────────────────────────
  // Recomputes INSTANTLY every time a WS price tick arrives.
  // No waiting for server — the browser IS the pricing engine.
  const livePnL = useMemo<LivePnL>(() => {
    const priceMap = new Map<string, number>();
    for (const m of markets) {
      if (m.price > 0) priceMap.set(m.ticker, m.price);
    }

    const byPosition: LivePnL["byPosition"] = [];
    let unrealized = 0;

    for (const pos of openPositions) {
      const currentPrice = priceMap.get(pos.ticker) || pos.current_price || 0;
      let pnl = 0;

      if (pos.direction === "BUY_YES") {
        pnl = (currentPrice - pos.entry_price) * (pos.remaining_contracts || pos.contracts);
      } else {
        pnl = (pos.entry_price - currentPrice) * (pos.remaining_contracts || pos.contracts);
      }

      unrealized += pnl;
      byPosition.push({
        ticker: pos.ticker,
        direction: pos.direction,
        pnl: Math.round(pnl * 100) / 100,
        contracts: pos.remaining_contracts || pos.contracts,
        entryPrice: pos.entry_price,
        currentPrice,
      });
    }

    const realized = positionSummary?.total_realized ?? 0;
    const now = Date.now();

    // Check staleness: find the most recent market update
    let latestUpdate = 0;
    for (const m of markets) {
      if (m.last_update_ts) {
        const ts = new Date(m.last_update_ts).getTime();
        if (ts > latestUpdate) latestUpdate = ts;
      }
    }

    return {
      total: Math.round((unrealized + realized) * 100) / 100,
      unrealized: Math.round(unrealized * 100) / 100,
      realized: Math.round(realized * 100) / 100,
      byPosition: byPosition.sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl)),
      lastUpdate: latestUpdate || now,
      isStale: latestUpdate > 0 && (now - latestUpdate) > 30000,
    };
  }, [markets, openPositions, positionSummary]);

  // Toggle execution engine pause/resume
  const toggleExecution = useCallback(async () => {
    const current = executionStatus;
    if (!current) return;
    const newPaused = !current.paused;
    try {
      if (newPaused) {
        await api.pauseExecution();
      } else {
        await api.resumeExecution();
      }
      // Only update UI AFTER server confirms
      const refreshed = await api.getExecutionStatus();
      setExecutionStatus(refreshed);
    } catch (e) {
      console.error("Failed to toggle execution:", e);
      // Re-fetch actual state on failure
      api.getExecutionStatus().then(setExecutionStatus).catch(() => {});
    }
  }, [executionStatus]);

  // Manual close position
  const closePosition = useCallback((ticker: string) => {
    api.closePosition(ticker).then(() => {
      // Refresh positions
      api.getPositions().then((d) => {
        setOpenPositions(d.open);
        setPositionSummary(d.summary);
      }).catch((e) => console.error("Failed to refresh positions:", e));
      api.getPositionsHistory().then((d) => setClosedPositions(d.closed)).catch((e) => console.error("Failed to refresh position history:", e));
    }).catch((e) => console.error("Failed to close position:", e));
  }, []);

  return (
    <DashboardContext.Provider
      value={{
        markets,
        signals,
        signalsMeta,
        feedEvents,
        portfolio,
        risk,
        selectedTicker,
        setSelectedTicker,
        wsConnected: priceConnected,
        loading,
        connectionError,
        openPositions,
        closedPositions,
        positionSummary,
        executionStatus,
        toggleExecution,
        closePosition,
        livePnL,
      }}
    >
      {children}
    </DashboardContext.Provider>
  );
}
