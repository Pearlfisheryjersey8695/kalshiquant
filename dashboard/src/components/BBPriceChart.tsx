"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";
import { fmtPrice, fmtVolume } from "@/lib/format";
import { useEffect, useRef, useState } from "react";
import type { HistoryPoint, KalshiOrderbook, KalshiOrderLevel } from "@/lib/types";

export default function BBPriceChart() {
  const { selectedTicker, signals, markets } = useDashboard();
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [orderbook, setOrderbook] = useState<KalshiOrderbook | null>(null);

  const market = markets.find((m) => m.ticker === selectedTicker);
  const signal = signals.find((s) => s.ticker === selectedTicker);

  // Fetch history + supplement with Kalshi trades if sparse
  useEffect(() => {
    if (!selectedTicker) { setHistory([]); return; }
    setLoading(true);
    api.getHistory(selectedTicker, 500)
      .then(async (h) => {
        if (h.length < 10) {
          // Sparse local data - supplement with Kalshi trade history
          try {
            const kalshiData = await api.getKalshiTrades(selectedTicker, 200);
            if (kalshiData.trades && kalshiData.trades.length > 0) {
              const kalshiPoints: HistoryPoint[] = kalshiData.trades.map((t) => ({
                ts: t.created_time,
                price: t.yes_price > 1 ? t.yes_price / 100 : t.yes_price,
                yes_bid: t.yes_price > 1 ? t.yes_price / 100 : t.yes_price,
                yes_ask: t.yes_price > 1 ? t.yes_price / 100 : t.yes_price,
                volume: t.count,
              }));
              // Merge: local data first, then Kalshi trades for gaps
              const existingTimes = new Set(h.map((p) => p.ts));
              const merged = [...h];
              for (const kp of kalshiPoints) {
                if (!existingTimes.has(kp.ts)) merged.push(kp);
              }
              merged.sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());
              setHistory(merged);
              return;
            }
          } catch {
            // Fall through to local data
          }
        }
        setHistory(h);
      })
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  }, [selectedTicker]);

  // Fetch orderbook
  useEffect(() => {
    if (!selectedTicker) { setOrderbook(null); return; }
    api.getKalshiOrderbook(selectedTicker)
      .then((ob) => setOrderbook(ob))
      .catch(() => setOrderbook(null));
  }, [selectedTicker]);

  // Render chart with Bloomberg theme
  useEffect(() => {
    if (!containerRef.current || !selectedTicker || history.length === 0) {
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
      return;
    }

    let cancelled = false;
    let roRef: ResizeObserver | null = null;

    import("lightweight-charts").then((lc) => {
      if (cancelled || !containerRef.current) return;
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }

      const chart = lc.createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
        layout: {
          background: { type: lc.ColorType.Solid, color: "#000000" },
          textColor: "#888888",
          fontFamily: "'IBM Plex Mono', 'Courier New', monospace",
          fontSize: 10,
        },
        grid: {
          vertLines: { color: "#111111" },
          horzLines: { color: "#111111" },
        },
        crosshair: {
          vertLine: { color: "#ff6600", width: 1, style: lc.LineStyle.Dashed },
          horzLine: { color: "#ff6600", width: 1, style: lc.LineStyle.Dashed },
        },
        rightPriceScale: {
          borderColor: "#1a1a1a",
        },
        timeScale: {
          borderColor: "#1a1a1a",
          timeVisible: true,
        },
      });

      chartRef.current = chart;

      const priceSeries = chart.addSeries(lc.LineSeries, {
        color: "#ffffff",
        lineWidth: 1,
        priceFormat: { type: "custom", formatter: (p: number) => `$${p.toFixed(2)}` },
      });

      const volumeSeries = chart.addSeries(lc.HistogramSeries, {
        color: "#1a1a1a",
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

      const priceData: { time: number; value: number }[] = [];
      const volumeData: { time: number; value: number; color: string }[] = [];
      const seenTimes = new Set<number>();

      for (const h of history) {
        const t = Math.floor(new Date(h.ts).getTime() / 1000);
        if (seenTimes.has(t) || isNaN(t)) continue;
        seenTimes.add(t);
        priceData.push({ time: t, value: h.price });
        volumeData.push({
          time: t, value: h.volume,
          color: h.price >= (priceData[priceData.length - 2]?.value ?? h.price)
            ? "rgba(0,255,0,0.30)" : "rgba(255,0,0,0.30)",
        });
      }

      priceData.sort((a, b) => a.time - b.time);
      volumeData.sort((a, b) => a.time - b.time);

      if (priceData.length > 0) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        priceSeries.setData(priceData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        volumeSeries.setData(volumeData as any);

        // Fair value overlay
        if (signal && priceData.length >= 2) {
          const fvLine = chart.addSeries(lc.LineSeries, {
            color: "#ff6600",
            lineWidth: 1,
            lineStyle: lc.LineStyle.Dashed,
            priceFormat: { type: "custom", formatter: (p: number) => `FV $${p.toFixed(2)}` },
          });
          fvLine.setData([
            { time: priceData[0].time, value: signal.fair_value },
            { time: priceData[priceData.length - 1].time, value: signal.fair_value },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          ] as any);
        }
      }

      chart.timeScale().fitContent();

      const ro = new ResizeObserver((entries) => {
        for (const entry of entries) {
          chart.applyOptions({ width: entry.contentRect.width, height: entry.contentRect.height });
        }
      });
      if (containerRef.current) ro.observe(containerRef.current);
      roRef = ro;
    });

    return () => {
      cancelled = true;
      roRef?.disconnect();
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
    };
  }, [history, signal, selectedTicker]);

  const showPlaceholder = !selectedTicker;
  const showLoading = selectedTicker && loading;
  const showNoData = selectedTicker && !loading && history.length === 0;

  // Compute change
  const firstPrice = history.length > 0 ? history[0].price : 0;
  const lastPrice = market?.price ?? (history.length > 0 ? history[history.length - 1].price : 0);
  const chg = lastPrice - firstPrice;
  const chgPct = firstPrice > 0 ? (chg / firstPrice) * 100 : 0;

  // Orderbook helpers
  const maxQty = orderbook
    ? Math.max(
        ...((orderbook.orderbook?.yes ?? []).map((l: KalshiOrderLevel) => l.quantity)),
        ...((orderbook.orderbook?.no ?? []).map((l: KalshiOrderLevel) => l.quantity)),
        1
      )
    : 1;

  function fmtOBPrice(cents: number): string {
    if (cents > 1) return (cents / 100).toFixed(2);
    return cents.toFixed(2);
  }

  return (
    <div className="flex flex-col h-full">
      <div className="bb-panel-title flex items-center justify-between">
        <span>PRICE CHART</span>
        <span className="text-bb-dim">{selectedTicker ?? "SELECT MARKET"}</span>
      </div>

      {/* Bloomberg data bar */}
      {market && (
        <div className="flex items-center gap-4 px-2 py-[2px] border-b border-bb-border bg-bb-panel text-[10px] shrink-0 overflow-x-auto">
          <span className="text-bb-orange">{selectedTicker}</span>
          <span className="text-bb-dim">LAST: <span className="text-bb-white">{fmtPrice(lastPrice)}</span></span>
          <span className="text-bb-dim">CHG: <span className={chg >= 0 ? "text-bb-green" : "text-bb-red"}>
            {chg >= 0 ? "+" : ""}{chg.toFixed(3)}
          </span></span>
          <span className="text-bb-dim">CHG%: <span className={chg >= 0 ? "text-bb-green" : "text-bb-red"}>
            {chgPct >= 0 ? "+" : ""}{chgPct.toFixed(1)}%
          </span></span>
          <span className="text-bb-dim">VOL: <span className="text-bb-white">{fmtVolume(market.volume)}</span></span>
          {signal && (
            <>
              <span className="text-bb-dim">FAIR VAL: <span className="text-bb-orange">{fmtPrice(signal.fair_value)}</span></span>
              <span className="text-bb-dim">EDGE: <span className={signal.edge >= 0 ? "text-bb-green" : "text-bb-red"}>
                {signal.edge >= 0 ? "+" : ""}{(signal.edge * 100).toFixed(1)}pts
              </span></span>
            </>
          )}
        </div>
      )}

      {/* Chart container */}
      <div ref={containerRef} className="flex-1 min-h-0 relative bg-bb-black">
        {showPlaceholder && (
          <div className="absolute inset-0 flex items-center justify-center text-bb-dim text-[11px]">
            SELECT A MARKET TO VIEW CHART
          </div>
        )}
        {showLoading && (
          <div className="absolute inset-0 flex items-center justify-center text-bb-dim text-[11px]">
            LOADING...
          </div>
        )}
        {showNoData && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-bb-dim text-[11px] gap-1">
            <span>NO HISTORY AVAILABLE</span>
            <span className="text-[9px]">WAITING FOR LIVE DATA</span>
          </div>
        )}
      </div>

      {/* Orderbook depth visualization */}
      {orderbook && orderbook.orderbook && selectedTicker && (
        <div className="border-t border-bb-border bg-bb-black shrink-0">
          <div className="flex items-center justify-between px-2 py-[2px] border-b border-bb-border">
            <span className="text-[9px] text-bb-dim tracking-widest">ORDERBOOK DEPTH</span>
            <span className="text-[9px] text-bb-dim">{orderbook.ticker}</span>
          </div>
          <div className="flex gap-0" style={{ maxHeight: 120, overflow: "hidden" }}>
            {/* YES Bids (green) */}
            <div className="flex-1 px-1 py-[2px]">
              <div className="text-[8px] text-bb-green tracking-widest mb-[2px] px-1">YES BID</div>
              {(orderbook.orderbook.yes ?? []).slice(0, 8).map((level: KalshiOrderLevel, i: number) => (
                <div key={`bid-${i}`} className="flex items-center gap-1 text-[10px] font-mono h-[14px] px-1">
                  <span className="text-bb-green w-[40px] text-right">{fmtOBPrice(level.price)}</span>
                  <div className="flex-1 h-[10px] relative">
                    <div
                      className="absolute left-0 top-0 h-full bg-green-900/60"
                      style={{ width: `${(level.quantity / maxQty) * 100}%` }}
                    />
                  </div>
                  <span className="text-bb-dim w-[36px] text-right">{level.quantity}</span>
                </div>
              ))}
              {(orderbook.orderbook.yes ?? []).length === 0 && (
                <div className="text-[9px] text-bb-dim px-1">--</div>
              )}
            </div>
            {/* NO Asks (red) */}
            <div className="flex-1 px-1 py-[2px] border-l border-bb-border">
              <div className="text-[8px] text-bb-red tracking-widest mb-[2px] px-1">NO ASK</div>
              {(orderbook.orderbook.no ?? []).slice(0, 8).map((level: KalshiOrderLevel, i: number) => (
                <div key={`ask-${i}`} className="flex items-center gap-1 text-[10px] font-mono h-[14px] px-1">
                  <span className="text-bb-red w-[40px] text-right">{fmtOBPrice(level.price)}</span>
                  <div className="flex-1 h-[10px] relative">
                    <div
                      className="absolute right-0 top-0 h-full bg-red-900/60"
                      style={{ width: `${(level.quantity / maxQty) * 100}%` }}
                    />
                  </div>
                  <span className="text-bb-dim w-[36px] text-right">{level.quantity}</span>
                </div>
              ))}
              {(orderbook.orderbook.no ?? []).length === 0 && (
                <div className="text-[9px] text-bb-dim px-1">--</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
