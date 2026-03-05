"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";
import { fmtPrice } from "@/lib/format";
import PanelHeader from "./PanelHeader";
import { useEffect, useRef, useState } from "react";
import type { HistoryPoint } from "@/lib/types";

export default function PriceChart() {
  const { selectedTicker, signals, markets } = useDashboard();
  const containerRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [loading, setLoading] = useState(false);

  const market = markets.find((m) => m.ticker === selectedTicker);
  const signal = signals.find((s) => s.ticker === selectedTicker);

  // Fetch history when ticker changes
  useEffect(() => {
    if (!selectedTicker) {
      setHistory([]);
      return;
    }
    setLoading(true);
    api
      .getHistory(selectedTicker, 500)
      .then((h) => setHistory(h))
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  }, [selectedTicker]);

  // Render chart
  useEffect(() => {
    if (!containerRef.current || !selectedTicker) return;

    // If no history, don't try to create chart
    if (history.length === 0) {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      return;
    }

    let cancelled = false;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let roRef: ResizeObserver | null = null;

    import("lightweight-charts").then((lc) => {
      if (cancelled || !containerRef.current) return;

      // Cleanup previous chart
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }

      const chart = lc.createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
        layout: {
          background: { type: lc.ColorType.Solid, color: "#12121a" },
          textColor: "#888899",
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 10,
        },
        grid: {
          vertLines: { color: "#1e1e2e" },
          horzLines: { color: "#1e1e2e" },
        },
        crosshair: {
          vertLine: { color: "#3b82f6", width: 1, style: lc.LineStyle.Dashed },
          horzLine: { color: "#3b82f6", width: 1, style: lc.LineStyle.Dashed },
        },
        rightPriceScale: {
          borderColor: "#1e1e2e",
        },
        timeScale: {
          borderColor: "#1e1e2e",
          timeVisible: true,
        },
      });

      chartRef.current = chart;

      const priceSeries = chart.addSeries(lc.LineSeries, {
        color: "#3b82f6",
        lineWidth: 2,
        priceFormat: {
          type: "custom",
          formatter: (price: number) => `$${price.toFixed(2)}`,
        },
      });

      const volumeSeries = chart.addSeries(lc.HistogramSeries, {
        color: "#1e1e2e",
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });

      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 },
      });

      // Parse timestamps — use unix seconds for intraday data
      const priceData: { time: number; value: number }[] = [];
      const volumeData: { time: number; value: number; color: string }[] = [];
      const seenTimes = new Set<number>();

      for (const h of history) {
        const t = Math.floor(new Date(h.ts).getTime() / 1000);
        if (seenTimes.has(t) || isNaN(t)) continue;
        seenTimes.add(t);

        priceData.push({ time: t, value: h.price });
        volumeData.push({
          time: t,
          value: h.volume,
          color:
            h.price >= (priceData[priceData.length - 2]?.value ?? h.price)
              ? "rgba(0,210,106,0.3)"
              : "rgba(255,59,59,0.3)",
        });
      }

      // Sort by time ascending (required by lightweight-charts)
      priceData.sort((a, b) => a.time - b.time);
      volumeData.sort((a, b) => a.time - b.time);

      if (priceData.length > 0) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        priceSeries.setData(priceData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        volumeSeries.setData(volumeData as any);

        // Fair value overlay line
        if (signal && priceData.length >= 2) {
          const fvLine = chart.addSeries(lc.LineSeries, {
            color: "#f59e0b",
            lineWidth: 1,
            lineStyle: lc.LineStyle.Dashed,
            priceFormat: {
              type: "custom",
              formatter: (price: number) => `FV $${price.toFixed(2)}`,
            },
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
          chart.applyOptions({
            width: entry.contentRect.width,
            height: entry.contentRect.height,
          });
        }
      });
      ro.observe(containerRef.current!);
      roRef = ro;
    });

    return () => {
      cancelled = true;
      roRef?.disconnect();
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, [history, signal, selectedTicker]);

  // Determine what to show in the chart area
  const showPlaceholder = !selectedTicker;
  const showLoading = selectedTicker && loading;
  const showNoData = selectedTicker && !loading && history.length === 0;

  return (
    <div className="flex flex-col h-full">
      <PanelHeader
        title="Price Chart"
        subtitle={selectedTicker ?? "Select a market"}
        right={
          market ? (
            <div className="flex items-center gap-3 text-[10px]">
              <span className="font-mono font-bold text-sm">{fmtPrice(market.price)}</span>
              {signal && (
                <span className={`font-mono font-semibold ${signal.edge > 0 ? "text-green" : "text-red"}`}>
                  FV {fmtPrice(signal.fair_value)}
                </span>
              )}
            </div>
          ) : undefined
        }
      />
      <div ref={containerRef} className="flex-1 min-h-0 relative">
        {showPlaceholder && (
          <div className="absolute inset-0 flex items-center justify-center text-text-secondary text-sm">
            Click a market in the scanner to view chart
          </div>
        )}
        {showLoading && (
          <div className="absolute inset-0 flex items-center justify-center text-text-secondary text-sm">
            Loading history...
          </div>
        )}
        {showNoData && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-text-secondary text-sm gap-1">
            <span>No history available</span>
            <span className="text-[10px]">Waiting for live price data from WebSocket</span>
          </div>
        )}
      </div>
    </div>
  );
}
