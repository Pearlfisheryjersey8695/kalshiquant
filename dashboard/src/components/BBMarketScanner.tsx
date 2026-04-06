"use client";

import { useDashboard } from "@/lib/store";
import { fmtPrice } from "@/lib/format";
import { useState, useMemo, useRef, useEffect } from "react";
import type { Signal } from "@/lib/types";

type SortKey = "ticker" | "price" | "edge" | "net_edge" | "volume" | "regime" | "expiry";
type SortDir = "asc" | "desc";

function formatExpiry(expTime: string | null | undefined): string {
  if (!expTime) return "—";
  const hours = (new Date(expTime).getTime() - Date.now()) / 3600000;
  if (hours < 0) return "EXP";
  if (hours < 1) return `${(hours * 60).toFixed(0)}m`;
  if (hours < 24) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

function formatVolume(vol: number): string {
  if (vol >= 1000000) return `${(vol / 1000000).toFixed(1)}M`;
  if (vol >= 1000) return `${(vol / 1000).toFixed(1)}K`;
  return String(vol);
}

export default function BBMarketScanner() {
  const { markets, signals, selectedTicker, setSelectedTicker } = useDashboard();
  const [sortKey, setSortKey] = useState<SortKey>("edge");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [filter, setFilter] = useState("");
  const prevPrices = useRef<Map<string, number>>(new Map());
  const [flashMap, setFlashMap] = useState<Map<string, "up" | "down">>(new Map());

  const signalMap = useMemo(() => {
    const map = new Map<string, Signal>();
    signals.forEach((s) => map.set(s.ticker, s));
    return map;
  }, [signals]);

  useEffect(() => {
    const flashes = new Map<string, "up" | "down">();
    markets.forEach((m) => {
      const prev = prevPrices.current.get(m.ticker);
      if (prev !== undefined && prev !== m.price) {
        flashes.set(m.ticker, m.price > prev ? "up" : "down");
      }
    });
    if (flashes.size > 0) {
      setFlashMap(flashes);
      const t = setTimeout(() => setFlashMap(new Map()), 400);
      prevPrices.current = new Map(markets.map((m) => [m.ticker, m.price]));
      return () => clearTimeout(t);
    }
    prevPrices.current = new Map(markets.map((m) => [m.ticker, m.price]));
  }, [markets]);

  const sorted = useMemo(() => {
    let filtered = markets;
    if (filter) {
      const q = filter.toLowerCase();
      filtered = markets.filter(
        (m) => m.ticker.toLowerCase().includes(q) || m.title.toLowerCase().includes(q) || m.category.toLowerCase().includes(q)
      );
    }
    return [...filtered].sort((a, b) => {
      let va: number, vb: number;
      if (sortKey === "ticker") {
        return sortDir === "asc" ? a.ticker.localeCompare(b.ticker) : b.ticker.localeCompare(a.ticker);
      } else if (sortKey === "edge") {
        va = Math.abs(signalMap.get(a.ticker)?.edge ?? 0);
        vb = Math.abs(signalMap.get(b.ticker)?.edge ?? 0);
      } else if (sortKey === "net_edge") {
        va = Math.abs(signalMap.get(a.ticker)?.net_edge ?? 0);
        vb = Math.abs(signalMap.get(b.ticker)?.net_edge ?? 0);
      } else if (sortKey === "regime") {
        const ra = signalMap.get(a.ticker)?.regime ?? "";
        const rb = signalMap.get(b.ticker)?.regime ?? "";
        return sortDir === "asc" ? ra.localeCompare(rb) : rb.localeCompare(ra);
      } else if (sortKey === "expiry") {
        const ea = a.expiration_time ? new Date(a.expiration_time).getTime() : Infinity;
        const eb = b.expiration_time ? new Date(b.expiration_time).getTime() : Infinity;
        va = ea;
        vb = eb;
        // For expiry, ascending = soonest first
      } else {
        va = a[sortKey] as number;
        vb = b[sortKey] as number;
      }
      return sortDir === "asc" ? va - vb : vb - va;
    });
  }, [markets, signalMap, filter, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  const arrow = (k: SortKey) => sortKey === k ? (sortDir === "asc" ? " \u25B2" : " \u25BC") : "";

  return (
    <div className="flex flex-col h-full">
      <div className="bb-panel-title flex items-center justify-between">
        <span>MARKET SCANNER</span>
        <span className="text-bb-dim">{sorted.length}</span>
      </div>
      <div className="px-1 py-[2px] border-b border-bb-border bg-bb-black flex items-center shrink-0">
        <span className="text-bb-orange text-[10px] mr-1">&gt;</span>
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="ENTER QUERY"
          className="bb-input flex-1 border-none text-[10px] bg-transparent p-0"
        />
      </div>
      <div className="bb-panel-body">
        <table className="bb-table">
          <thead>
            <tr>
              <th onClick={() => toggleSort("ticker")} style={{width: "28%"}}>TICKER{arrow("ticker")}</th>
              <th onClick={() => toggleSort("price")} style={{width: "10%", textAlign: "right"}}>LAST{arrow("price")}</th>
              <th style={{width: "10%", textAlign: "right"}}>BID/ASK</th>
              <th onClick={() => toggleSort("edge")} style={{width: "9%", textAlign: "right"}}>EDGE{arrow("edge")}</th>
              <th onClick={() => toggleSort("net_edge")} style={{width: "9%", textAlign: "right"}}>NET{arrow("net_edge")}</th>
              <th style={{width: "7%", textAlign: "center"}}>DIR</th>
              <th onClick={() => toggleSort("regime")} style={{width: "9%", textAlign: "center"}}>REGIME{arrow("regime")}</th>
              <th onClick={() => toggleSort("volume")} style={{width: "8%", textAlign: "right"}}>VOL{arrow("volume")}</th>
              <th onClick={() => toggleSort("expiry")} style={{width: "10%", textAlign: "right"}}>EXPIRY{arrow("expiry")}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((m) => {
              const sig = signalMap.get(m.ticker);
              const flash = flashMap.get(m.ticker);
              const isSelected = selectedTicker === m.ticker;
              const edge = sig?.edge ?? 0;
              const netEdge = sig?.net_edge ?? 0;
              const dir = sig?.direction;
              const regime = sig?.regime;

              return (
                <tr
                  key={m.ticker}
                  onClick={() => setSelectedTicker(m.ticker)}
                  className={`cursor-pointer ${isSelected ? "bb-selected" : ""} ${flash === "up" ? "flash-green" : flash === "down" ? "flash-red" : ""}`}
                >
                  <td className="truncate-ticker" title={m.title}>
                    {m.ticker.length > 22 ? m.ticker.slice(0, 22) + "\u2026" : m.ticker}
                  </td>
                  <td style={{textAlign: "right"}}>{fmtPrice(m.price)}</td>
                  <td style={{textAlign: "right"}}>{fmtPrice(m.yes_bid)}/{fmtPrice(m.yes_ask)}</td>
                  <td style={{textAlign: "right"}} className={edge > 0 ? "text-bb-green" : edge < 0 ? "text-bb-red" : ""}>
                    {edge !== 0 ? (edge > 0 ? "+" : "") + (edge * 100).toFixed(1) + "c" : "—"}
                  </td>
                  <td style={{textAlign: "right"}} className={netEdge > 0 ? "text-bb-green" : netEdge < 0 ? "text-bb-red" : "text-bb-dim"}>
                    {netEdge !== 0 ? (netEdge > 0 ? "+" : "") + (netEdge * 100).toFixed(1) + "c" : "—"}
                  </td>
                  <td style={{textAlign: "center"}} className={dir === "BUY_YES" ? "text-bb-green" : dir === "BUY_NO" ? "text-bb-red" : "text-bb-dim"}>
                    {dir === "BUY_YES" ? "YES" : dir === "BUY_NO" ? "NO" : "—"}
                  </td>
                  <td style={{textAlign: "center"}}>
                    <span className={`text-[11px] ${
                      regime === "CONVERGENCE" ? "text-bb-blue" :
                      regime === "TRENDING" ? "text-bb-green" :
                      regime === "MEAN_REVERTING" ? "text-bb-yellow" :
                      regime === "HIGH_VOLATILITY" ? "text-bb-red" :
                      "text-bb-dim"
                    }`}>{regime ? regime.slice(0, 5) : "—"}</span>
                  </td>
                  <td style={{textAlign: "right"}}>{formatVolume(m.volume)}</td>
                  <td style={{textAlign: "right"}} className="text-bb-dim">{formatExpiry(m.expiration_time)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
