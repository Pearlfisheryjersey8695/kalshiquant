"use client";

import { api } from "@/lib/api";
import { useState, useEffect, useCallback } from "react";
import type { Strategy, StrategyParam, StrategyRiskLimits } from "@/lib/types";

const STRAT_TYPES = ["MOMENTUM", "MEAN_REVERSION", "ARBITRAGE", "SENTIMENT", "CUSTOM"];

export default function BBStrategyLab() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const load = useCallback(() => {
    api.getStrategies().then(setStrategies).catch(() => {});
  }, []);

  useEffect(() => { load(); }, [load]);

  const selected = strategies.find(s => s.id === selectedId) ?? null;

  async function handleCreate() {
    const s = await api.createStrategy();
    load();
    setSelectedId(s.id);
  }

  async function handleUpdate(data: Partial<Strategy>) {
    if (!selectedId) return;
    await api.updateStrategy(selectedId, data);
    load();
  }

  async function handleDelete() {
    if (!selectedId) return;
    try {
      await api.deleteStrategy(selectedId);
      setStrategies(prev => prev.filter(s => s.id !== selectedId));
      setSelectedId(null);
      // Sync to execution engine by triggering strategy reload
      await api.getStrategies();
    } catch (e) {
      console.error("Delete failed:", e);
    }
  }

  return (
    <div className="h-full flex" style={{ gap: 1, background: "#1a1a1a" }}>
      {/* LEFT: Strategy List (30%) */}
      <div className="bb-panel flex flex-col" style={{ width: "30%", minWidth: 220 }}>
        <div className="bb-panel-title flex items-center justify-between">
          <span>STRATEGIES</span>
          <div className="flex items-center gap-2">
            {selected && (
              <button
                onClick={() => {
                  window.dispatchEvent(new CustomEvent("switchTab", { detail: "backtest" }));
                }}
                className="px-3 py-1 border border-[#00aaff]/50 text-[#00aaff] text-[10px] hover:bg-[#00aaff]/10"
              >
                BACKTEST STRATEGY
              </button>
            )}
            <button onClick={handleCreate} className="text-bb-orange text-[10px] hover:text-bb-white">+ NEW</button>
          </div>
        </div>
        <div className="bb-panel-body">
          {strategies.length === 0 ? (
            <div className="text-bb-dim text-[10px] p-2">NO STRATEGIES — CLICK + NEW</div>
          ) : (
            strategies.map(s => (
              <div
                key={s.id}
                onClick={() => setSelectedId(s.id)}
                className={`px-2 py-[3px] cursor-pointer text-[10px] border-b border-bb-border ${selectedId === s.id ? "bb-selected" : "hover:bg-bb-row-even"}`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-bb-orange">{s.id}</span>
                  <span className={s.pnl >= 0 ? "text-bb-green" : "text-bb-red"}>
                    {s.pnl >= 0 ? "+" : ""}${s.pnl.toFixed(2)}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-bb-white">{s.name}</span>
                  <span className="flex items-center gap-1">
                    <span className={`w-[5px] h-[5px] ${s.status === "LIVE" ? "bg-bb-green" : s.status === "PAUSED" ? "bg-bb-yellow" : "bg-bb-red"}`} />
                    <span className={s.status === "LIVE" ? "text-bb-green" : s.status === "PAUSED" ? "text-bb-yellow" : "text-bb-red"}>
                      {s.status}
                    </span>
                  </span>
                </div>
                <div className="text-bb-dim text-[9px]">{s.type} | {s.trades_today} trades | WR {(s.win_rate * 100).toFixed(0)}%</div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* RIGHT: Strategy Editor (70%) */}
      <div className="bb-panel flex flex-col flex-1 min-w-0">
        {selected ? (
          <StrategyEditor strategy={selected} onUpdate={handleUpdate} onDelete={handleDelete} />
        ) : (
          <div className="flex-1 flex items-center justify-center text-bb-dim text-[11px]">
            SELECT OR CREATE A STRATEGY
          </div>
        )}
      </div>
    </div>
  );
}

// ── Strategy Editor ──────────────────────────────────────────────────────

type EditorTab = "config" | "parameters" | "signals" | "risk" | "status";

function StrategyEditor({ strategy, onUpdate, onDelete }: {
  strategy: Strategy;
  onUpdate: (data: Partial<Strategy>) => void;
  onDelete: () => void;
}) {
  const [tab, setTab] = useState<EditorTab>("config");
  const TABS: { id: EditorTab; label: string }[] = [
    { id: "config", label: "CONFIG" },
    { id: "parameters", label: "PARAMETERS" },
    { id: "signals", label: "SIGNALS" },
    { id: "risk", label: "RISK LIMITS" },
    { id: "status", label: "STATUS" },
  ];

  return (
    <div className="flex flex-col h-full">
      <div className="bb-panel-title flex items-center justify-between">
        <span>{strategy.id} — {strategy.name}</span>
        <button onClick={onDelete} className="text-bb-red text-[9px] hover:text-bb-white">DELETE</button>
      </div>
      {/* Sub-tabs */}
      <div className="flex border-b border-bb-border shrink-0 bg-[#080808]">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-[3px] text-[10px] border-b ${tab === t.id ? "text-bb-orange border-bb-orange" : "text-bb-dim border-transparent hover:text-bb-white"}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="bb-panel-body p-2">
        {tab === "config" && <ConfigTab strategy={strategy} onUpdate={onUpdate} />}
        {tab === "parameters" && <ParamsTab strategy={strategy} onUpdate={onUpdate} />}
        {tab === "signals" && <SignalsTab strategy={strategy} onUpdate={onUpdate} />}
        {tab === "risk" && <RiskTab strategy={strategy} onUpdate={onUpdate} />}
        {tab === "status" && <StatusTab strategy={strategy} />}
      </div>
    </div>
  );
}

// ── Config Tab ───────────────────────────────────────────────────────────

function ConfigTab({ strategy, onUpdate }: { strategy: Strategy; onUpdate: (d: Partial<Strategy>) => void }) {
  const [name, setName] = useState(strategy.name);
  const [type, setType] = useState(strategy.type);
  const [status, setStatus] = useState(strategy.status);
  const [desc, setDesc] = useState(strategy.description);

  useEffect(() => {
    setName(strategy.name);
    setType(strategy.type);
    setStatus(strategy.status);
    setDesc(strategy.description);
  }, [strategy.id, strategy.name, strategy.type, strategy.status, strategy.description]);

  function save(overrides?: Partial<{ name: string; type: string; status: string; description: string }>) {
    onUpdate({ name, type, status, description: desc, ...overrides });
  }

  return (
    <div className="space-y-2 text-[10px]">
      <Field label="STRATEGY NAME">
        <input value={name} onChange={e => setName(e.target.value)} onBlur={() => save()} className="bb-input w-full" />
      </Field>
      <Field label="TYPE">
        <select value={type} onChange={e => { const newType = e.target.value; setType(newType); save({ type: newType }); }} className="bb-input w-full">
          {STRAT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </Field>
      <Field label="STATUS">
        <div className="flex gap-2">
          {["LIVE", "PAUSED", "STOPPED"].map(s => (
            <button
              key={s}
              onClick={() => { setStatus(s); onUpdate({ status: s }); }}
              className={`px-2 py-[2px] border text-[10px] ${status === s
                ? (s === "LIVE" ? "border-bb-green text-bb-green" : s === "PAUSED" ? "border-bb-yellow text-bb-yellow" : "border-bb-red text-bb-red")
                : "border-bb-border text-bb-dim hover:text-bb-white"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </Field>
      <Field label="DESCRIPTION">
        <textarea value={desc} onChange={e => setDesc(e.target.value)} onBlur={save} rows={3} className="bb-input w-full resize-none" />
      </Field>
    </div>
  );
}

// ── Parameters Tab ───────────────────────────────────────────────────────

function ParamsTab({ strategy, onUpdate }: { strategy: Strategy; onUpdate: (d: Partial<Strategy>) => void }) {
  const params: StrategyParam[] = Array.isArray(strategy.parameters) ? strategy.parameters : [];

  function updateParam(idx: number, field: keyof StrategyParam, val: number | string) {
    const next = [...params];
    next[idx] = { ...next[idx], [field]: typeof val === "string" ? (field === "name" ? val : parseFloat(val) || 0) : val };
    onUpdate({ parameters: next });
  }

  function addParam() {
    onUpdate({ parameters: [...params, { name: "NEW_PARAM", value: 0, min: 0, max: 100 }] });
  }

  return (
    <div className="text-[10px]">
      <table className="bb-table">
        <thead>
          <tr>
            <th>PARAM NAME</th>
            <th style={{ textAlign: "right" }}>VALUE</th>
            <th style={{ textAlign: "right" }}>MIN</th>
            <th style={{ textAlign: "right" }}>MAX</th>
          </tr>
        </thead>
        <tbody>
          {params.map((p, i) => (
            <tr key={i}>
              <td className="text-bb-orange">{p.name}</td>
              <td style={{ textAlign: "right" }}>
                <input
                  type="number"
                  value={p.value}
                  onChange={e => updateParam(i, "value", e.target.value)}
                  className="bb-input w-[60px] text-right text-[10px]"
                  step="any"
                />
              </td>
              <td style={{ textAlign: "right" }} className="text-bb-dim">{p.min}</td>
              <td style={{ textAlign: "right" }} className="text-bb-dim">{p.max}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button onClick={addParam} className="text-bb-orange text-[10px] mt-2 hover:text-bb-white">+ ADD PARAMETER</button>
    </div>
  );
}

// ── Signals Tab ──────────────────────────────────────────────────────────

function SignalsTab({ strategy, onUpdate }: { strategy: Strategy; onUpdate: (d: Partial<Strategy>) => void }) {
  const cfg = strategy.signals_config || {};
  const toggles: { key: string; label: string }[] = [
    { key: "fair_value", label: "FAIR VALUE MODEL" },
    { key: "regime_classifier", label: "REGIME CLASSIFIER" },
    { key: "sentiment_score", label: "SENTIMENT SCORE" },
    { key: "momentum", label: "MOMENTUM INDICATOR" },
    { key: "mean_reversion", label: "MEAN REVERSION" },
    { key: "volume_signal", label: "VOLUME SIGNAL" },
  ];

  function toggle(key: string) {
    const next = { ...cfg, [key]: !cfg[key as keyof typeof cfg] };
    onUpdate({ signals_config: next as Strategy["signals_config"] });
  }

  const weights = cfg.weights || {};
  function updateWeight(key: string, val: string) {
    const next = { ...cfg, weights: { ...weights, [key]: parseFloat(val) || 0 } };
    onUpdate({ signals_config: next as Strategy["signals_config"] });
  }

  return (
    <div className="text-[10px] space-y-3">
      <div className="text-bb-orange text-[9px] border-b border-bb-border pb-[2px]">SIGNAL SOURCES</div>
      {toggles.map(t => (
        <div key={t.key} className="flex items-center justify-between">
          <span className="text-bb-orange">{t.label}</span>
          <button
            onClick={() => toggle(t.key)}
            className={`px-2 py-0 border text-[9px] ${(cfg as unknown as Record<string, unknown>)[t.key] ? "border-bb-green text-bb-green" : "border-bb-dim text-bb-dim"}`}
          >
            {(cfg as unknown as Record<string, unknown>)[t.key] ? "ON" : "OFF"}
          </button>
        </div>
      ))}
      <div className="text-bb-orange text-[9px] border-b border-bb-border pb-[2px] mt-3">SIGNAL WEIGHTS</div>
      {Object.entries(weights).map(([key, val]) => (
        <div key={key} className="flex items-center justify-between">
          <span className="text-bb-orange uppercase">{key.replace(/_/g, " ")} WEIGHT</span>
          <input
            type="number"
            value={val as number}
            onChange={e => updateWeight(key, e.target.value)}
            className="bb-input w-[60px] text-right text-[10px]"
            step="0.05"
            min="0"
            max="2"
          />
        </div>
      ))}
    </div>
  );
}

// ── Risk Limits Tab ──────────────────────────────────────────────────────

const validateRiskLimits = (limits: StrategyRiskLimits): string[] => {
  const errors: string[] = [];
  if (limits.stop_loss_pct <= 0 || limits.stop_loss_pct > 0.5) errors.push("Stop loss must be 0-50%");
  if (limits.take_profit_pct <= 0 || limits.take_profit_pct > 5) errors.push("Take profit must be 0-500%");
  if (limits.kelly_fraction <= 0 || limits.kelly_fraction > 1) errors.push("Kelly fraction must be 0-100%");
  if (limits.min_edge < 0 || limits.min_edge > 0.5) errors.push("Min edge must be 0-50c");
  if (limits.min_confidence < 0 || limits.min_confidence > 1) errors.push("Min confidence must be 0-100%");
  if (limits.max_position_size <= 0) errors.push("Max position size must be positive");
  if (limits.max_daily_loss <= 0) errors.push("Max daily loss must be positive");
  if (limits.max_open_positions <= 0) errors.push("Max open positions must be positive");
  return errors;
};

function RiskTab({ strategy, onUpdate }: { strategy: Strategy; onUpdate: (d: Partial<Strategy>) => void }) {
  const limits = strategy.risk_limits || {};
  const [validationErrors, setValidationErrors] = useState<string[]>([]);

  const fields: { key: string; label: string; prefix?: string; suffix?: string }[] = [
    { key: "max_position_size", label: "MAX POSITION SIZE", prefix: "$" },
    { key: "max_daily_loss", label: "MAX DAILY LOSS", prefix: "$" },
    { key: "max_open_positions", label: "MAX OPEN POSITIONS" },
    { key: "kelly_fraction", label: "KELLY FRACTION" },
    { key: "stop_loss_pct", label: "STOP LOSS", suffix: "%" },
    { key: "take_profit_pct", label: "TAKE PROFIT", suffix: "%" },
    { key: "min_edge", label: "MIN EDGE", suffix: "pts" },
    { key: "min_confidence", label: "MIN CONFIDENCE" },
    { key: "min_tradability", label: "MIN TRADABILITY SCORE" },
  ];

  function updateLimit(key: string, val: string) {
    const next = { ...limits, [key]: parseFloat(val) || 0 } as StrategyRiskLimits;
    setValidationErrors(validateRiskLimits(next));
    onUpdate({ risk_limits: next });
  }

  function handleApply() {
    const errors = validateRiskLimits(strategy.risk_limits);
    setValidationErrors(errors);
    if (errors.length === 0) {
      onUpdate({ risk_limits: strategy.risk_limits });
    }
  }

  return (
    <div className="text-[10px] space-y-1">
      <div className="text-bb-orange text-[9px] border-b border-bb-border pb-[2px]">PER-STRATEGY RISK CONTROLS</div>
      {fields.map(f => (
        <div key={f.key} className="flex items-center justify-between py-[1px]">
          <span className="text-bb-orange">{f.label}</span>
          <div className="flex items-center gap-1">
            {f.prefix && <span className="text-bb-dim">{f.prefix}</span>}
            <input
              type="number"
              value={(limits as unknown as Record<string, number>)[f.key] ?? 0}
              onChange={e => updateLimit(f.key, e.target.value)}
              className="bb-input w-[70px] text-right text-[10px]"
              step="any"
            />
            {f.suffix && <span className="text-bb-dim">{f.suffix}</span>}
          </div>
        </div>
      ))}
      {validationErrors.length > 0 && (
        <div className="text-[#ff0000] text-[11px] border border-[#ff0000]/30 bg-[#ff0000]/5 p-2 mt-2">
          {validationErrors.map((e, i) => <div key={i}>{e}</div>)}
        </div>
      )}
      <div className="mt-3">
        <button
          onClick={handleApply}
          className="px-3 py-[3px] border border-bb-orange text-bb-orange text-[10px] hover:bg-bb-orange/10"
        >
          APPLY RISK LIMITS
        </button>
      </div>
    </div>
  );
}

// ── Status Tab ───────────────────────────────────────────────────────────

function StatusTab({ strategy }: { strategy: Strategy }) {
  return (
    <div className="text-[10px] space-y-2">
      <div className="text-bb-orange text-[9px] border-b border-bb-border pb-[2px]">STRATEGY STATUS</div>
      {/* Real strategy stats */}
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-[#0a0a0f] border border-[#1e1e2e] p-3">
            <div className="text-[11px] text-[#888899] mb-1">TOTAL P&L</div>
            <div className={`font-mono text-lg font-bold ${strategy.pnl >= 0 ? "text-[#00ff00]" : "text-[#ff0000]"}`}>
              {strategy.pnl >= 0 ? "+" : ""}${strategy.pnl.toFixed(2)}
            </div>
          </div>
          <div className="bg-[#0a0a0f] border border-[#1e1e2e] p-3">
            <div className="text-[11px] text-[#888899] mb-1">TRADES TODAY</div>
            <div className="font-mono text-lg font-bold">{strategy.trades_today}</div>
          </div>
          <div className="bg-[#0a0a0f] border border-[#1e1e2e] p-3">
            <div className="text-[11px] text-[#888899] mb-1">WIN RATE</div>
            <div className={`font-mono text-lg font-bold ${strategy.win_rate > 0.5 ? "text-[#00ff00]" : strategy.win_rate > 0 ? "text-[#ffff00]" : "text-[#888899]"}`}>
              {(strategy.win_rate * 100).toFixed(0)}%
            </div>
          </div>
        </div>

        {/* Strategy config summary */}
        <div className="bg-[#0a0a0f] border border-[#1e1e2e] p-3">
          <div className="text-[11px] text-[#ff6600] mb-2">ACTIVE CONFIGURATION</div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-[12px]">
            <div className="flex justify-between">
              <span className="text-[#888899]">TYPE</span>
              <span className="font-mono">{strategy.type.toUpperCase()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#888899]">STATUS</span>
              <span className={`font-mono ${strategy.status === "LIVE" ? "text-[#00ff00]" : "text-[#ffff00]"}`}>{strategy.status.toUpperCase()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#888899]">KELLY</span>
              <span className="font-mono">{(strategy.risk_limits.kelly_fraction * 100).toFixed(0)}%</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#888899]">STOP LOSS</span>
              <span className="font-mono">{(strategy.risk_limits.stop_loss_pct * 100).toFixed(0)}%</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#888899]">TAKE PROFIT</span>
              <span className="font-mono">{(strategy.risk_limits.take_profit_pct * 100).toFixed(0)}%</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#888899]">MIN EDGE</span>
              <span className="font-mono">{(strategy.risk_limits.min_edge * 100).toFixed(1)}c</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#888899]">MAX POSITIONS</span>
              <span className="font-mono">{strategy.risk_limits.max_open_positions}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#888899]">MAX DAILY LOSS</span>
              <span className="font-mono text-[#ff0000]">${strategy.risk_limits.max_daily_loss}</span>
            </div>
          </div>
        </div>

        {/* Timestamps */}
        <div className="text-[11px] text-[#888899] space-y-1">
          <div>Created: {new Date(strategy.created_at).toLocaleString()}</div>
          <div>Updated: {new Date(strategy.updated_at).toLocaleString()}</div>
        </div>
      </div>
    </div>
  );
}

// ── Shared Components ────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-bb-orange text-[9px] mb-[2px]">{label}</div>
      {children}
    </div>
  );
}
