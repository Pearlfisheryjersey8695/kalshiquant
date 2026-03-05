"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";
import PanelHeader from "./PanelHeader";
import { useMemo, useState, useEffect } from "react";
import type { CorrelationData, Signal } from "@/lib/types";

export default function RiskDashboard() {
  const { risk, signals, signalsMeta } = useDashboard();

  // Category exposure from signals
  const categoryExposure = useMemo(() => {
    const map = new Map<string, number>();
    signals.forEach((s) => {
      if (s.recommended_contracts > 0) {
        const cat = s.category || "Other";
        map.set(cat, (map.get(cat) || 0) + s.risk.size_dollars);
      }
    });
    return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
  }, [signals]);

  const totalExposure = categoryExposure.reduce((sum, [, v]) => sum + v, 0);
  const portfolioValue = signalsMeta.portfolio_value || 10000;

  // Position limit utilization
  const limits = useMemo(() => {
    const positioned = signals.filter((s) => s.recommended_contracts > 0);
    const maxSingle = positioned.reduce((max, s) => Math.max(max, s.risk.size_dollars), 0);
    return [
      { label: "Total Exposure", value: totalExposure, limit: portfolioValue * 0.6, color: "#3b82f6" },
      { label: "Largest Position", value: maxSingle, limit: portfolioValue * 0.1, color: "#f59e0b" },
      { label: "Cash Reserve", value: portfolioValue - totalExposure, limit: portfolioValue * 0.4, color: "#00d26a" },
    ];
  }, [signals, totalExposure, portfolioValue]);

  const [corrData, setCorrData] = useState<CorrelationData | null>(null);

  useEffect(() => {
    api.getCorrelations()
      .then(setCorrData)
      .catch(() => {}); // silently fail
  }, []);

  const varValue = risk?.var_95 ?? 0;
  const varPct = (varValue / portfolioValue) * 100;

  // Donut chart colors
  const DONUT_COLORS = ["#3b82f6", "#00d26a", "#f59e0b", "#ff3b3b", "#a855f7", "#06b6d4"];

  return (
    <div className="flex flex-col h-full">
      <PanelHeader title="Risk Dashboard" />
      <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {/* VaR Gauge */}
        <div className="flex items-center gap-4">
          <div className="relative w-20 h-20 shrink-0">
            <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
              <circle cx="50" cy="50" r="40" fill="none" stroke="#1e1e2e" strokeWidth="8" />
              <circle
                cx="50" cy="50" r="40"
                fill="none"
                stroke={varPct > 15 ? "#ff3b3b" : varPct > 10 ? "#f59e0b" : "#00d26a"}
                strokeWidth="8"
                strokeDasharray={`${Math.min(varPct * 2.5, 251)} 251`}
                strokeLinecap="round"
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="font-mono text-sm font-bold">{varPct.toFixed(1)}%</span>
              <span className="text-[8px] text-text-secondary">VaR</span>
            </div>
          </div>
          <div className="space-y-1">
            <div className="text-[10px] text-text-secondary uppercase tracking-wider">Portfolio VaR (95%)</div>
            <div className="font-mono text-lg font-bold text-red">${varValue.toLocaleString(undefined, { minimumFractionDigits: 0 })}</div>
            <div className="text-[10px] text-text-secondary">
              {risk?.positions.length ?? 0} positioned markets
            </div>
          </div>
        </div>

        {/* Category Exposure Donut */}
        <div>
          <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-2">Category Exposure</div>
          {categoryExposure.length > 0 ? (
            <div className="flex items-start gap-3">
              {/* Mini donut */}
              <svg viewBox="0 0 100 100" className="w-16 h-16 shrink-0 -rotate-90">
                {(() => {
                  let offset = 0;
                  return categoryExposure.map(([cat, val], i) => {
                    const pct = totalExposure > 0 ? (val / totalExposure) * 251 : 0;
                    const el = (
                      <circle
                        key={cat}
                        cx="50" cy="50" r="40"
                        fill="none"
                        stroke={DONUT_COLORS[i % DONUT_COLORS.length]}
                        strokeWidth="10"
                        strokeDasharray={`${pct} ${251 - pct}`}
                        strokeDashoffset={-offset}
                      />
                    );
                    offset += pct;
                    return el;
                  });
                })()}
              </svg>
              {/* Legend */}
              <div className="flex-1 space-y-1">
                {categoryExposure.map(([cat, val], i) => (
                  <div key={cat} className="flex items-center justify-between text-[10px]">
                    <div className="flex items-center gap-1.5">
                      <div className="w-2 h-2 rounded-sm" style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} />
                      <span className="text-text-secondary">{cat}</span>
                    </div>
                    <span className="font-mono">${val.toFixed(0)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-[10px] text-text-secondary">No positioned signals</div>
          )}
        </div>

        {/* Position Limit Utilization Bars */}
        <div>
          <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-2">Limit Utilization</div>
          <div className="space-y-2">
            {limits.map((lim) => {
              const pct = lim.limit > 0 ? Math.min((lim.value / lim.limit) * 100, 100) : 0;
              const isOver = lim.value > lim.limit;
              return (
                <div key={lim.label}>
                  <div className="flex justify-between text-[10px] mb-0.5">
                    <span className="text-text-secondary">{lim.label}</span>
                    <span className={`font-mono ${isOver ? "text-red font-bold" : ""}`}>
                      ${lim.value.toFixed(0)} / ${lim.limit.toFixed(0)}
                    </span>
                  </div>
                  <div className="h-1.5 bg-bg rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${pct}%`, background: isOver ? "#ff3b3b" : lim.color }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        {/* Correlation Matrix Heatmap */}
        {corrData && corrData.tickers.length > 1 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-2">Correlations</div>
            <div className="overflow-auto max-h-48">
              <CorrelationHeatmap data={corrData} />
            </div>
          </div>
        )}

        {/* Divergence Alerts */}
        {corrData && corrData.divergences.length > 0 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">Divergence Alerts</div>
            <div className="space-y-1">
              {corrData.divergences.slice(0, 5).map((d, i) => (
                <div key={i} className="text-[9px] text-amber/80 bg-amber/5 border border-amber/20 rounded px-2 py-1">
                  <span className="font-mono">{d.t1.slice(0, 12)}</span> vs{" "}
                  <span className="font-mono">{d.t2.slice(0, 12)}</span>: corr{" "}
                  <span className="font-semibold">{d.correlation.toFixed(2)}</span> but{" "}
                  <span className="font-semibold">{(d.spread * 100).toFixed(0)}c</span> spread
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Regime Map */}
        <RegimeMap signals={signals} />
      </div>
    </div>
  );
}

function corrColor(val: number): string {
  // Red for +1, white for 0, blue for -1
  if (val > 0) {
    const r = 255;
    const g = Math.round(255 * (1 - val));
    const b = Math.round(255 * (1 - val));
    return `rgb(${r},${g},${b})`;
  } else {
    const r = Math.round(255 * (1 + val));
    const g = Math.round(255 * (1 + val));
    const b = 255;
    return `rgb(${r},${g},${b})`;
  }
}

function CorrelationHeatmap({ data }: { data: CorrelationData }) {
  const { tickers, matrix } = data;
  const [hoveredCell, setHoveredCell] = useState<string | null>(null);

  // Build lookup
  const corrLookup = useMemo(() => {
    const map = new Map<string, number>();
    matrix.forEach((e) => {
      map.set(`${e.t1}|${e.t2}`, e.corr);
      map.set(`${e.t2}|${e.t1}`, e.corr);
    });
    return map;
  }, [matrix]);

  const n = tickers.length;
  const cellSize = Math.max(8, Math.min(16, 200 / n));

  return (
    <div className="relative">
      <div className="inline-block">
        <svg
          width={n * cellSize + 60}
          height={n * cellSize + 10}
          className="block"
        >
          {/* Ticker labels on left */}
          {tickers.map((t, i) => (
            <text
              key={`label-${i}`}
              x={58}
              y={i * cellSize + cellSize / 2 + 3}
              textAnchor="end"
              fill="#888899"
              fontSize="6"
              fontFamily="JetBrains Mono"
            >
              {t.slice(0, 8)}
            </text>
          ))}
          {/* Cells */}
          {tickers.map((t1, i) =>
            tickers.map((t2, j) => {
              const corr = i === j ? 1.0 : (corrLookup.get(`${t1}|${t2}`) ?? 0);
              const key = `${t1}|${t2}`;
              return (
                <rect
                  key={key}
                  x={60 + j * cellSize}
                  y={i * cellSize}
                  width={cellSize - 1}
                  height={cellSize - 1}
                  fill={corrColor(corr)}
                  opacity={0.8}
                  onMouseEnter={() => setHoveredCell(`${t1.slice(0, 10)} vs ${t2.slice(0, 10)}: ${corr.toFixed(2)}`)}
                  onMouseLeave={() => setHoveredCell(null)}
                />
              );
            })
          )}
        </svg>
      </div>
      {hoveredCell && (
        <div className="absolute top-0 left-0 bg-surface border border-border rounded px-2 py-1 text-[9px] font-mono pointer-events-none z-10">
          {hoveredCell}
        </div>
      )}
    </div>
  );
}

const REGIME_BG: Record<string, string> = {
  TRENDING: "bg-blue/30 text-blue border border-blue/20",
  MEAN_REVERTING: "bg-green/30 text-green border border-green/20",
  HIGH_VOLATILITY: "bg-red/30 text-red border border-red/20",
  CONVERGENCE: "bg-amber/30 text-amber border border-amber/20",
  STALE: "bg-text-secondary/20 text-text-secondary border border-text-secondary/10",
  UNKNOWN: "bg-surface text-text-secondary border border-border",
};

function RegimeMap({ signals }: { signals: Signal[] }) {
  if (signals.length === 0) return null;

  return (
    <div>
      <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-2">Regime Map</div>
      <div className="grid grid-cols-4 gap-1">
        {signals.map((s) => {
          const regime = s.regime || "UNKNOWN";
          const topProb = s.regime_probs
            ? Math.max(...Object.values(s.regime_probs))
            : 0;
          return (
            <div
              key={s.ticker}
              className={`p-1 rounded text-[7px] font-mono text-center truncate ${REGIME_BG[regime] || REGIME_BG.UNKNOWN}`}
              title={`${s.ticker}: ${regime} (${(topProb * 100).toFixed(0)}%)`}
            >
              {s.ticker.slice(-8)}
            </div>
          );
        })}
      </div>
      {/* Legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-2">
        {["TRENDING", "MEAN_REVERTING", "HIGH_VOLATILITY", "CONVERGENCE", "STALE"].map((r) => (
          <div key={r} className="flex items-center gap-1 text-[8px] text-text-secondary">
            <div className={`w-2 h-2 rounded-sm ${REGIME_BG[r].split(" ")[0]}`} />
            <span>{r.slice(0, 5)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
