"use client";

import { useDashboard } from "@/lib/store";
import { fmtPrice, fmtEdgeCents, fmtVolume } from "@/lib/format";
import { api } from "@/lib/api";
import PanelHeader from "./PanelHeader";
import { useState, useMemo, useRef, useEffect } from "react";
import type { Signal } from "@/lib/types";

type SortKey = "ticker" | "price" | "edge" | "volume" | "tradability_score";
type SortDir = "asc" | "desc";

export default function MarketScanner() {
  const { markets, signals, selectedTicker, setSelectedTicker } = useDashboard();
  const [sortKey, setSortKey] = useState<SortKey>("edge");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [filter, setFilter] = useState("");
  const prevPrices = useRef<Map<string, number>>(new Map());
  const [flashMap, setFlashMap] = useState<Map<string, "green" | "red">>(new Map());
  const [arbTickers, setArbTickers] = useState<Set<string>>(new Set());

  // Fetch arbitrage opportunities on mount
  useEffect(() => {
    api.getArbitrage()
      .then((opps) => {
        const tickers = new Set<string>();
        opps.forEach((o) => {
          tickers.add(o.buy_ticker);
          tickers.add(o.sell_ticker);
        });
        setArbTickers(tickers);
      })
      .catch(() => {}); // silently fail if endpoint not available
  }, []);

  // Build signal lookup
  const signalMap = useMemo(() => {
    const map = new Map<string, Signal>();
    signals.forEach((s) => map.set(s.ticker, s));
    return map;
  }, [signals]);

  // Detect price changes for flash
  useEffect(() => {
    const newFlashes = new Map<string, "green" | "red">();
    markets.forEach((m) => {
      const prev = prevPrices.current.get(m.ticker);
      if (prev !== undefined && prev !== m.price) {
        newFlashes.set(m.ticker, m.price > prev ? "green" : "red");
      }
    });
    if (newFlashes.size > 0) {
      setFlashMap(newFlashes);
      const timer = setTimeout(() => setFlashMap(new Map()), 600);
      return () => clearTimeout(timer);
    }
    prevPrices.current = new Map(markets.map((m) => [m.ticker, m.price]));
  }, [markets]);

  // Filter + sort
  const sorted = useMemo(() => {
    let filtered = markets;
    if (filter) {
      const q = filter.toLowerCase();
      filtered = markets.filter(
        (m) =>
          m.ticker.toLowerCase().includes(q) ||
          m.title.toLowerCase().includes(q) ||
          m.category.toLowerCase().includes(q)
      );
    }

    return [...filtered].sort((a, b) => {
      let va: number, vb: number;
      if (sortKey === "edge") {
        va = Math.abs(signalMap.get(a.ticker)?.edge ?? 0);
        vb = Math.abs(signalMap.get(b.ticker)?.edge ?? 0);
      } else if (sortKey === "ticker") {
        return sortDir === "asc"
          ? a.ticker.localeCompare(b.ticker)
          : b.ticker.localeCompare(a.ticker);
      } else {
        va = a[sortKey];
        vb = b[sortKey];
      }
      return sortDir === "asc" ? va - vb : vb - va;
    });
  }, [markets, signalMap, filter, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const SortIcon = ({ k }: { k: SortKey }) =>
    sortKey === k ? (
      <span className="ml-0.5 text-blue">{sortDir === "asc" ? "\u25B2" : "\u25BC"}</span>
    ) : null;

  return (
    <div className="flex flex-col h-full">
      <PanelHeader
        title="Market Scanner"
        subtitle={`${sorted.length} markets`}
        right={
          <input
            type="text"
            placeholder="Search..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="bg-bg border border-border rounded px-2 py-0.5 text-[10px] text-text-primary w-28 focus:outline-none focus:border-blue"
          />
        }
      />
      <div className="flex-1 overflow-y-auto min-h-0">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-surface z-10">
            <tr className="text-text-secondary border-b border-border">
              <th className="text-left px-2 py-1 cursor-pointer select-none" onClick={() => toggleSort("ticker")}>
                Ticker<SortIcon k="ticker" />
              </th>
              <th className="text-right px-2 py-1 cursor-pointer select-none" onClick={() => toggleSort("price")}>
                Price<SortIcon k="price" />
              </th>
              <th className="text-right px-2 py-1 cursor-pointer select-none" onClick={() => toggleSort("edge")}>
                Edge<SortIcon k="edge" />
              </th>
              <th className="text-center px-2 py-1">Dir</th>
              <th className="text-right px-2 py-1 cursor-pointer select-none" onClick={() => toggleSort("volume")}>
                Vol<SortIcon k="volume" />
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((m, idx) => {
              const sig = signalMap.get(m.ticker);
              const flash = flashMap.get(m.ticker);
              const isSelected = selectedTicker === m.ticker;
              const dirColor =
                sig?.direction === "BUY_YES"
                  ? "text-green"
                  : sig?.direction === "BUY_NO"
                  ? "text-red"
                  : "text-text-secondary";
              const rowBg = idx % 2 === 0 ? "" : "bg-bg/30";

              return (
                <tr
                  key={m.ticker}
                  onClick={() => setSelectedTicker(m.ticker)}
                  className={`cursor-pointer border-b border-border/30 hover:bg-border/40 transition-colors ${rowBg} ${
                    isSelected ? "!bg-blue/10 border-l-2 !border-l-blue" : ""
                  } ${flash ? (flash === "green" ? "flash-green" : "flash-red") : ""}`}
                >
                  <td className="px-2 py-1.5 max-w-[160px]" title={m.title}>
                    <div className="font-mono text-[10px] truncate flex items-center gap-1">
                      {m.ticker.length > 24 ? m.ticker.slice(0, 24) + "\u2026" : m.ticker}
                      {arbTickers.has(m.ticker) && (
                        <span className="px-1 py-0.5 rounded text-[8px] font-bold bg-red/20 text-red shrink-0">ARB</span>
                      )}
                    </div>
                    <div className="text-[9px] text-text-secondary truncate">{m.category}</div>
                  </td>
                  <td className="text-right px-2 py-1.5 font-mono font-semibold">
                    {fmtPrice(m.price)}
                  </td>
                  <td
                    className={`text-right px-2 py-1.5 font-mono font-semibold ${
                      sig && sig.edge > 0 ? "text-green" : sig && sig.edge < 0 ? "text-red" : ""
                    }`}
                  >
                    {sig ? fmtEdgeCents(sig.edge) : "\u2014"}
                  </td>
                  <td className={`text-center px-2 py-1.5 font-mono text-[10px] font-bold ${dirColor}`}>
                    {sig?.direction === "BUY_YES" ? "BUY" : sig?.direction === "BUY_NO" ? "SELL" : "\u2014"}
                  </td>
                  <td className="text-right px-2 py-1.5 font-mono text-text-secondary">
                    {fmtVolume(m.volume)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
