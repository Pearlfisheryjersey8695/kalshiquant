"use client";

import { api } from "@/lib/api";
import { useDashboard } from "@/lib/store";
import { useState, useEffect, useMemo } from "react";
import type { MorningBrief, NewsItem, MarketContext } from "@/lib/types";

// ── Helpers ─────────────────────────────────────────────────────────────────

function pnlColor(v: number) {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-[#888899]";
}

function pnlSign(v: number) {
  return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
}

const CATEGORY_COLORS: Record<string, string> = {
  Crypto: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  Economics: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  Sports: "bg-green-500/20 text-green-400 border-green-500/30",
  Financials: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  Elections: "bg-red-500/20 text-red-400 border-red-500/30",
  Entertainment: "bg-pink-500/20 text-pink-400 border-pink-500/30",
  Ticker: "bg-amber-500/20 text-amber-400 border-amber-500/30",
};

function categoryBadgeClass(cat: string) {
  return CATEGORY_COLORS[cat] || "bg-[#1e1e2e] text-[#888899] border-[#2a2a3a]";
}

const REGIME_BAR_COLORS: Record<string, string> = {
  CONVERGENCE: "bg-blue-500",
  TRENDING: "bg-green-500",
  MEAN_REVERTING: "bg-amber-500",
  HIGH_VOLATILITY: "bg-red-500",
  STALE: "bg-gray-500",
};

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

// ── Component ───────────────────────────────────────────────────────────────

export default function BBIntel() {
  const { signals } = useDashboard();
  const [brief, setBrief] = useState<MorningBrief | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [pipelineStatus, setPipelineStatus] = useState<any>(null);

  const fetchBrief = () => {
    api.getMorningBrief()
      .then((d) => { setBrief(d); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchBrief();
    api.getPipelineStatus().then(setPipelineStatus).catch(() => {});
    const interval = setInterval(() => {
      fetchBrief();
      api.getPipelineStatus().then(setPipelineStatus).catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  // Regime distribution computed from live signals
  const regimeDist = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of signals) {
      const r = s.regime || "STALE";
      counts[r] = (counts[r] || 0) + 1;
    }
    const total = signals.length || 1;
    return Object.entries(counts)
      .map(([regime, count]) => ({ regime, count, pct: count / total }))
      .sort((a, b) => b.count - a.count);
  }, [signals]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const isStale = pipelineStatus && Object.values(pipelineStatus.files || {}).some((f: any) => f.stale);
  const lastRefresh = pipelineStatus?.last_refresh?.timestamp || "never";

  if (loading) {
    return (
      <div className="h-full bg-[#0a0a0f] flex items-center justify-center">
        <div className="text-[13px] text-[#888899] animate-pulse font-mono">LOADING INTEL...</div>
      </div>
    );
  }

  if (error || !brief) {
    return (
      <div className="h-full bg-[#0a0a0f] flex flex-col items-center justify-center gap-3">
        <div className="text-[13px] text-red-400 font-mono">INTEL UNAVAILABLE</div>
        <div className="text-[11px] text-[#888899]">{error}</div>
        <button onClick={fetchBrief} className="text-[11px] text-bb-orange border border-bb-orange/30 px-3 py-1 hover:bg-bb-orange/10 font-mono">
          RETRY
        </button>
      </div>
    );
  }

  const portfolio = brief.portfolio;
  const news = brief.news || [];
  const marketContext = brief.market_context || [];
  const alerts = brief.recent_alerts || [];
  const expiring = brief.expiring_today || [];
  const cash = (portfolio?.bankroll ?? 0) - (portfolio?.total_deployed ?? 0);
  const heat = (portfolio?.portfolio_heat ?? 0) * 100;

  return (
    <div className="h-full bg-[#0a0a0f] overflow-y-auto font-mono text-[13px]">
      {/* Pipeline freshness banner */}
      {isStale && (
        <div className="bg-amber-500/10 border-b border-amber-500/30 px-3 py-1.5 text-[11px] text-amber-400 flex items-center justify-between">
          <span>DATA STALE -- pipeline last ran {lastRefresh}</span>
          <button
            onClick={() => {
              api.refreshPipeline("light").then(() => fetchBrief()).catch(() => {});
            }}
            className="border border-amber-500/30 px-2 py-0.5 hover:bg-amber-500/10"
          >
            REFRESH NOW
          </button>
        </div>
      )}

      {/* Header */}
      <div className="h-[32px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center justify-between px-4 shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-bb-orange text-[13px] font-bold tracking-wider">F1 INTEL</span>
          <span className="text-[#888899] text-[11px]">{brief.date}</span>
          <span className="text-[#888899] text-[11px]">{brief.time} UTC</span>
          {!isStale && (
            <span className="text-green-400 text-[9px] px-1.5 py-0.5 border border-green-500/30 bg-green-500/10 rounded">
              LIVE
            </span>
          )}
        </div>
        <button onClick={fetchBrief} className="text-[10px] text-[#888899] border border-[#1e1e2e] px-2 py-0.5 hover:text-bb-white hover:border-[#2a2a3a]">
          REFRESH
        </button>
      </div>

      {/* Top stats strip: O/N P&L | TRADES | CASH | HEAT */}
      <div className="grid grid-cols-4 border-b border-[#1e1e2e]">
        <StatCell label="O/N P&L" value={pnlSign(brief.overnight_pnl)} color={pnlColor(brief.overnight_pnl)} />
        <StatCell label="O/N TRADES" value={String(brief.overnight_trades)} color="text-bb-white" />
        <StatCell label="CASH" value={`$${cash.toFixed(0)}`} color="text-bb-white" />
        <StatCell label="HEAT" value={`${heat.toFixed(0)}%`} color={heat > 50 ? "text-red-400" : heat > 30 ? "text-amber-400" : "text-green-400"} />
      </div>

      {/* Main two-column: News | Alerts + Regime */}
      <div className="grid grid-cols-[1fr_340px] min-h-0" style={{ height: "calc(100% - 140px)" }}>
        {/* Left: Market News */}
        <div className="border-r border-[#1e1e2e] flex flex-col overflow-hidden">
          <SectionHeader title="MARKET NEWS" count={news.length} />
          <div className="flex-1 overflow-y-auto">
            {news.length === 0 ? (
              <div className="px-4 py-6 text-[11px] text-[#888899] text-center">No market news available</div>
            ) : (
              <div className="divide-y divide-[#1e1e2e]/50">
                {news.map((item: NewsItem, i: number) => (
                  <div key={i} className="px-3 py-2 hover:bg-[#1e1e2e]/20 transition-colors">
                    <div className="flex items-start gap-2">
                      <span className={`px-1.5 py-0.5 text-[9px] border rounded shrink-0 mt-0.5 ${categoryBadgeClass(item.category)}`}>
                        {item.category.toUpperCase()}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          {item.url ? (
                            <a
                              href={item.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[12px] text-bb-white hover:text-bb-orange truncate block"
                              title={item.title}
                            >
                              {item.title}
                            </a>
                          ) : (
                            <span className="text-[12px] text-bb-white truncate block">{item.title}</span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[9px] text-[#888899]">{item.source}</span>
                          <span className="text-[9px] text-[#888899]">|</span>
                          <div className="flex items-center gap-1">
                            <div className="w-[40px] h-[3px] bg-[#1e1e2e] rounded-full overflow-hidden">
                              <div
                                className="h-full bg-bb-orange/60 rounded-full"
                                style={{ width: `${item.relevance * 100}%` }}
                              />
                            </div>
                            <span className="text-[9px] text-[#888899]">{item.relevance.toFixed(2)}</span>
                          </div>
                          {item.published && (
                            <>
                              <span className="text-[9px] text-[#888899]">|</span>
                              <span className="text-[9px] text-[#888899]">{item.published}</span>
                            </>
                          )}
                        </div>
                        {item.snippet && (
                          <p className="text-[10px] text-[#888899] mt-1 leading-relaxed">
                            {item.snippet.length > 200 ? item.snippet.slice(0, 200) + "..." : item.snippet}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: Alerts + Expiring + Regime */}
        <div className="flex flex-col overflow-hidden">
          {/* Alerts */}
          <SectionHeader title="ALERTS" count={alerts.length} />
          <div className="max-h-[200px] overflow-y-auto border-b border-[#1e1e2e]">
            {alerts.length === 0 ? (
              <div className="px-4 py-4 text-[11px] text-[#888899] text-center">No recent alerts</div>
            ) : (
              alerts.map((a, i) => {
                const isError = a.event_type === "ERROR";
                const isSignal = a.event_type === "SIGNAL_CHANGE";
                const isRegime = a.event_type === "REGIME_CHANGE";
                const levelColor = isError ? "text-red-400 bg-red-500/5" :
                  isSignal ? "text-amber-400" : isRegime ? "text-orange-400" : "text-[#888899]";
                const levelTag = isError ? "CRITICAL" : isSignal ? "SIGNAL" : isRegime ? "REGIME" : a.event_type;
                return (
                  <div key={i} className={`px-3 py-1.5 border-b border-[#1e1e2e]/50 text-[11px] ${levelColor}`}>
                    <span className="text-[9px] font-bold mr-1">[{levelTag}]</span>
                    {a.message}
                    <div className="text-[9px] text-[#888899] mt-0.5">{new Date(a.ts).toLocaleTimeString()}</div>
                  </div>
                );
              })
            )}
          </div>

          {/* Expiring Today */}
          <SectionHeader title="EXPIRING TODAY" count={expiring.length} />
          <div className="max-h-[160px] overflow-y-auto border-b border-[#1e1e2e]">
            {expiring.length === 0 ? (
              <div className="px-4 py-4 text-[11px] text-[#888899] text-center">No markets expiring today</div>
            ) : (
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-[#888899] text-left border-b border-[#1e1e2e]">
                    <th className="px-3 py-1 font-normal">TICKER</th>
                    <th className="px-2 py-1 font-normal text-right">PRICE</th>
                    <th className="px-2 py-1 font-normal text-right">EXPIRES</th>
                    <th className="px-2 py-1 font-normal">POS</th>
                  </tr>
                </thead>
                <tbody>
                  {expiring.map((m, i) => (
                    <tr key={i} className="border-b border-[#1e1e2e]/50 hover:bg-[#1e1e2e]/30">
                      <td className="px-3 py-1 text-bb-white" title={m.title}>{m.ticker.slice(0, 16)}</td>
                      <td className="px-2 py-1 text-right text-[#888899]">{(m.price * 100).toFixed(0)}c</td>
                      <td className="px-2 py-1 text-right text-[#888899]">
                        {new Date(m.expiration_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </td>
                      <td className="px-2 py-1">
                        {m.has_position ? (
                          <span className="text-amber-400 text-[9px] font-bold">OPEN</span>
                        ) : (
                          <span className="text-[#888899] text-[9px]">--</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Regime Overview */}
          <SectionHeader title="REGIME OVERVIEW" count={signals.length} />
          <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
            {regimeDist.length === 0 ? (
              <div className="text-[11px] text-[#888899] text-center py-3">NO SIGNAL DATA</div>
            ) : (
              regimeDist.map(({ regime, count, pct }) => {
                const barColor = REGIME_BAR_COLORS[regime] || "bg-gray-500";
                const short =
                  regime === "MEAN_REVERTING" ? "MEAN_REV" :
                  regime === "HIGH_VOLATILITY" ? "HIGH_VOL" :
                  regime === "CONVERGENCE" ? "CONV" : regime;
                return (
                  <div key={regime}>
                    <div className="flex items-center justify-between text-[11px] mb-0.5">
                      <span className="text-[#e0e0e0]">{short}</span>
                      <span className="text-[#888899]">{count} ({(pct * 100).toFixed(0)}%)</span>
                    </div>
                    <div className="h-[6px] bg-[#1a1a2a] rounded overflow-hidden">
                      <div
                        className={`h-full rounded ${barColor}`}
                        style={{ width: `${clamp(pct * 100, 0, 100)}%` }}
                      />
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* Bottom: Market Context */}
      <div className="border-t border-[#1e1e2e]">
        <SectionHeader title="MARKET CONTEXT" count={marketContext.length} />
        <div className="overflow-x-auto">
          {marketContext.length === 0 ? (
            <div className="px-4 py-4 text-[11px] text-[#888899] text-center">No market context available</div>
          ) : (
            <div className="flex divide-x divide-[#1e1e2e]/50">
              {marketContext.map((ctx: MarketContext, i: number) => (
                <div key={i} className="px-4 py-2 min-w-[200px] hover:bg-[#1e1e2e]/20">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className={`px-1.5 py-0.5 text-[9px] border rounded ${categoryBadgeClass(ctx.category)}`}>
                      {ctx.category.toUpperCase()}
                    </span>
                    <span className="text-[10px] text-[#888899]">{ctx.market_count} mkts</span>
                    {ctx.high_conviction_count > 0 && (
                      <span className="text-[9px] text-amber-400">{ctx.high_conviction_count} hi-conv</span>
                    )}
                  </div>
                  <div className="text-[11px] text-bb-white">{ctx.summary}</div>
                  <div className="text-[9px] text-[#888899] mt-0.5">Avg vol: {ctx.avg_volume.toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function StatCell({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-[#12121a] border-r border-[#1e1e2e] last:border-r-0 px-4 py-3 text-center">
      <div className="text-[9px] text-[#888899] tracking-wider mb-1">{label}</div>
      <div className={`text-[14px] font-bold ${color}`}>{value}</div>
    </div>
  );
}

function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="h-[24px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center justify-between px-3 shrink-0">
      <span className="text-bb-orange text-[10px] tracking-wider font-bold">{title}</span>
      <span className="text-[10px] text-[#888899]">{count}</span>
    </div>
  );
}
