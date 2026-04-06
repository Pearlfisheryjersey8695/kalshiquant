"use client";

import { api } from "@/lib/api";
import { useState, useEffect } from "react";
import type { MorningBrief, NewsItem, MarketContext } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Helpers ─────────────────────────────────────────────────────────────────

function pnlColor(v: number) {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-[#888899]";
}

function pnlSign(v: number) {
  return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
}

function flagBadge(flag: string) {
  const colors: Record<string, string> = {
    edge_decayed: "bg-amber-500/20 text-amber-400 border-amber-500/30",
    signal_dropped: "bg-red-500/20 text-red-400 border-red-500/30",
    expiring_soon: "bg-orange-500/20 text-orange-400 border-orange-500/30",
    large_loss: "bg-red-500/20 text-red-400 border-red-500/30",
  };
  return colors[flag] || "bg-[#1e1e2e] text-[#888899] border-[#2a2a3a]";
}

function dirLabel(d: string) {
  return d === "BUY_YES" ? "YES" : d === "BUY_NO" ? "NO" : "HOLD";
}

function dirColor(d: string) {
  return d === "BUY_YES" ? "text-green-400" : d === "BUY_NO" ? "text-red-400" : "text-[#888899]";
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

// ── Component ───────────────────────────────────────────────────────────────

export default function BBMorningBrief() {
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

  const refreshPipeline = async () => {
    try {
      await fetch(`${API_BASE}/api/pipeline/refresh?mode=light`, { method: "POST" });
      // Re-fetch brief
      api.getMorningBrief().then(setBrief);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    fetchBrief();
    api.getPipelineStatus().then(setPipelineStatus).catch(() => {});
    const interval = setInterval(fetchBrief, 30000); // was 60000
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="h-full bg-[#0a0a0f] flex items-center justify-center">
        <div className="text-[11px] text-[#888899] animate-pulse font-mono">LOADING MORNING BRIEF...</div>
      </div>
    );
  }

  if (error || !brief) {
    return (
      <div className="h-full bg-[#0a0a0f] flex flex-col items-center justify-center gap-3">
        <div className="text-[11px] text-red-400 font-mono">BRIEF UNAVAILABLE</div>
        <div className="text-[9px] text-[#888899]">{error}</div>
        <button onClick={fetchBrief} className="text-[10px] text-bb-orange border border-bb-orange/30 px-3 py-1 hover:bg-bb-orange/10">
          RETRY
        </button>
      </div>
    );
  }

  const portfolio = brief.portfolio;
  const news = brief.news || [];
  const marketContext = brief.market_context || [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const isStale = pipelineStatus && Object.values(pipelineStatus.files || {}).some((f: any) => f.stale);

  return (
    <div className="h-full bg-[#0a0a0f] overflow-y-auto font-mono">
      {/* Data freshness banner */}
      {isStale && (
        <div className="bg-amber-500/10 border-b border-amber-500/30 px-3 py-2 text-[10px] text-amber-400 flex items-center justify-between">
          <span>DATA STALE -- pipeline last ran {pipelineStatus?.last_refresh?.timestamp || "never"}</span>
          <button onClick={refreshPipeline} className="border border-amber-500/30 px-2 py-0.5 hover:bg-amber-500/10">
            REFRESH NOW
          </button>
        </div>
      )}

      {/* Header */}
      <div className="h-[32px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center justify-between px-4 shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-bb-orange text-[11px] font-bold tracking-wider">F6 MORNING BRIEF</span>
          <span className="text-[#888899] text-[10px]">{brief.date}</span>
          <span className="text-[#888899] text-[10px]">{brief.time} UTC</span>
        </div>
        <button onClick={fetchBrief} className="text-[9px] text-[#888899] border border-[#1e1e2e] px-2 py-0.5 hover:text-bb-white hover:border-[#2a2a3a]">
          REFRESH
        </button>
      </div>

      {/* Top stats strip */}
      <div className="grid grid-cols-5 border-b border-[#1e1e2e]">
        <StatCell label="O/N P&L" value={pnlSign(brief.overnight_pnl)} color={pnlColor(brief.overnight_pnl)} />
        <StatCell label="O/N TRADES" value={String(brief.overnight_trades)} color="text-bb-white" />
        <StatCell
          label="BEST TRADE"
          value={brief.biggest_winner ? `${pnlSign(brief.biggest_winner.pnl)} ${brief.biggest_winner.ticker.slice(0, 10)}` : "--"}
          color="text-green-400"
        />
        <StatCell
          label="WORST TRADE"
          value={brief.biggest_loser ? `${pnlSign(brief.biggest_loser.pnl)} ${brief.biggest_loser.ticker.slice(0, 10)}` : "--"}
          color="text-red-400"
        />
        <StatCell
          label="PORTFOLIO"
          value={`Cash $${((portfolio?.bankroll ?? 0) - (portfolio?.total_deployed ?? 0)).toFixed(0)}  Heat ${((portfolio?.portfolio_heat ?? 0) * 100).toFixed(0)}%`}
          color="text-bb-white"
        />
      </div>

      {/* Main two-column layout */}
      <div className="grid grid-cols-2 gap-0">
        {/* Positions at Risk */}
        <div className="border-r border-b border-[#1e1e2e]">
          <SectionHeader title="POSITIONS AT RISK" count={brief.positions_at_risk.length} />
          <div className="max-h-[240px] overflow-y-auto">
            {brief.positions_at_risk.length === 0 ? (
              <div className="px-4 py-6 text-[10px] text-[#888899] text-center">No positions at risk</div>
            ) : (
              <table className="w-full text-[10px]">
                <thead>
                  <tr className="text-[#888899] text-left border-b border-[#1e1e2e]">
                    <th className="px-3 py-1.5 font-normal">TICKER</th>
                    <th className="px-2 py-1.5 font-normal text-right">P&L</th>
                    <th className="px-2 py-1.5 font-normal text-right">EXP</th>
                    <th className="px-2 py-1.5 font-normal">FLAGS</th>
                  </tr>
                </thead>
                <tbody>
                  {brief.positions_at_risk.map((p, i) => (
                    <tr key={i} className="border-b border-[#1e1e2e]/50 hover:bg-[#1e1e2e]/30">
                      <td className="px-3 py-1.5 text-bb-white">{p.ticker.slice(0, 16)}</td>
                      <td className={`px-2 py-1.5 text-right ${pnlColor(p.unrealized_pnl)}`}>
                        {pnlSign(p.unrealized_pnl)}
                      </td>
                      <td className="px-2 py-1.5 text-right text-[#888899]">
                        {p.hours_to_expiry.toFixed(1)}h
                      </td>
                      <td className="px-2 py-1.5">
                        <div className="flex flex-wrap gap-1">
                          {p.risk_flags.map((f, j) => (
                            <span key={j} className={`px-1 py-0.5 text-[8px] border rounded ${flagBadge(f)}`}>
                              {f.replace(/_/g, " ").toUpperCase()}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Top Opportunities */}
        <div className="border-b border-[#1e1e2e]">
          <SectionHeader title="TOP OPPORTUNITIES" count={brief.top_opportunities.length} />
          <div className="max-h-[240px] overflow-y-auto">
            {brief.top_opportunities.length === 0 ? (
              <div className="px-4 py-6 text-[10px] text-[#888899] text-center">No opportunities found</div>
            ) : (
              <table className="w-full text-[10px]">
                <thead>
                  <tr className="text-[#888899] text-left border-b border-[#1e1e2e]">
                    <th className="px-3 py-1.5 font-normal">TICKER</th>
                    <th className="px-2 py-1.5 font-normal text-right">EDGE</th>
                    <th className="px-2 py-1.5 font-normal text-right">CONF</th>
                    <th className="px-2 py-1.5 font-normal">DIR</th>
                    <th className="px-2 py-1.5 font-normal text-right">SIZE</th>
                  </tr>
                </thead>
                <tbody>
                  {brief.top_opportunities.map((o, i) => (
                    <tr key={i} className="border-b border-[#1e1e2e]/50 hover:bg-[#1e1e2e]/30">
                      <td className="px-3 py-1.5 text-bb-white" title={o.title}>{o.ticker.slice(0, 16)}</td>
                      <td className={`px-2 py-1.5 text-right ${o.edge > 0 ? "text-green-400" : "text-red-400"}`}>
                        {(o.edge * 100).toFixed(1)}%
                      </td>
                      <td className="px-2 py-1.5 text-right text-[#888899]">
                        {(o.confidence * 100).toFixed(0)}%
                      </td>
                      <td className={`px-2 py-1.5 ${dirColor(o.direction)}`}>
                        {dirLabel(o.direction)}
                      </td>
                      <td className="px-2 py-1.5 text-right text-[#888899]">
                        {o.recommended_contracts}ct
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Recent Alerts */}
        <div className="border-r border-b border-[#1e1e2e]">
          <SectionHeader title="RECENT ALERTS" count={brief.recent_alerts.length} />
          <div className="max-h-[200px] overflow-y-auto">
            {brief.recent_alerts.length === 0 ? (
              <div className="px-4 py-6 text-[10px] text-[#888899] text-center">No recent alerts</div>
            ) : (
              brief.recent_alerts.map((a, i) => {
                const isError = a.event_type === "ERROR";
                const isSignal = a.event_type === "SIGNAL_CHANGE";
                const isRegime = a.event_type === "REGIME_CHANGE";
                const levelColor = isError ? "text-red-400 bg-red-500/5" :
                  isSignal ? "text-amber-400" : isRegime ? "text-orange-400" : "text-[#888899]";
                const levelTag = isError ? "CRITICAL" : isSignal ? "SIGNAL" : isRegime ? "REGIME" : a.event_type;
                return (
                  <div key={i} className={`px-3 py-1.5 border-b border-[#1e1e2e]/50 text-[10px] ${levelColor}`}>
                    <span className="text-[8px] font-bold mr-1">[{levelTag}]</span>
                    {a.message}
                    <div className="text-[8px] text-[#888899] mt-0.5">{new Date(a.ts).toLocaleTimeString()}</div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Expiring Today */}
        <div className="border-b border-[#1e1e2e]">
          <SectionHeader title="EXPIRING TODAY" count={brief.expiring_today.length} />
          <div className="max-h-[200px] overflow-y-auto">
            {brief.expiring_today.length === 0 ? (
              <div className="px-4 py-6 text-[10px] text-[#888899] text-center">No markets expiring today</div>
            ) : (
              <table className="w-full text-[10px]">
                <thead>
                  <tr className="text-[#888899] text-left border-b border-[#1e1e2e]">
                    <th className="px-3 py-1.5 font-normal">TICKER</th>
                    <th className="px-2 py-1.5 font-normal text-right">PRICE</th>
                    <th className="px-2 py-1.5 font-normal text-right">EXPIRES</th>
                    <th className="px-2 py-1.5 font-normal">POS</th>
                  </tr>
                </thead>
                <tbody>
                  {brief.expiring_today.map((m, i) => (
                    <tr key={i} className="border-b border-[#1e1e2e]/50 hover:bg-[#1e1e2e]/30">
                      <td className="px-3 py-1.5 text-bb-white" title={m.title}>{m.ticker.slice(0, 16)}</td>
                      <td className="px-2 py-1.5 text-right text-[#888899]">{(m.price * 100).toFixed(0)}c</td>
                      <td className="px-2 py-1.5 text-right text-[#888899]">
                        {new Date(m.expiration_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </td>
                      <td className="px-2 py-1.5">
                        {m.has_position ? (
                          <span className="text-amber-400 text-[8px] font-bold">OPEN</span>
                        ) : (
                          <span className="text-[#888899] text-[8px]">--</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {/* ── MARKET NEWS ──────────────────────────────────────────────────── */}
      <div>
        <SectionHeader title="MARKET NEWS" count={news.length} />
        <div className="max-h-[320px] overflow-y-auto">
          {news.length === 0 ? (
            <div className="px-4 py-6 text-[10px] text-[#888899] text-center">No market news available</div>
          ) : (
            <div className="divide-y divide-[#1e1e2e]/50">
              {news.map((item: NewsItem, i: number) => (
                <div key={i} className="px-3 py-2 hover:bg-[#1e1e2e]/20 transition-colors">
                  <div className="flex items-start gap-2">
                    <span className={`px-1.5 py-0.5 text-[8px] border rounded shrink-0 mt-0.5 ${categoryBadgeClass(item.category)}`}>
                      {item.category.toUpperCase()}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        {item.url ? (
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[10px] text-bb-white hover:text-bb-orange truncate block"
                            title={item.title}
                          >
                            {item.title}
                          </a>
                        ) : (
                          <span className="text-[10px] text-bb-white truncate block">{item.title}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[8px] text-[#888899]">{item.source}</span>
                        <span className="text-[8px] text-[#888899]">|</span>
                        <div className="flex items-center gap-1">
                          <div className="w-[40px] h-[3px] bg-[#1e1e2e] rounded-full overflow-hidden">
                            <div
                              className="h-full bg-bb-orange/60 rounded-full"
                              style={{ width: `${item.relevance * 100}%` }}
                            />
                          </div>
                          <span className="text-[8px] text-[#888899]">{item.relevance.toFixed(2)}</span>
                        </div>
                        {item.published && (
                          <>
                            <span className="text-[8px] text-[#888899]">|</span>
                            <span className="text-[8px] text-[#888899]">{item.published}</span>
                          </>
                        )}
                      </div>
                      {item.snippet && (
                        <p className="text-[9px] text-[#888899] mt-1 leading-relaxed">
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

      {/* ── MARKET CONTEXT ───────────────────────────────────────────────── */}
      <div>
        <SectionHeader title="MARKET CONTEXT" count={marketContext.length} />
        <div className="max-h-[200px] overflow-y-auto">
          {marketContext.length === 0 ? (
            <div className="px-4 py-6 text-[10px] text-[#888899] text-center">No market context available</div>
          ) : (
            <div className="grid grid-cols-2 gap-0">
              {marketContext.map((ctx: MarketContext, i: number) => (
                <div key={i} className="px-3 py-2 border-b border-r border-[#1e1e2e]/50 hover:bg-[#1e1e2e]/20">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`px-1.5 py-0.5 text-[8px] border rounded ${categoryBadgeClass(ctx.category)}`}>
                      {ctx.category.toUpperCase()}
                    </span>
                    <span className="text-[9px] text-[#888899]">{ctx.market_count} mkts</span>
                    {ctx.high_conviction_count > 0 && (
                      <span className="text-[8px] text-amber-400">{ctx.high_conviction_count} high-conv</span>
                    )}
                  </div>
                  <div className="text-[10px] text-bb-white">{ctx.summary}</div>
                  <div className="text-[8px] text-[#888899] mt-0.5">Avg vol: {ctx.avg_volume.toLocaleString()}</div>
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
      <div className="text-[8px] text-[#888899] tracking-wider mb-1">{label}</div>
      <div className={`text-[13px] font-bold ${color}`}>{value}</div>
    </div>
  );
}

function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="h-[24px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center justify-between px-3">
      <span className="text-bb-orange text-[9px] tracking-wider font-bold">{title}</span>
      <span className="text-[9px] text-[#888899]">{count}</span>
    </div>
  );
}
