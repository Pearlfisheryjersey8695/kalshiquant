"use client";

import { useDashboard } from "@/lib/store";
import { api } from "@/lib/api";
import { fmtPrice } from "@/lib/format";
import { useState, useEffect } from "react";
import type { MarketRisk } from "@/lib/types";

function corrColor(v: number): string {
  if (v >= 0.5) return "#00ff00";
  if (v >= 0.2) return "#007700";
  if (v > -0.2) return "#888800";
  if (v > -0.5) return "#770000";
  return "#ff0000";
}

function signedColor(v: number): string {
  return v > 0 ? "#00ff00" : v < 0 ? "#ff0000" : "#ffffff";
}

export default function BBSignalDetail() {
  const { selectedTicker, signals, markets } = useDashboard();
  const signal = signals.find((s) => s.ticker === selectedTicker);
  const market = markets.find((m) => m.ticker === selectedTicker);
  const [risk, setRisk] = useState<MarketRisk | null>(null);

  useEffect(() => {
    if (!selectedTicker) { setRisk(null); return; }
    api.getMarketRisk(selectedTicker).then(setRisk).catch(() => setRisk(null));
  }, [selectedTicker]);

  if (!selectedTicker) {
    return (
      <div className="flex flex-col h-full">
        <div className="bb-panel-title">SIGNAL DETAIL</div>
        <div className="flex-1 flex items-center justify-center text-bb-dim text-[11px]">
          SELECT A MARKET
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="bb-panel-title flex items-center justify-between">
        <span>SIGNAL DETAIL</span>
        {signal && (
          <span className={signal.direction === "BUY_YES" ? "text-bb-green" : signal.direction === "BUY_NO" ? "text-bb-red" : "text-bb-yellow"}>
            {signal.direction === "BUY_YES" ? "BUY" : signal.direction === "BUY_NO" ? "SELL" : "HOLD"}
          </span>
        )}
      </div>
      <div className="bb-panel-body p-2">
        <div className="bb-kv">
          <KV label="MARKET" value={selectedTicker} />
          <KV label="TITLE" value={market?.title ?? "\u2014"} />
          <KV label="CATEGORY" value={market?.category ?? "\u2014"} />
          <div className="bb-kv-separator" />

          {signal ? (
            <>
              <KV label="DIRECTION" value={signal.direction} color={signal.direction === "BUY_YES" ? "#00ff00" : signal.direction === "BUY_NO" ? "#ff0000" : "#ffff00"} />
              <KV label="FAIR VALUE" value={fmtPrice(signal.fair_value)} />
              <KV label="MKT PRICE" value={fmtPrice(signal.current_price)} />
              <KV label="EDGE" value={`${signal.edge >= 0 ? "+" : ""}${(signal.edge * 100).toFixed(1)}pts`} color={signedColor(signal.edge)} />
              <KV label="NET EDGE" value={`${signal.net_edge >= 0 ? "+" : ""}${(signal.net_edge * 100).toFixed(1)}pts`} color={signedColor(signal.net_edge)} />
              <KV label="FEE IMPACT" value={`${(signal.fee_impact * 100).toFixed(1)}pts`} color="#ff0000" />
              <div className="bb-kv-separator" />

              <KV label="CONFIDENCE" value={`${(signal.confidence * 100).toFixed(1)}%`} />
              <KV label="META QUALITY" value={`${(signal.meta_quality * 100).toFixed(0)}%`} />
              <KV label="REGIME" value={signal.regime} color={
                signal.regime === "TRENDING" ? "#ffff00" :
                signal.regime === "MEAN_REVERTING" ? "#00ff00" :
                signal.regime === "HIGH_VOLATILITY" ? "#ff0000" :
                signal.regime === "CONVERGENCE" ? "#00aaff" : "#888888"
              } />
              <KV label="STRATEGY" value={signal.strategy.toUpperCase()} color="#ff6600" />
              <div className="bb-kv-separator" />

              <KV label="PREDICTION 1H" value={signal.price_prediction_1h > 0 ? "UP" : signal.price_prediction_1h < 0 ? "DOWN" : "FLAT"} color={signal.price_prediction_1h > 0 ? "#00ff00" : signal.price_prediction_1h < 0 ? "#ff0000" : "#ffff00"} />
              <KV label="ML CONFIDENCE" value={`${(signal.prediction_confidence * 100).toFixed(0)}%`} />
              <KV label="CONTRACTS" value={String(signal.recommended_contracts)} />
              <div className="bb-kv-separator" />

              <KV label="STOP LOSS" value={fmtPrice(signal.risk.stop_loss)} color="#ff0000" />
              <KV label="TAKE PROFIT" value={fmtPrice(signal.risk.take_profit)} color="#00ff00" />
              <KV label="MAX LOSS" value={`$${signal.risk.true_max_loss.toFixed(2)}`} color="#ff0000" />
              <KV label="RISK:REWARD" value={signal.risk.risk_reward.toFixed(2)} />
            </>
          ) : (
            <>
              <KV label="MKT PRICE" value={market ? fmtPrice(market.price) : "\u2014"} />
              <KV label="YES BID" value={market ? fmtPrice(market.yes_bid) : "\u2014"} />
              <KV label="YES ASK" value={market ? fmtPrice(market.yes_ask) : "\u2014"} />
              <KV label="VOLUME" value={market ? String(market.volume) : "\u2014"} />
              <div className="bb-kv-separator" />
              <div style={{ gridColumn: "1 / -1" }} className="text-bb-dim text-[10px]">
                NO ACTIVE SIGNAL
              </div>
            </>
          )}

          {/* ── MARKET RISK ──────────────────────────────────────── */}
          <div className="bb-kv-separator" />
          <div style={{ gridColumn: "1 / -1" }} className="border-b border-bb-orange mt-1 mb-1">
            <span className="text-bb-orange text-[10px] font-medium tracking-wider">MARKET RISK</span>
          </div>

          {risk ? (
            <>
              <KV label="VAR (95%)" value={`$${risk.var95.toFixed(2)}`} color="#ff0000" />
              <KV label="VAR (99%)" value={`$${risk.var99.toFixed(2)}`} color="#ff0000" />
              <KV label="MAX LOSS" value={`$${risk.max_loss_1ct.toFixed(2)} (1ct)`} color="#ff0000" />
              <KV label="PROB WIN" value={`${(risk.prob_win * 100).toFixed(1)}%`} color="#00ff00" />
              <KV label="PROB LOSS" value={`${(risk.prob_loss * 100).toFixed(1)}%`} color="#ff0000" />
              <KV label="EXP VALUE" value={`${risk.expected_value >= 0 ? "+" : ""}$${risk.expected_value.toFixed(2)}`} color={signedColor(risk.expected_value)} />
              <div className="bb-kv-separator" />
              <KV label="KELLY %" value={`${risk.kelly_pct.toFixed(1)}%`} />
              <KV label="HALF KELLY %" value={`${risk.half_kelly_pct.toFixed(1)}%`} color="#ff6600" />
              <KV label="SHARPE (7D)" value={risk.sharpe_7d.toFixed(2)} color={signedColor(risk.sharpe_7d)} />
              <KV label="SORTINO (7D)" value={risk.sortino_7d.toFixed(2)} color={signedColor(risk.sortino_7d)} />
              <KV label="MAX DRAWDOWN" value={`$${risk.max_drawdown.toFixed(2)}`} color="#ff0000" />
              <div className="bb-kv-separator" />
              <KV label="CORR (MKT)" value={risk.corr_sp500 >= 0 ? `+${risk.corr_sp500.toFixed(2)}` : risk.corr_sp500.toFixed(2)} color={corrColor(risk.corr_sp500)} />
              <KV label="CORR (BTC)" value={risk.corr_btc >= 0 ? `+${risk.corr_btc.toFixed(2)}` : risk.corr_btc.toFixed(2)} color={corrColor(risk.corr_btc)} />
              <KV label="LIQUIDITY RISK" value={risk.liquidity_risk} color={risk.liquidity_risk === "LOW" ? "#00ff00" : risk.liquidity_risk === "MED" ? "#ffff00" : "#ff0000"} />
            </>
          ) : (
            <div style={{ gridColumn: "1 / -1" }} className="text-bb-dim text-[10px]">LOADING RISK...</div>
          )}

          {/* ── HEDGE SUGGESTION ─────────────────────────────────────── */}
          {signal && signal.hedge && (
            <div style={{ gridColumn: "1 / -1" }} className="mt-3 border-t border-[#1e1e2e] pt-2">
              <div className="text-bb-orange text-[12px] mb-1">HEDGE SUGGESTION</div>
              <div className="text-[13px]">
                <span className={signal.hedge.direction === "BUY_YES" ? "text-bb-green" : "text-bb-red"}>
                  {signal.hedge.direction}
                </span>
                {" "}{signal.hedge.ticker}
                <span className="text-bb-dim ml-2">corr: {signal.hedge.correlation.toFixed(2)}</span>
              </div>
            </div>
          )}

          {/* ── REGIME PROBABILITIES ──────────────────────────────────── */}
          {signal && signal.regime_probs && Object.keys(signal.regime_probs).length > 0 && (
            <div style={{ gridColumn: "1 / -1" }} className="mt-3 border-t border-[#1e1e2e] pt-2">
              <div className="text-bb-orange text-[12px] mb-1">REGIME PROBABILITIES</div>
              {Object.entries(signal.regime_probs)
                .sort(([,a], [,b]) => (b as number) - (a as number))
                .map(([regime, prob]) => (
                  <div key={regime} className="flex items-center gap-2 text-[12px]">
                    <span className="w-20 text-bb-dim">{regime.slice(0, 8)}</span>
                    <div className="flex-1 h-2 bg-[#1a1a1a]">
                      <div className="h-full bg-bb-blue" style={{width: `${(prob as number) * 100}%`}} />
                    </div>
                    <span className="w-10 text-right font-mono">{((prob as number) * 100).toFixed(0)}%</span>
                  </div>
                ))
              }
            </div>
          )}

          {/* ── SENTIMENT ─────────────────────────────────────────────── */}
          {signal && (signal.consensus_edge !== undefined || signal.ai_prob !== undefined) && (
            <div style={{ gridColumn: "1 / -1" }} className="mt-3 border-t border-[#1e1e2e] pt-2">
              <div className="text-bb-orange text-[12px] mb-1">SENTIMENT</div>
              <div className="grid grid-cols-2 gap-x-4 text-[12px]">
                {signal.consensus_prob !== undefined && (
                  <>
                    <span className="text-bb-dim">CONSENSUS PROB</span>
                    <span className="text-right font-mono">{(signal.consensus_prob * 100).toFixed(1)}%</span>
                  </>
                )}
                {signal.consensus_edge !== undefined && (
                  <>
                    <span className="text-bb-dim">CONSENSUS EDGE</span>
                    <span className={`text-right font-mono ${signal.consensus_edge > 0 ? "text-bb-green" : signal.consensus_edge < 0 ? "text-bb-red" : ""}`}>
                      {signal.consensus_edge > 0 ? "+" : ""}{(signal.consensus_edge * 100).toFixed(1)}c
                    </span>
                  </>
                )}
                {signal.ai_prob !== undefined && (
                  <>
                    <span className="text-bb-dim">AI PROB</span>
                    <span className="text-right font-mono">{(signal.ai_prob * 100).toFixed(1)}%</span>
                  </>
                )}
                {signal.ai_reasoning && (
                  <>
                    <span className="text-bb-dim">REASONING</span>
                    <span className="text-right text-[11px]">{signal.ai_reasoning}</span>
                  </>
                )}
              </div>
            </div>
          )}

          {/* ── TIME TO EVENT ─────────────────────────────────────────── */}
          {signal && signal.minutes_to_release !== undefined && signal.minutes_to_release < 9000 && (
            <div style={{ gridColumn: "1 / -1" }} className="mt-2 flex items-center gap-2 text-[12px]">
              <span className="text-bb-orange">EVENT IN</span>
              <span className={`font-mono ${signal.minutes_to_release < 60 ? "text-bb-red" : "text-bb-yellow"}`}>
                {signal.minutes_to_release < 60
                  ? `${signal.minutes_to_release.toFixed(0)}m`
                  : `${(signal.minutes_to_release / 60).toFixed(1)}h`
                }
              </span>
            </div>
          )}

          {/* ── FAIR VALUE WEIGHTS ────────────────────────────────────── */}
          {signal && signal.fv_weights && Object.keys(signal.fv_weights).length > 0 && (
            <div style={{ gridColumn: "1 / -1" }} className="mt-3 border-t border-[#1e1e2e] pt-2">
              <div className="text-bb-orange text-[12px] mb-1">FAIR VALUE WEIGHTS</div>
              {Object.entries(signal.fv_weights).map(([source, weight]) => (
                <div key={source} className="flex justify-between text-[12px]">
                  <span className="text-bb-dim">{source.replace(/_/g, " ").toUpperCase()}</span>
                  <span className="font-mono">{((weight as number) * 100).toFixed(0)}%</span>
                </div>
              ))}
            </div>
          )}

          {/* Reasoning (compact, at bottom) */}
          {signal && signal.reasons.length > 0 && (
            <>
              <div className="bb-kv-separator" />
              <div style={{ gridColumn: "1 / -1" }} className="mt-1">
                <div className="text-bb-orange text-[9px] uppercase mb-1">REASONING</div>
                {signal.reasons.slice(0, 4).map((r, i) => (
                  <div key={i} className="text-[9px] text-bb-dim leading-tight">- {r}</div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function KV({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <>
      <div className="bb-kv-label">{label}</div>
      <div className="bb-kv-value" style={color ? { color } : undefined}>{value}</div>
    </>
  );
}
