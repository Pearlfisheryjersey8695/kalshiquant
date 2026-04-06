"use client";

import { api } from "@/lib/api";
import { useState, useEffect } from "react";
import type { PortfolioRisk, CorrelationMatrix, PnlCalendar, EquityCurve } from "@/lib/types";

export default function BBRiskEngine() {
  const [portfolio, setPortfolio] = useState<PortfolioRisk | null>(null);
  const [corr, setCorr] = useState<CorrelationMatrix | null>(null);
  const [calendar, setCalendar] = useState<PnlCalendar | null>(null);
  const [equity, setEquity] = useState<EquityCurve | null>(null);

  useEffect(() => {
    const load = () => {
      api.getPortfolioRisk().then(setPortfolio).catch(() => {});
      api.getRiskCorrelations().then(setCorr).catch(() => {});
      api.getPnlCalendar().then(setCalendar).catch(() => {});
      api.getEquityCurve().then(setEquity).catch(() => {});
    };
    load();
    const id = setInterval(load, 10000); // was 30000
    return () => clearInterval(id);
  }, []);

  return (
    <div className="h-full grid grid-cols-2 grid-rows-2" style={{ gap: 1, background: "#1a1a1a" }}>
      {/* Panel A: P&L Heatmap */}
      <div className="bb-panel flex flex-col overflow-hidden">
        <div className="bb-panel-title">P&L CALENDAR</div>
        <div className="bb-panel-body p-2">
          <PnlHeatmap calendar={calendar} />
        </div>
      </div>
      {/* Panel B: Correlation Matrix */}
      <div className="bb-panel flex flex-col overflow-hidden">
        <div className="bb-panel-title">CORRELATION MATRIX</div>
        <div className="bb-panel-body p-2">
          <CorrMatrix data={corr} />
        </div>
      </div>
      {/* Panel C: Equity Curve */}
      <div className="bb-panel flex flex-col overflow-hidden">
        <div className="bb-panel-title">EQUITY CURVE</div>
        <div className="bb-panel-body p-1">
          <EquityChart data={equity} bankroll={portfolio?.total_capital ?? 10000} />
        </div>
      </div>
      {/* Panel D: Risk Summary */}
      <div className="bb-panel flex flex-col overflow-hidden">
        <div className="bb-panel-title">RISK SUMMARY</div>
        <div className="bb-panel-body p-2">
          <RiskSummary data={portfolio} />
        </div>
      </div>
    </div>
  );
}

// ── P&L Heatmap ─────────────────────────────────────────────────────────

