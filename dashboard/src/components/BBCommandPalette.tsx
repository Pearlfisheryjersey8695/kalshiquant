"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";
import { useState, useMemo, useRef, useEffect } from "react";
import type { KalshiMarket } from "@/lib/types";

interface Props {
  onClose: () => void;
}

export default function BBCommandPalette({ onClose }: Props) {
  const { markets, signals, setSelectedTicker } = useDashboard();
  const [query, setQuery] = useState("");
  const [kalshiResults, setKalshiResults] = useState<KalshiMarket[]>([]);
  const [kalshiLoading, setKalshiLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Debounced Kalshi search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (query.length < 2) {
      setKalshiResults([]);
      setKalshiLoading(false);
      return;
    }
    setKalshiLoading(true);
    debounceRef.current = setTimeout(() => {
      api.searchKalshiMarkets(query)
        .then((data) => setKalshiResults(data.results?.slice(0, 10) ?? []))
        .catch(() => setKalshiResults([]))
        .finally(() => setKalshiLoading(false));
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query]);

  const results = useMemo(() => {
    if (!query) return markets.slice(0, 15);
    const q = query.toLowerCase();
    return markets
      .filter((m) => m.ticker.toLowerCase().includes(q) || m.title.toLowerCase().includes(q) || m.category.toLowerCase().includes(q))
      .slice(0, 15);
  }, [query, markets]);

  const signalMap = useMemo(() => {
    const map = new Map<string, (typeof signals)[0]>();
    signals.forEach((s) => map.set(s.ticker, s));
    return map;
  }, [signals]);

  function select(ticker: string) {
    setSelectedTicker(ticker);
    onClose();
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") onClose();
    if (e.key === "Enter" && results.length > 0) {
      select(results[0].ticker);
    }
  }

  function fmtKalshiPrice(cents: number): string {
    if (cents > 1) return `$${(cents / 100).toFixed(2)}`;
    return `$${cents.toFixed(2)}`;
  }

  return (
    <div className="bb-overlay" onClick={onClose}>
      <div className="bb-palette" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center border-b border-bb-border">
          <span className="text-bb-orange text-[13px] pl-3 pr-1">&gt;</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="ENTER TICKER OR QUERY"
            className="bb-palette-input"
          />
        </div>
        <div className="max-h-[400px] overflow-y-auto">
          {/* Local tracked markets */}
          {results.length > 0 && (
            <>
              <div className="px-3 py-[3px] text-[9px] text-bb-dim bg-bb-panel border-b border-bb-border tracking-widest">
                TRACKED MARKETS
              </div>
              {results.map((m, i) => {
                const sig = signalMap.get(m.ticker);
                return (
                  <div
                    key={m.ticker}
                    onClick={() => select(m.ticker)}
                    className={`flex items-center justify-between px-3 py-[3px] cursor-pointer text-[11px] hover:bg-bb-selected ${i === 0 ? "bg-bb-row-even" : ""}`}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="text-bb-orange w-[220px] truncate">{m.ticker}</span>
                      <span className="text-bb-dim truncate">{m.title}</span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-bb-white">${m.price.toFixed(2)}</span>
                      {sig && (
                        <span className={sig.edge >= 0 ? "text-bb-green" : "text-bb-red"}>
                          {sig.edge >= 0 ? "+" : ""}{(sig.edge * 100).toFixed(1)}
                        </span>
                      )}
                      {sig && (
                        <span className={sig.direction === "BUY_YES" ? "text-bb-green" : sig.direction === "BUY_NO" ? "text-bb-red" : "text-bb-yellow"} style={{ width: 32 }}>
                          {sig.direction === "BUY_YES" ? "BUY" : sig.direction === "BUY_NO" ? "SELL" : "HOLD"}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </>
          )}

          {/* Kalshi REST API results */}
          {query.length >= 2 && (
            <>
              <div className="px-3 py-[3px] text-[9px] text-bb-dim bg-bb-panel border-b border-bb-border border-t tracking-widest">
                KALSHI MARKETS {kalshiLoading && <span className="text-bb-yellow ml-2">SEARCHING...</span>}
              </div>
              {kalshiResults.map((km) => (
                <div
                  key={km.ticker}
                  onClick={() => select(km.ticker)}
                  className="flex items-center justify-between px-3 py-[3px] cursor-pointer text-[11px] hover:bg-bb-selected"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-bb-orange w-[220px] truncate">{km.ticker}</span>
                    <span className="text-bb-dim truncate">{km.title}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0 font-mono">
                    <span className="text-bb-green text-[10px]">
                      BID {fmtKalshiPrice(km.yes_bid)}
                    </span>
                    <span className="text-bb-red text-[10px]">
                      ASK {fmtKalshiPrice(km.yes_ask)}
                    </span>
                    <span className="text-bb-dim text-[10px] w-[60px] text-right">
                      VOL {km.volume.toLocaleString()}
                    </span>
                  </div>
                </div>
              ))}
              {!kalshiLoading && kalshiResults.length === 0 && query.length >= 2 && (
                <div className="text-bb-dim text-[11px] p-3">NO KALSHI RESULTS</div>
              )}
            </>
          )}

          {results.length === 0 && kalshiResults.length === 0 && !kalshiLoading && (
            <div className="text-bb-dim text-[11px] p-3">NO MATCHES</div>
          )}
        </div>
      </div>
    </div>
  );
}
