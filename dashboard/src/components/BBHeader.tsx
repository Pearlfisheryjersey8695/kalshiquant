"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";
import type { Alert } from "@/lib/types";
import { useEffect, useState } from "react";

export default function BBHeader() {
  const { wsConnected, positionSummary, signalsMeta, executionStatus, livePnL } = useDashboard();
  const [time, setTime] = useState("");
  const [alertCount, setAlertCount] = useState<Record<string, number>>({});
  const [showAlerts, setShowAlerts] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [benchmarks, setBenchmarks] = useState<Record<string, {ticker: string; price: number; title: string}>>({});

  // Kill switch state
  const [killActive, setKillActive] = useState(false);

  // Drawdown warning
  const [drawdown, setDrawdown] = useState(0);

  useEffect(() => {
    const tick = () => setTime(new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC");
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const fetchCount = () => api.getAlertCount().then(setAlertCount).catch(() => {});
    fetchCount();
    const interval = setInterval(fetchCount, 15000);
    return () => clearInterval(interval);
  }, []);

  // Poll benchmarks every 10s
  useEffect(() => {
    const fetchBenchmarks = () => api.getBenchmarks().then(setBenchmarks).catch(() => {});
    fetchBenchmarks();
    const interval = setInterval(fetchBenchmarks, 10000);
    return () => clearInterval(interval);
  }, []);

  // Poll drawdown from risk engine
  useEffect(() => {
    const fetchDrawdown = () => api.getPortfolioRisk()
      .then(d => setDrawdown(d.max_drawdown_pct || 0))
      .catch(() => {});
    fetchDrawdown();
    const id = setInterval(fetchDrawdown, 10000);
    return () => clearInterval(id);
  }, []);

  const toggleAlerts = () => {
    if (!showAlerts) {
      api.getAlerts(20).then(setAlerts).catch(() => {});
    }
    setShowAlerts(v => !v);
  };

  const toggleKill = async () => {
    try {
      await api.toggleKillSwitch(!killActive);
      setKillActive(!killActive);
      if (!killActive) {
        // Also pause execution engine
        await api.pauseExecution();
      }
    } catch {}
  };

  const heat = positionSummary?.portfolio_heat ?? 0;
  const staleSec = livePnL.lastUpdate > 0 ? Math.floor((Date.now() - livePnL.lastUpdate) / 1000) : 0;

  return (
    <div className={`bb-header ${drawdown > 0.03 ? "border-red-500 animate-pulse" : ""}`}>
      {/* Brand */}
      <span className="text-bb-orange font-semibold text-[14px] tracking-wider">KALSHIQUANT</span>

      {/* Live indicator */}
      <span className="flex items-center gap-1">
        <span className={`w-[6px] h-[6px] ${wsConnected ? "bg-bb-green pulse-live" : "bg-bb-red"}`} />
        <span className={`text-[13px] ${wsConnected ? "text-bb-green" : "text-bb-red"}`}>
          {wsConnected ? "LIVE" : "DISC"}
        </span>
      </span>

      {/* Time */}
      <span className="text-bb-dim text-[11px]">{time}</span>

      {/* Signals */}
      <span className="text-bb-dim text-[13px]">
        SIG: <span className="text-bb-white">{signalsMeta.total_signals}</span>
      </span>

      {/* Engine status */}
      {executionStatus && (
        <span className={`text-[13px] ${executionStatus.paused ? "text-bb-yellow" : "text-bb-green"}`}>
          {executionStatus.paused ? "PAUSED" : "ENGINE"}
        </span>
      )}

      {/* Open positions count */}
      {livePnL.byPosition.length > 0 && (
        <span className="text-bb-dim text-[13px]">
          POS: <span className="text-bb-white">{livePnL.byPosition.length}</span>
        </span>
      )}

      {/* Benchmark references */}
      {benchmarks.BTC && (
        <span className="text-[#888899] text-[13px]">
          BTC: <span className="text-[#ffffff] font-mono">{(benchmarks.BTC.price * 100).toFixed(0)}%</span>
        </span>
      )}
      {benchmarks.FED && (
        <span className="text-[#888899] text-[13px]">
          FED: <span className="text-[#ffffff] font-mono">{(benchmarks.FED.price * 100).toFixed(0)}%</span>
        </span>
      )}

      {/* Spacer */}
      <span className="flex-1" />

      {/* ═══ REAL-TIME P&L — THE DOMINANT ELEMENT ═══ */}
      {/* Computed client-side from WS prices × positions. Updates on every tick. */}
      <div className="flex items-center gap-3">
        {/* Total P&L */}
        <div className="flex items-center gap-1">
          <span className={`font-mono text-[18px] font-bold tracking-tight ${
            livePnL.total > 0 ? "text-[#00ff00]" : livePnL.total < 0 ? "text-[#ff0000]" : "text-[#888899]"
          }`}>
            {livePnL.total >= 0 ? "+" : ""}{livePnL.total.toFixed(2)}
          </span>
          <span className="text-[9px] text-[#555] leading-tight">
            P&L
          </span>
        </div>

        {/* Unrealized / Realized breakdown */}
        {(livePnL.unrealized !== 0 || livePnL.realized !== 0) && (
          <div className="flex flex-col text-[9px] leading-tight font-mono">
            <span className={livePnL.unrealized >= 0 ? "text-[#00aa00]" : "text-[#aa0000]"}>
              U:{livePnL.unrealized >= 0 ? "+" : ""}{livePnL.unrealized.toFixed(2)}
            </span>
            <span className={livePnL.realized >= 0 ? "text-[#00aa00]" : "text-[#aa0000]"}>
              R:{livePnL.realized >= 0 ? "+" : ""}{livePnL.realized.toFixed(2)}
            </span>
          </div>
        )}

        {/* Staleness indicator */}
        <span className={`text-[9px] font-mono ${
          livePnL.isStale ? "text-[#ff0000] animate-pulse" : staleSec > 10 ? "text-[#ffaa00]" : "text-[#333]"
        }`}>
          {staleSec > 0 ? `${staleSec}s` : ""}
        </span>
      </div>

      {/* Heat */}
      <span className="text-[13px]">
        <span className="text-[#555]">HEAT </span>
        <span className={`font-mono font-bold ${heat > 0.35 ? "text-[#ff0000]" : heat > 0.2 ? "text-[#ffaa00]" : "text-[#888]"}`}>
          {(heat * 100).toFixed(0)}%
        </span>
      </span>

      {/* Alert bell */}
      <div className="relative">
        <button onClick={toggleAlerts} className="text-[#888899] hover:text-bb-white text-sm relative">
          &#x1F514;
          {(alertCount.CRITICAL || 0) + (alertCount.WARN || 0) > 0 && (
            <span className={`absolute -top-1 -right-1 w-3.5 h-3.5 rounded-full text-[11px] flex items-center justify-center font-bold ${
              alertCount.CRITICAL ? "bg-red-500 text-white" : "bg-amber-500 text-black"
            }`}>
              {(alertCount.CRITICAL || 0) + (alertCount.WARN || 0)}
            </span>
          )}
        </button>
        {showAlerts && (
          <div className="absolute right-0 top-8 w-80 bg-[#12121a] border border-[#1e1e2e] z-50 max-h-64 overflow-y-auto">
            <div className="px-3 py-2 border-b border-[#1e1e2e] text-[13px] text-bb-orange font-mono">ALERTS</div>
            {alerts.map(a => (
              <div key={a.seq} className={`px-3 py-1.5 border-b border-[#1e1e2e]/50 text-[13px] ${
                a.level === "CRITICAL" ? "bg-red-500/10 text-red-400" : a.level === "WARN" ? "text-amber-400" : "text-[#888899]"
              }`}>
                <span className="font-mono text-[11px] mr-1">[{a.level}]</span>
                {a.message}
                <div className="text-[11px] text-[#888899] mt-0.5">{new Date(a.ts).toLocaleTimeString()}</div>
              </div>
            ))}
            {alerts.length === 0 && <div className="px-3 py-4 text-[13px] text-[#888899] text-center">No recent alerts</div>}
          </div>
        )}
      </div>

      {/* Kill Switch */}
      <button
        onClick={toggleKill}
        className={`px-3 py-1 font-bold text-[13px] tracking-wider transition-all ${
          killActive
            ? "bg-red-600 text-white animate-pulse border-2 border-red-400"
            : "bg-[#1a0000] text-red-500 border border-red-800 hover:bg-red-900/50"
        }`}
      >
        {killActive ? "\u26A0 KILLED" : "KILL"}
      </button>
    </div>
  );
}
