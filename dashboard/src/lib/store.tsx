"use client";

import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from "react";
import { api } from "./api";
import { useWebSocket } from "./useWebSocket";
import type { Market, Signal, SignalsEnvelope, FeedEvent, Portfolio, RiskData } from "./types";

export interface SimTrade {
  id: number;
  ticker: string;
  title: string;
  direction: "BUY_YES" | "BUY_NO";
  contracts: number;
  entryPrice: number;
  sizeDollars: number;
  ts: string;
}

interface DashboardState {
  markets: Market[];
  signals: Signal[];
  signalsMeta: { generated_at: string; portfolio_value: number; total_signals: number };
  feedEvents: FeedEvent[];
  portfolio: Portfolio | null;
  risk: RiskData | null;
  selectedTicker: string | null;
  setSelectedTicker: (ticker: string | null) => void;
  wsConnected: boolean;
  // Simulated portfolio
  simCash: number;
  simTrades: SimTrade[];
  executeTrade: (signal: Signal) => void;
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
  const [signalsMeta, setSignalsMeta] = useState({ generated_at: "", portfolio_value: 10000, total_signals: 0 });
  const [feedEvents, setFeedEvents] = useState<FeedEvent[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [risk, setRisk] = useState<RiskData | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  // Simulated portfolio
  const [simCash, setSimCash] = useState(10000);
  const [simTrades, setSimTrades] = useState<SimTrade[]>([]);
  const tradeIdRef = useRef(0);

  const prevPricesRef = useRef<Map<string, number>>(new Map());

  // Initial data load
  useEffect(() => {
    api.getMarkets().then(setMarkets).catch(() => {});
    api.getSignals().then((env) => {
      setSignals(env.signals);
      setSignalsMeta({ generated_at: env.generated_at, portfolio_value: env.portfolio_value, total_signals: env.total_signals });
    }).catch(() => {});
    api.getFeed(100).then(setFeedEvents).catch(() => {});
    api.getPortfolio().then(setPortfolio).catch(() => {});
    api.getRisk().then(setRisk).catch(() => {});
  }, []);

  // WS: prices
  const { connected: priceConnected } = useWebSocket<{ type: string; data: Market[] }>({
    path: "/ws/prices",
    onMessage: useCallback((msg: { type: string; data: Market[] }) => {
      if (msg.type === "prices" && Array.isArray(msg.data)) {
        setMarkets((prev) => {
          const newPrices = new Map<string, number>();
          msg.data.forEach((m) => newPrices.set(m.ticker, m.price));
          prevPricesRef.current = new Map(prev.map((m) => [m.ticker, m.price]));
          return msg.data;
        });
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

  // Execute simulated trade — gated on cash deduction + 60% deployment limit
  const executeTrade = useCallback((signal: Signal) => {
    const sizeDollars = signal.risk.size_dollars;
    let tradeCreated = false;
    setSimCash((prev) => {
      if (prev < sizeDollars) return prev; // insufficient funds
      // Enforce 60% max deployment (40% cash reserve)
      if (prev - sizeDollars < 10000 * 0.40) return prev;
      tradeCreated = true;
      return prev - sizeDollars;
    });
    // Only create trade if cash was actually deducted
    if (!tradeCreated) return;
    tradeIdRef.current += 1;
    const trade: SimTrade = {
      id: tradeIdRef.current,
      ticker: signal.ticker,
      title: signal.title,
      direction: signal.direction as "BUY_YES" | "BUY_NO",
      contracts: signal.recommended_contracts,
      entryPrice: signal.current_price,
      sizeDollars,
      ts: new Date().toISOString(),
    };
    setSimTrades((prev) => [trade, ...prev]);
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
        simCash,
        simTrades,
        executeTrade,
      }}
    >
      {children}
    </DashboardContext.Provider>
  );
}
