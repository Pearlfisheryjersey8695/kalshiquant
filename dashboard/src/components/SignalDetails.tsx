"use client";

import { useDashboard } from "@/lib/store";
import { fmtPrice, fmtDollar } from "@/lib/format";
import { api } from "@/lib/api";
import PanelHeader from "./PanelHeader";
import { useState, useEffect } from "react";
import type { DecayPoint, VolSurface } from "@/lib/types";

const REGIME_COLORS: Record<string, string> = {
  TRENDING: "bg-blue/20 text-blue border-blue/30",
  MEAN_REVERTING: "bg-green/20 text-green border-green/30",
  HIGH_VOLATILITY: "bg-red/20 text-red border-red/30",
  CONVERGENCE: "bg-amber/20 text-amber border-amber/30",
  STALE: "bg-text-secondary/20 text-text-secondary border-text-secondary/30",
};

const REGIME_BAR_COLORS: Record<string, string> = {
  TRENDING: "#3b82f6",
  MEAN_REVERTING: "#00d26a",
  HIGH_VOLATILITY: "#ff3b3b",
  CONVERGENCE: "#f59e0b",
  STALE: "#888899",
};

export default function SignalDetails() {
  const { selectedTicker, signals, markets } = useDashboard();

  // Robust signal matching: exact ticker match first, then partial match
  const signal = signals.find((s) => s.ticker === selectedTicker)
    ?? signals.find((s) => selectedTicker && s.ticker.startsWith(selectedTicker))
    ?? signals.find((s) => selectedTicker && selectedTicker.startsWith(s.ticker));

  const market = markets.find((m) => m.ticker === selectedTicker);

  // Vol surface for series markets (hooks must be before early returns)
  const [volSurface, setVolSurface] = useState<VolSurface | null>(null);
  const ticker = signal?.ticker;
  useEffect(() => {
    if (!ticker) { setVolSurface(null); return; }
    const parts = ticker.split("-");
    if (parts.length < 3) { setVolSurface(null); return; }
    const prefix = parts.slice(0, -1).join("-");
    api.getVolSurface(prefix).then(setVolSurface).catch(() => setVolSurface(null));
  }, [ticker]);

  if (!selectedTicker) {
    return (
      <div className="flex flex-col h-full">
        <PanelHeader title="Signal Details" />
        <div className="flex-1 flex items-center justify-center text-text-secondary text-sm">
          Select a market
        </div>
      </div>
    );
  }

  if (!signal) {
    return (
      <div className="flex flex-col h-full">
        <PanelHeader title="Signal Details" subtitle={selectedTicker} />
        <div className="flex-1 flex flex-col items-center justify-center text-text-secondary text-sm gap-2 p-4">
          <span>No signal for this market</span>
          {market && (
            <div className="text-[10px] text-center space-y-1">
              <div>Price: <span className="font-mono">{fmtPrice(market.price)}</span></div>
              <div>Volume: <span className="font-mono">{market.volume.toLocaleString()}</span></div>
              <div className="text-[9px]">Only markets with |edge| &gt; 2c generate signals</div>
            </div>
          )}
        </div>
      </div>
    );
  }

  const edgeCents = signal.edge * 100;
  const isPositive = signal.edge > 0;
  const regimeStyle = REGIME_COLORS[signal.regime] || REGIME_COLORS.STALE;

  return (
    <div className="flex flex-col h-full">
      <PanelHeader
        title="Signal Details"
        subtitle={signal.ticker.length > 28 ? signal.ticker.slice(0, 28) + "\u2026" : signal.ticker}
      />
      <div className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {/* Edge display - large */}
        <div className="flex items-center gap-4">
          <div className="text-center">
            <div className={`font-mono text-3xl font-bold ${isPositive ? "text-green" : "text-red"}`}>
              {edgeCents > 0 ? "+" : ""}{edgeCents.toFixed(1)}c
            </div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mt-0.5">Edge</div>
          </div>
          <div className="flex-1 space-y-1.5">
            {/* Direction badge */}
            <div className="flex items-center gap-2">
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                  signal.direction === "BUY_YES"
                    ? "bg-green/20 text-green"
                    : signal.direction === "BUY_NO"
                    ? "bg-red/20 text-red"
                    : "bg-text-secondary/20 text-text-secondary"
                }`}
              >
                {signal.direction}
              </span>
              <span className={`px-2 py-0.5 rounded text-[10px] font-medium border ${regimeStyle}`}>
                {signal.regime}
              </span>
            </div>
            {/* Regime probability distribution bar */}
            {signal.regime_probs && Object.keys(signal.regime_probs).length > 0 && (
              <div className="flex h-2 rounded-full overflow-hidden" title="Regime probabilities">
                {Object.entries(signal.regime_probs)
                  .sort(([, a], [, b]) => b - a)
                  .filter(([, p]) => p > 0.03)
                  .map(([regime, prob]) => (
                    <div
                      key={regime}
                      style={{ width: `${prob * 100}%`, background: REGIME_BAR_COLORS[regime] || "#888899" }}
                      className="h-full"
                      title={`${regime}: ${(prob * 100).toFixed(0)}%`}
                    />
                  ))}
              </div>
            )}
            {/* Price vs FV */}
            <div className="text-[10px] text-text-secondary">
              Market {fmtPrice(signal.current_price)} vs Fair Value {fmtPrice(signal.fair_value)}
            </div>
          </div>
        </div>

        {/* Confidence bar */}
        <div>
          <div className="flex justify-between text-[10px] mb-1">
            <span className="text-text-secondary">Confidence</span>
            <span className="font-mono font-semibold">{(signal.confidence * 100).toFixed(0)}%</span>
          </div>
          <div className="h-2 bg-bg rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${signal.confidence * 100}%`,
                background: signal.confidence > 0.7 ? "#00d26a" : signal.confidence > 0.4 ? "#f59e0b" : "#ff3b3b",
              }}
            />
          </div>
        </div>

        {/* Fee Economics */}
        <div className="flex items-center gap-3 text-[10px] bg-bg rounded p-2 border border-border">
          <div>
            <span className="text-text-secondary">Gross </span>
            <span className={`font-mono font-semibold ${signal.edge > 0 ? "text-green" : "text-red"}`}>
              {(signal.edge * 100).toFixed(1)}c
            </span>
          </div>
          <span className="text-text-secondary">-</span>
          <div>
            <span className="text-text-secondary">Fee </span>
            <span className="font-mono text-red">{((signal.fee_impact ?? signal.risk?.fee_impact ?? 0.03) * 100).toFixed(1)}c</span>
          </div>
          <span className="text-text-secondary">=</span>
          <div>
            <span className="text-text-secondary">Net </span>
            <span className={`font-mono font-semibold ${(signal.net_edge ?? 0) > 0 ? "text-green" : "text-red"}`}>
              {((signal.net_edge ?? signal.risk?.net_edge ?? 0) * 100).toFixed(1)}c
            </span>
          </div>
          {signal.meta_quality != null && (
            <>
              <span className="text-text-secondary">|</span>
              <div>
                <span className="text-text-secondary">Meta </span>
                <span className={`font-mono font-semibold ${signal.meta_quality > 0.6 ? "text-green" : signal.meta_quality > 0.4 ? "text-amber" : "text-red"}`}>
                  {(signal.meta_quality * 100).toFixed(0)}%
                </span>
              </div>
            </>
          )}
        </div>

        {/* Market vs Consensus */}
        {signal.consensus_edge != null && Math.abs(signal.consensus_edge) > 0.001 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">Market vs Consensus</div>
            <div className="bg-bg rounded border border-border p-2">
              <div className="flex justify-between text-[10px] mb-1">
                <span>Market: <span className="font-mono">{(signal.current_price * 100).toFixed(0)}%</span></span>
                <span>Consensus: <span className="font-mono font-semibold">{((signal.current_price + signal.consensus_edge) * 100).toFixed(0)}%</span></span>
              </div>
              <div className="relative h-3 bg-surface rounded-full">
                <div className="absolute h-3 w-1 bg-text-secondary rounded" style={{ left: `${signal.current_price * 100}%` }} />
                <div className="absolute h-3 w-1 bg-blue rounded" style={{ left: `${Math.min(Math.max((signal.current_price + signal.consensus_edge) * 100, 0), 100)}%` }} />
                {/* Shaded region between markers */}
                <div
                  className="absolute h-3 rounded opacity-20"
                  style={{
                    left: `${Math.min(signal.current_price, signal.current_price + signal.consensus_edge) * 100}%`,
                    width: `${Math.abs(signal.consensus_edge) * 100}%`,
                    background: signal.consensus_edge > 0 ? "#00d26a" : "#ff3b3b",
                  }}
                />
              </div>
              <div className="text-[9px] text-text-secondary mt-1">
                Edge: <span className={`font-mono font-semibold ${signal.consensus_edge > 0 ? "text-green" : "text-red"}`}>
                  {(signal.consensus_edge * 100).toFixed(1)}c
                </span>
                {signal.consensus_prob ? <span className="ml-2">Source prob: <span className="font-mono">{(signal.consensus_prob * 100).toFixed(0)}%</span></span> : null}
              </div>
            </div>
          </div>
        )}

        {/* AI Sentiment */}
        {signal.ai_reasoning && signal.ai_reasoning.length > 0 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">AI Sentiment</div>
            <div className="bg-bg rounded border border-border p-2 text-[10px]">
              <div className="flex justify-between mb-1">
                <span>Claude estimate: <span className="font-mono font-semibold">{((signal.ai_prob ?? 0) * 100).toFixed(0)}%</span></span>
                <span className={`font-mono ${(signal.ai_edge ?? 0) > 0 ? "text-green" : "text-red"}`}>
                  {((signal.ai_edge ?? 0) * 100).toFixed(1)}c edge
                </span>
              </div>
              <div className="text-[9px] text-text-secondary italic">{signal.ai_reasoning}</div>
            </div>
          </div>
        )}

        {/* Edge Decay Sparkline */}
        {signal.decay_curve && signal.decay_curve.length > 1 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">Edge Decay</div>
            <EdgeDecaySparkline curve={signal.decay_curve} />
          </div>
        )}

        {/* Stats grid */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[10px]">
          <div className="flex justify-between">
            <span className="text-text-secondary">Strategy</span>
            <span
              className="font-mono font-semibold px-1 py-0.5 rounded text-[9px]"
              style={{
                background:
                  signal.strategy === "momentum" ? "rgba(245,158,11,0.2)" :
                  signal.strategy === "mean_reversion" ? "rgba(0,210,106,0.2)" :
                  signal.strategy === "event_driven" ? "rgba(168,85,247,0.2)" :
                  signal.strategy === "convergence" ? "rgba(59,130,246,0.2)" :
                  "rgba(136,136,153,0.2)",
                color:
                  signal.strategy === "momentum" ? "#f59e0b" :
                  signal.strategy === "mean_reversion" ? "#00d26a" :
                  signal.strategy === "event_driven" ? "#a855f7" :
                  signal.strategy === "convergence" ? "#3b82f6" :
                  "#888899",
              }}
            >
              {signal.strategy}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Prediction 1h</span>
            <span
              className={`font-mono font-semibold ${
                signal.price_prediction_1h > 0
                  ? "text-green"
                  : signal.price_prediction_1h < 0
                  ? "text-red"
                  : "text-text-secondary"
              }`}
            >
              {signal.price_prediction_1h > 0 ? "UP" : signal.price_prediction_1h < 0 ? "DOWN" : "FLAT"}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">ML Confidence</span>
            <span className="font-mono">{(signal.prediction_confidence * 100).toFixed(0)}%</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Contracts</span>
            <span className="font-mono font-semibold">{signal.recommended_contracts}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Stop Loss</span>
            <span className="font-mono text-red">{fmtPrice(signal.risk.stop_loss)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Take Profit</span>
            <span className="font-mono text-green">{fmtPrice(signal.risk.take_profit)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Max Loss</span>
            <span className="font-mono text-red">{fmtDollar(signal.risk.true_max_loss)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">R:R</span>
            <span className="font-mono">{signal.risk.risk_reward.toFixed(2)}</span>
          </div>
        </div>

        {/* FV Component Weights */}
        {signal.fv_weights && Object.keys(signal.fv_weights).length > 0 && (
          <div>
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">FV Weights (Adaptive)</div>
            <div className="flex h-3 rounded-full overflow-hidden mb-1">
              {Object.entries(signal.fv_weights).map(([name, w]) => (
                <div
                  key={name}
                  style={{
                    width: `${w * 100}%`,
                    background: name === "base_rate" ? "#3b82f6" : name === "orderbook" ? "#00d26a" : name === "cross_market" ? "#f59e0b" : name === "time_decay" ? "#a855f7" : "#06b6d4",
                  }}
                  className="h-full"
                  title={`${name}: ${(w * 100).toFixed(1)}%`}
                />
              ))}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5">
              {Object.entries(signal.fv_weights).map(([name, w]) => (
                <div key={name} className="text-[8px] text-text-secondary flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-sm inline-block" style={{
                    background: name === "base_rate" ? "#3b82f6" : name === "orderbook" ? "#00d26a" : name === "cross_market" ? "#f59e0b" : name === "time_decay" ? "#a855f7" : "#06b6d4",
                  }} />
                  <span className="font-mono">{({base_rate:"Base",orderbook:"OB",cross_market:"Cross",time_decay:"Decay",sentiment:"Sent"} as Record<string,string>)[name] ?? name} {(w * 100).toFixed(0)}%</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Implied Distribution Chart */}
        {volSurface && volSurface.strikes.length >= 3 && (
          <VolSurfaceChart surface={volSurface} currentTicker={signal.ticker} />
        )}

        {/* Reasoning */}
        <div>
          <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">Reasoning</div>
          <ul className="space-y-0.5">
            {signal.reasons.map((r, i) => (
              <li key={i} className="text-[10px] text-text-primary flex items-start gap-1.5">
                <span className="text-blue mt-0.5">&#x2022;</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Hedge suggestion */}
        {signal.hedge && (
          <div className="bg-bg rounded p-2 border border-border">
            <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">Hedge</div>
            <div className="text-[10px] font-mono">
              {signal.hedge.direction} {signal.hedge.ticker}
              <span className="text-text-secondary ml-2">corr: {signal.hedge.correlation.toFixed(2)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function EdgeDecaySparkline({ curve }: { curve: DecayPoint[] }) {
  if (curve.length < 2) return null;

  const edges = curve.map((p) => p.edge);
  const absMax = Math.max(...edges.map(Math.abs), 0.01);

  const W = 200;
  const H = 40;
  const PAD = 4;

  const minMinutes = curve[0].minutes;
  const maxMinutes = curve[curve.length - 1].minutes;
  const minuteRange = maxMinutes - minMinutes || 1;

  const points = curve.map((p) => {
    const x = PAD + ((p.minutes - minMinutes) / minuteRange) * (W - PAD * 2);
    const y = H / 2 - (p.edge / absMax) * (H / 2 - PAD);
    return { x, y };
  });

  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ");

  const lastEdge = edges[edges.length - 1];
  const firstEdge = edges[0];
  const persists = Math.abs(lastEdge) > Math.abs(firstEdge) * 0.3;
  const lineColor = persists ? "#00d26a" : "#ff3b3b";

  return (
    <div className="bg-bg rounded border border-border p-1.5">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 40 }}>
        {/* Zero line */}
        <line x1={PAD} y1={H / 2} x2={W - PAD} y2={H / 2} stroke="#1e1e2e" strokeWidth="1" strokeDasharray="2 2" />
        {/* Edge line */}
        <path d={pathD} fill="none" stroke={lineColor} strokeWidth="1.5" />
        {/* Dots */}
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r="2" fill={lineColor} />
        ))}
        {/* Labels */}
        <text x={PAD} y={H - 1} fill="#888899" fontSize="7" fontFamily="JetBrains Mono">-60m</text>
        <text x={W - PAD} y={H - 1} fill="#888899" fontSize="7" fontFamily="JetBrains Mono" textAnchor="end">now</text>
      </svg>
      <div className="text-[8px] text-text-secondary text-center mt-0.5">
        {persists ? "Edge persists (alpha stable)" : "Edge decaying (alpha fading)"}
      </div>
    </div>
  );
}

function VolSurfaceChart({ surface, currentTicker }: { surface: VolSurface; currentTicker: string }) {
  const { strikes, theoretical_mean, theoretical_std, mispricings } = surface;

  const W = 220;
  const H = 80;
  const PAD_L = 30;
  const PAD_R = 10;
  const PAD_T = 5;
  const PAD_B = 20;

  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;

  // X scale: strike values
  const strikeVals = strikes.map((s) => s.strike);
  const minStrike = Math.min(...strikeVals);
  const maxStrike = Math.max(...strikeVals);
  const strikeRange = maxStrike - minStrike || 1;
  const xScale = (v: number) => PAD_L + ((v - minStrike) / strikeRange) * chartW;

  // Y scale: probability
  const maxProb = Math.max(...strikes.map((s) => Math.max(s.market_prob, s.theoretical_prob)), 0.01);
  const yScale = (v: number) => PAD_T + chartH - (v / maxProb) * chartH;

  // Market line
  const marketPath = strikes.map((s, i) => `${i === 0 ? "M" : "L"} ${xScale(s.strike).toFixed(1)} ${yScale(s.market_prob).toFixed(1)}`).join(" ");
  // Theoretical line
  const theoPath = strikes.map((s, i) => `${i === 0 ? "M" : "L"} ${xScale(s.strike).toFixed(1)} ${yScale(s.theoretical_prob).toFixed(1)}`).join(" ");
  // Area under market line
  const areaPath = marketPath + ` L ${xScale(strikeVals[strikeVals.length - 1]).toFixed(1)} ${yScale(0).toFixed(1)} L ${xScale(strikeVals[0]).toFixed(1)} ${yScale(0).toFixed(1)} Z`;

  // Current strike highlight
  const currentStrike = strikes.find((s) => s.ticker === currentTicker);

  return (
    <div>
      <div className="text-[10px] text-text-secondary uppercase tracking-wider mb-1">Implied Distribution</div>
      <div className="bg-bg rounded border border-border p-1.5">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 80 }}>
          {/* Grid lines */}
          {[0, 0.25, 0.5, 0.75, 1.0].filter((v) => v <= maxProb).map((v) => (
            <g key={v}>
              <line x1={PAD_L} y1={yScale(v)} x2={W - PAD_R} y2={yScale(v)} stroke="#1e1e2e" strokeWidth="0.5" />
              <text x={PAD_L - 2} y={yScale(v) + 2} textAnchor="end" fill="#888899" fontSize="6" fontFamily="JetBrains Mono">
                {(v * 100).toFixed(0)}
              </text>
            </g>
          ))}

          {/* Area fill under market curve */}
          <path d={areaPath} fill="#3b82f6" opacity={0.1} />

          {/* Theoretical line (dashed) */}
          <path d={theoPath} fill="none" stroke="#888899" strokeWidth="1" strokeDasharray="3 2" />

          {/* Market line */}
          <path d={marketPath} fill="none" stroke="#3b82f6" strokeWidth="1.5" />

          {/* Strike dots */}
          {strikes.map((s) => {
            const isMispriced = mispricings.some((m) => m.ticker === s.ticker);
            const isCurrent = s.ticker === currentTicker;
            return (
              <circle
                key={s.ticker}
                cx={xScale(s.strike)}
                cy={yScale(s.market_prob)}
                r={isCurrent ? 3 : 2}
                fill={isMispriced ? "#f59e0b" : isCurrent ? "#00d26a" : "#3b82f6"}
                stroke={isCurrent ? "#00d26a" : "none"}
                strokeWidth={isCurrent ? 1 : 0}
              />
            );
          })}

          {/* Current strike vertical line */}
          {currentStrike && (
            <line
              x1={xScale(currentStrike.strike)}
              y1={PAD_T}
              x2={xScale(currentStrike.strike)}
              y2={PAD_T + chartH}
              stroke="#00d26a"
              strokeWidth="0.5"
              strokeDasharray="2 2"
            />
          )}

          {/* Strike labels on x-axis */}
          {strikes.filter((_, i) => i % Math.ceil(strikes.length / 5) === 0 || i === strikes.length - 1).map((s) => (
            <text key={`lbl-${s.strike}`} x={xScale(s.strike)} y={H - 2} textAnchor="middle" fill="#888899" fontSize="5.5" fontFamily="JetBrains Mono">
              {s.strike > 10000 ? `${(s.strike / 1000000).toFixed(1)}M` : s.strike.toFixed(s.strike < 10 ? 2 : 0)}
            </text>
          ))}
        </svg>

        {/* Legend + stats */}
        <div className="flex justify-between text-[8px] text-text-secondary mt-0.5 px-1">
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-0.5"><span className="inline-block w-3 h-0.5 bg-blue" /> Market</span>
            <span className="flex items-center gap-0.5"><span className="inline-block w-3 h-0.5 bg-text-secondary opacity-50" style={{ borderTop: "1px dashed" }} /> Normal</span>
          </div>
          <span className="font-mono">
            {theoretical_std > 10000 ? `\u03BC=${(theoretical_mean / 1e6).toFixed(1)}M` : `\u03BC=${theoretical_mean.toFixed(2)}`}
          </span>
        </div>

        {/* Mispricings */}
        {mispricings.length > 0 && (
          <div className="mt-1 space-y-0.5">
            {mispricings.slice(0, 3).map((mp) => (
              <div key={mp.ticker} className={`text-[8px] px-1.5 py-0.5 rounded ${mp.direction === "OVERPRICED" ? "text-red bg-red/5" : "text-green bg-green/5"}`}>
                {mp.ticker.split("-").pop()}: {mp.direction} by {Math.abs(mp.mispricing * 100).toFixed(0)}c
                <span className="text-text-secondary ml-1">(mkt {(mp.market_prob * 100).toFixed(0)}% vs theo {(mp.theoretical_prob * 100).toFixed(0)}%)</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