function PnlHeatmap({ calendar }: { calendar: PnlCalendar | null }) {
  if (!calendar || calendar.weeks.length === 0) {
    return <div className="text-bb-dim text-[10px]">NO TRADE DATA — P&L CALENDAR WILL POPULATE AFTER CLOSED POSITIONS</div>;
  }

  const allPnls = calendar.weeks.flat().filter(d => d.has_data).map(d => d.pnl);
  const minPnl = allPnls.reduce((a, b) => Math.min(a, b), 0);
  const maxPnl = allPnls.reduce((a, b) => Math.max(a, b), 0);
  const maxAbs = Math.max(Math.abs(minPnl), Math.abs(maxPnl), 1);

  function cellBg(pnl: number, hasData: boolean): string {
    if (!hasData) return "#111111";
    if (pnl === 0) return "#111111";
    const intensity = Math.min(Math.abs(pnl) / maxAbs, 1);
    if (pnl > 0) {
      const g = Math.round(50 + intensity * 205);
      return `rgb(0, ${g}, 0)`;
    } else {
      const r = Math.round(50 + intensity * 205);
      return `rgb(${r}, 0, 0)`;
    }
  }

  const DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

  return (
    <div>
      {/* Day headers */}
      <div className="grid grid-cols-7 gap-[1px] mb-[2px]">
        {DAYS.map(d => (
          <div key={d} className="text-[8px] text-bb-orange text-center">{d}</div>
        ))}
      </div>
      {/* Week rows */}
      {calendar.weeks.map((week, wi) => (
        <div key={wi} className="grid grid-cols-7 gap-[1px] mb-[1px]">
          {week.map((day, di) => (
            <div
              key={di}
              className="relative flex flex-col items-center justify-center"
              style={{
                background: cellBg(day.pnl, day.has_data),
                border: "1px solid #111111",
                height: 28,
                minWidth: 0,
              }}
              title={`${day.date}: ${day.has_data ? (day.pnl >= 0 ? "+" : "") + "$" + day.pnl.toFixed(2) : "no trades"}`}
            >
              <div className="text-[7px] text-bb-dim">{day.date.slice(8)}</div>
              {day.has_data && (
                <div className="text-[8px] text-bb-white">{day.pnl >= 0 ? "+" : ""}{day.pnl.toFixed(0)}</div>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// ── Correlation Matrix ──────────────────────────────────────────────────

function CorrMatrix({ data }: { data: CorrelationMatrix | null }) {
  if (!data || data.tickers.length === 0) {
    return <div className="text-bb-dim text-[10px]">LOADING CORRELATION DATA...</div>;
  }

  function corrBg(v: number): string {
    const abs = Math.abs(v);
    if (abs >= 0.7) return "rgba(255, 0, 0, 0.25)";    // HIGH correlation = DANGER (red)
    if (abs >= 0.5) return "rgba(255, 165, 0, 0.20)";   // MODERATE = WARNING (orange)
    if (abs >= 0.3) return "rgba(255, 255, 0, 0.10)";   // LOW-MODERATE = CAUTION (yellow)
    return "rgba(0, 255, 0, 0.05)";                       // LOW correlation = DIVERSIFIED (green)
  }

  function corrText(v: number): string {
    const abs = Math.abs(v);
    if (abs >= 0.7) return "#ff4444";
    if (abs >= 0.5) return "#ffaa44";
    if (abs >= 0.3) return "#dddd44";
    return "#ffffff";
  }

  return (
    <div className="overflow-auto">
      <table className="border-collapse text-[9px]" style={{ tableLayout: "fixed" }}>
        <thead>
          <tr>
            <th className="text-bb-orange text-left p-0 w-[120px] sticky left-0 bg-bb-black z-10" />
            {data.indices.map(idx => (
              <th key={idx} className="text-bb-orange text-center p-0 w-[50px] whitespace-nowrap">{idx}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.tickers.map(ticker => (
            <tr key={ticker}>
              <td className="text-bb-orange p-0 pr-1 truncate sticky left-0 bg-bb-black z-10" title={ticker}>
                {ticker.length > 16 ? ticker.slice(0, 16) + "\u2026" : ticker}
              </td>
              {data.indices.map(idx => {
                const v = data.matrix[ticker]?.[idx] ?? 0;
                return (
                  <td
                    key={idx}
                    className="text-center p-0"
                    style={{
                      background: corrBg(v),
                      color: corrText(v),
                      border: "1px solid #111111",
                      height: 22,
                    }}
                    title={`${ticker} vs ${idx}: ${v.toFixed(2)}`}
                  >
                    {v.toFixed(2)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Equity Curve (SVG) ──────────────────────────────────────────────────

function EquityChart({ data, bankroll }: { data: EquityCurve | null; bankroll: number }) {
  if (!data || data.points.length < 2) {
    return (
      <div className="h-full flex items-center justify-center text-bb-dim text-[10px]">
        NO EQUITY DATA — WILL POPULATE AFTER TRADES
      </div>
    );
  }

  const W = 500;
  const H_EQUITY = 120;
  const H_DD = 40;
  const PAD = 2;

  const pts = data.points;
  const dd = data.drawdown;

  const equities = pts.map(p => p.equity);
  const minEq = Math.min(...equities);
  const maxEq = Math.max(...equities);
  const eqRange = maxEq - minEq || 1;

  const toX = (i: number) => PAD + ((W - 2 * PAD) * i) / (pts.length - 1);
  const toY = (v: number) => H_EQUITY - PAD - ((v - minEq) / eqRange) * (H_EQUITY - 2 * PAD);

  const eqLine = pts.map((p, i) => `${toX(i)},${toY(p.equity)}`).join(" ");
  const refY = toY(bankroll);
  const lastEq = equities[equities.length - 1];
  const lineColor = lastEq >= bankroll ? "#ffffff" : "#ff0000";

  // Drawdown bars
  const ddVals = dd.map(d => d.drawdown_pct);
  const minDD = Math.min(...ddVals, 0);
  const maxDD = 0;
  const ddRange = maxDD - minDD || 1;
  const toDDY = (v: number) => H_DD - PAD - ((v - minDD) / ddRange) * (H_DD - 2 * PAD);

  return (
    <div className="h-full flex flex-col">
      {/* Equity */}
      <svg viewBox={`0 0 ${W} ${H_EQUITY}`} className="w-full" style={{ height: H_EQUITY, flex: "none" }}>
        <line x1={PAD} y1={refY} x2={W - PAD} y2={refY} stroke="#ff6600" strokeWidth="0.5" strokeDasharray="4,2" />
        <polyline points={eqLine} fill="none" stroke={lineColor} strokeWidth="1.5" />
        <circle cx={toX(pts.length - 1)} cy={toY(lastEq)} r="2" fill={lineColor} />
        <text x={W - PAD} y={refY - 3} textAnchor="end" fill="#ff6600" fontSize="8" fontFamily="'IBM Plex Mono'">
          START ${bankroll.toFixed(0)}
        </text>
      </svg>
      {/* Drawdown */}
      {dd.length > 1 && (
        <svg viewBox={`0 0 ${W} ${H_DD}`} className="w-full" style={{ height: H_DD, flex: "none" }}>
          <line x1={PAD} y1={toDDY(0)} x2={W - PAD} y2={toDDY(0)} stroke="#1a1a1a" strokeWidth="0.5" />
          {dd.map((d, i) => {
            const x = PAD + ((W - 2 * PAD) * i) / (dd.length - 1);
            const y0 = toDDY(0);
            const y1 = toDDY(d.drawdown_pct);
            const h = Math.abs(y1 - y0);
            return <rect key={i} x={x - 1} y={Math.min(y0, y1)} width={3} height={h} fill="#ff0000" opacity={0.7} />;
          })}
          {minDD < 0 && (
            <>
              <line x1={PAD} y1={toDDY(minDD)} x2={W - PAD} y2={toDDY(minDD)} stroke="#ff0000" strokeWidth="0.5" strokeDasharray="3,2" />
              <text x={W - PAD} y={toDDY(minDD) - 2} textAnchor="end" fill="#ff0000" fontSize="7" fontFamily="'IBM Plex Mono'">
                MAX DD {minDD.toFixed(1)}%
              </text>
            </>
          )}
        </svg>
      )}
    </div>
  );
}

// ── Risk Summary ────────────────────────────────────────────────────────

function RiskSummary({ data }: { data: PortfolioRisk | null }) {
  if (!data) return <div className="text-bb-dim text-[10px]">LOADING...</div>;

  const sc = (v: number) => v > 0 ? "#00ff00" : v < 0 ? "#ff0000" : "#ffffff";

  return (
    <div className="text-[10px] space-y-1">
      {/* Portfolio */}
      <SectionTitle text="PORTFOLIO RISK" />
      <RK label="TOTAL CAPITAL" value={`$${data.total_capital.toLocaleString()}`} />
      <RK label="DEPLOYED" value={`$${data.deployed.toFixed(0)} (${data.deployed_pct.toFixed(1)}%)`} />
      <RK label="CASH" value={`$${data.cash.toFixed(0)}`} />
      <RK label="OPEN P&L" value={`${data.unrealized_pnl >= 0 ? "+" : ""}$${data.unrealized_pnl.toFixed(2)}`} color={sc(data.unrealized_pnl)} />
      <RK label="REALIZED P&L" value={`${data.realized_pnl >= 0 ? "+" : ""}$${data.realized_pnl.toFixed(2)}`} color={sc(data.realized_pnl)} />
      <RK label="TOTAL P&L" value={`${data.total_pnl >= 0 ? "+" : ""}$${data.total_pnl.toFixed(2)}`} color={sc(data.total_pnl)} />

      <SectionTitle text="RISK METRICS" />
      <RK label="VAR (95%)" value={`$${data.var95.toFixed(2)}`} color="#ff0000" />
      <RK label="VAR (99%)" value={`$${data.var99.toFixed(2)}`} color="#ff0000" />
      <RK label="SHARPE" value={data.sharpe.toFixed(4)} color={sc(data.sharpe)} />
      <RK label="SORTINO" value={data.sortino.toFixed(4)} color={sc(data.sortino)} />
      <RK label="CALMAR" value={data.calmar.toFixed(4)} color={sc(data.calmar)} />
      <RK label="MAX DRAWDOWN" value={`$${data.max_drawdown.toFixed(2)} (${data.max_drawdown_pct.toFixed(2)}%)`} color="#ff0000" />
      <RK label="WIN RATE" value={`${(data.win_rate * 100).toFixed(1)}%`} color={data.win_rate >= 0.5 ? "#00ff00" : "#ff0000"} />
      <RK label="PROFIT FACTOR" value={data.profit_factor.toFixed(2)} color={data.profit_factor >= 1 ? "#00ff00" : "#ff0000"} />
      <RK label="AVG WIN" value={`$${data.avg_win.toFixed(2)}`} color="#00ff00" />
      <RK label="AVG LOSS" value={`$${data.avg_loss.toFixed(2)}`} color="#ff0000" />
      <RK label="BEST DAY" value={`+$${data.best_day.toFixed(2)}`} color="#00ff00" />
      <RK label="WORST DAY" value={`$${data.worst_day.toFixed(2)}`} color="#ff0000" />

      {/* Exposure by category */}
      {data.exposure_by_category.length > 0 && (
        <>
          <SectionTitle text="EXPOSURE BY CATEGORY" />
          {data.exposure_by_category.map(cat => (
            <div key={cat.category} className="flex items-center gap-1">
              <span className="text-bb-orange w-[100px] truncate">{cat.category}</span>
              <span className="text-bb-white w-[70px] text-right">${cat.amount.toFixed(0)} ({cat.pct.toFixed(1)}%)</span>
              <div className="flex-1 h-[3px] bg-[#111111] ml-1">
                <div
                  className="h-full"
                  style={{
                    width: `${Math.min(cat.pct, 100)}%`,
                    background: cat.over_limit ? "#ff0000" : "#00ff00",
                  }}
                />
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function SectionTitle({ text }: { text: string }) {
  return <div className="text-bb-orange text-[9px] border-b border-bb-border pt-1 pb-[1px] tracking-wider">{text}</div>;
}

function RK({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-bb-orange">{label}</span>
      <span style={{ color: color ?? "#ffffff" }}>{value}</span>
    </div>
  );
}
