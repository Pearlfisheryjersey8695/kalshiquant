"use client";

import { api } from "@/lib/api";
import { useState, useEffect, useMemo, useCallback } from "react";
import type { JournalEntry, JournalSummary } from "@/lib/types";

// ── Helpers ─────────────────────────────────────────────────────────────────

function pnlColor(v: number) {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-[#888899]";
}

function pnlBg(v: number) {
  return v > 0 ? "bg-green-500/5" : v < 0 ? "bg-red-500/5" : "";
}

function pnlSign(v: number) {
  return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
}

function pctSign(v: number) {
  return v >= 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%`;
}

function dirLabel(d: string) {
  return d === "BUY_YES" ? "YES" : d === "BUY_NO" ? "NO" : "HOLD";
}

function dirColor(d: string) {
  return d === "BUY_YES" ? "text-green-400" : d === "BUY_NO" ? "text-red-400" : "text-[#888899]";
}

function holdTime(mins: number) {
  if (mins < 60) return `${mins.toFixed(0)}m`;
  return `${(mins / 60).toFixed(1)}h`;
}

type SortKey = "entry_time" | "ticker" | "realized_pnl" | "pnl_pct" | "hold_time_minutes" | "edge_at_entry";
type SortDir = "asc" | "desc";

// ── Component ───────────────────────────────────────────────────────────────

export default function BBTradeJournal() {
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filters
  const [filterCategory, setFilterCategory] = useState("");
  const [filterRegime, setFilterRegime] = useState("");
  const [filterStrategy, setFilterStrategy] = useState("");
  const [filterExitReason, setFilterExitReason] = useState("");
  const [filterTicker, setFilterTicker] = useState("");

  // Sort
  const [sortKey, setSortKey] = useState<SortKey>("entry_time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // Expanded rows
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  // Notes editing
  const [editingNote, setEditingNote] = useState<string | null>(null);
  const [noteText, setNoteText] = useState("");
  const [noteSaving, setNoteSaving] = useState(false);

  const fetchData = useCallback(() => {
    const params: Record<string, string> = {};
    if (filterCategory) params.category = filterCategory;
    if (filterRegime) params.regime = filterRegime;
    if (filterStrategy) params.strategy = filterStrategy;
    if (filterTicker) params.ticker = filterTicker;

    Promise.all([
      api.getJournal(Object.keys(params).length > 0 ? params : undefined),
      api.getJournalSummary(),
    ])
      .then(([j, s]) => { setEntries(j); setSummary(s); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filterCategory, filterRegime, filterStrategy, filterTicker]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Derive unique filter options
  const categories = useMemo(() => Array.from(new Set(entries.map(e => e.category).filter(Boolean))), [entries]);
  const regimes = useMemo(() => Array.from(new Set(entries.map(e => e.regime_at_entry).filter(Boolean))), [entries]);
  const strategies = useMemo(() => Array.from(new Set(entries.map(e => e.strategy_at_entry).filter(Boolean))), [entries]);
  const exitReasons = useMemo(() => Array.from(new Set(entries.map(e => e.exit_reason).filter(Boolean))), [entries]);

  // Apply client-side exit reason filter and sort
  const filtered = useMemo(() => {
    let arr = [...entries];
    if (filterExitReason) arr = arr.filter(e => e.exit_reason === filterExitReason);
    arr.sort((a, b) => {
      const va = a[sortKey];
      const vb = b[sortKey];
      if (typeof va === "string" && typeof vb === "string") return sortDir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
      return sortDir === "asc" ? (va as number) - (vb as number) : (vb as number) - (va as number);
    });
    return arr;
  }, [entries, filterExitReason, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("desc"); }
  };

  const sortArrow = (key: SortKey) => sortKey === key ? (sortDir === "asc" ? " \u25B2" : " \u25BC") : "";

  const rowKey = (e: JournalEntry) => `${e.ticker}_${e.entry_time}`;

  const saveNote = async (e: JournalEntry) => {
    setNoteSaving(true);
    try {
      await api.addJournalNote(e.ticker, e.entry_time, noteText);
      // Update local state
      setEntries(prev => prev.map(p =>
        rowKey(p) === rowKey(e) ? { ...p, journal_notes: noteText } : p
      ));
      setEditingNote(null);
    } catch {
      // silent
    } finally {
      setNoteSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="h-full bg-[#0a0a0f] flex items-center justify-center">
        <div className="text-[11px] text-[#888899] animate-pulse font-mono">LOADING TRADE JOURNAL...</div>
      </div>
    );
  }

  if (error && entries.length === 0) {
    return (
      <div className="h-full bg-[#0a0a0f] flex flex-col items-center justify-center gap-3">
        <div className="text-[11px] text-red-400 font-mono">JOURNAL UNAVAILABLE</div>
        <div className="text-[9px] text-[#888899]">{error}</div>
        <button onClick={fetchData} className="text-[10px] text-bb-orange border border-bb-orange/30 px-3 py-1 hover:bg-bb-orange/10">
          RETRY
        </button>
      </div>
    );
  }

  return (
    <div className="h-full bg-[#0a0a0f] flex flex-col overflow-hidden font-mono">
      {/* Header */}
      <div className="h-[32px] bg-[#12121a] border-b border-[#1e1e2e] flex items-center justify-between px-4 shrink-0">
        <span className="text-bb-orange text-[11px] font-bold tracking-wider">TRADE JOURNAL</span>
        <div className="flex items-center gap-2">
          {/* Ticker search */}
          <input
            type="text"
            value={filterTicker}
            onChange={e => setFilterTicker(e.target.value)}
            placeholder="TICKER..."
            className="bg-[#0a0a0f] border border-[#1e1e2e] text-[10px] text-bb-white px-2 py-0.5 w-24 focus:outline-none focus:border-bb-orange/50 placeholder:text-[#888899]"
          />
          <FilterSelect label="CATEGORY" value={filterCategory} options={categories} onChange={setFilterCategory} />
          <FilterSelect label="REGIME" value={filterRegime} options={regimes} onChange={setFilterRegime} />
          <FilterSelect label="STRATEGY" value={filterStrategy} options={strategies} onChange={setFilterStrategy} />
          <FilterSelect label="EXIT" value={filterExitReason} options={exitReasons} onChange={setFilterExitReason} />
        </div>
      </div>

      {/* Summary stats strip */}
      {summary && (
        <div className="grid grid-cols-6 border-b border-[#1e1e2e] shrink-0">
          <MiniStat label="TRADES" value={String(summary.total_trades)} />
          <MiniStat label="WIN%" value={`${(summary.win_rate * 100).toFixed(0)}%`} color={summary.win_rate > 0.5 ? "text-green-400" : "text-red-400"} />
          <MiniStat label="P&L" value={pnlSign(summary.total_pnl)} color={pnlColor(summary.total_pnl)} />
          <MiniStat label="AVG WIN" value={pnlSign(summary.avg_win)} color="text-green-400" />
          <MiniStat label="AVG LOSS" value={pnlSign(summary.avg_loss)} color="text-red-400" />
          <MiniStat label="AVG HOLD" value={holdTime(summary.avg_hold_minutes)} />
        </div>
      )}

      {/* Table */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <table className="w-full text-[10px]">
          <thead className="sticky top-0 bg-[#12121a] z-10">
            <tr className="text-[#888899] text-left border-b border-[#1e1e2e]">
              <th className="px-3 py-1.5 font-normal cursor-pointer hover:text-bb-white" onClick={() => toggleSort("entry_time")}>
                ENTRY{sortArrow("entry_time")}
              </th>
              <th className="px-2 py-1.5 font-normal cursor-pointer hover:text-bb-white" onClick={() => toggleSort("ticker")}>
                TICKER{sortArrow("ticker")}
              </th>
              <th className="px-2 py-1.5 font-normal">DIR</th>
              <th className="px-2 py-1.5 font-normal text-right">ENTRY</th>
              <th className="px-2 py-1.5 font-normal text-right">EXIT</th>
              <th className="px-2 py-1.5 font-normal text-right cursor-pointer hover:text-bb-white" onClick={() => toggleSort("realized_pnl")}>
                P&L{sortArrow("realized_pnl")}
              </th>
              <th className="px-2 py-1.5 font-normal text-right cursor-pointer hover:text-bb-white" onClick={() => toggleSort("pnl_pct")}>
                P&L%{sortArrow("pnl_pct")}
              </th>
              <th className="px-2 py-1.5 font-normal text-right cursor-pointer hover:text-bb-white" onClick={() => toggleSort("hold_time_minutes")}>
                HOLD{sortArrow("hold_time_minutes")}
              </th>
              <th className="px-2 py-1.5 font-normal">STATUS</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((e) => {
              const key = rowKey(e);
              const expanded = expandedRow === key;
              return (
                <TradeRow
                  key={key}
                  entry={e}
                  expanded={expanded}
                  onToggle={() => setExpandedRow(expanded ? null : key)}
                  editingNote={editingNote === key}
                  noteText={noteText}
                  noteSaving={noteSaving}
                  onStartEdit={() => { setEditingNote(key); setNoteText(e.journal_notes || ""); }}
                  onNoteChange={setNoteText}
                  onSaveNote={() => saveNote(e)}
                  onCancelEdit={() => setEditingNote(null)}
                />
              );
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center text-[#888899]">No trades match filters</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function FilterSelect({ label, value, options, onChange }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="bg-[#0a0a0f] border border-[#1e1e2e] text-[9px] text-[#888899] px-1 py-0.5 focus:outline-none focus:border-bb-orange/50 appearance-none cursor-pointer"
    >
      <option value="">{label}</option>
      {options.map(o => <option key={o} value={o}>{o.toUpperCase()}</option>)}
    </select>
  );
}

function MiniStat({ label, value, color = "text-bb-white" }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-[#12121a] border-r border-[#1e1e2e] last:border-r-0 px-3 py-2 text-center">
      <div className="text-[8px] text-[#888899] tracking-wider">{label}</div>
      <div className={`text-[12px] font-bold ${color}`}>{value}</div>
    </div>
  );
}

function TradeRow({ entry: e, expanded, onToggle, editingNote, noteText, noteSaving, onStartEdit, onNoteChange, onSaveNote, onCancelEdit }: {
  entry: JournalEntry;
  expanded: boolean;
  onToggle: () => void;
  editingNote: boolean;
  noteText: string;
  noteSaving: boolean;
  onStartEdit: () => void;
  onNoteChange: (v: string) => void;
  onSaveNote: () => void;
  onCancelEdit: () => void;
}) {
  const entryDate = new Date(e.entry_time);
  const dateStr = `${String(entryDate.getMonth() + 1).padStart(2, "0")}-${String(entryDate.getDate()).padStart(2, "0")} ${String(entryDate.getHours()).padStart(2, "0")}:${String(entryDate.getMinutes()).padStart(2, "0")}`;

  return (
    <>
      <tr
        className={`border-b border-[#1e1e2e]/50 cursor-pointer hover:bg-[#1e1e2e]/30 ${pnlBg(e.realized_pnl)}`}
        onClick={onToggle}
      >
        <td className="px-3 py-1.5 text-[#888899]">{dateStr}</td>
        <td className="px-2 py-1.5 text-bb-white" title={e.title}>{e.ticker.slice(0, 14)}</td>
        <td className={`px-2 py-1.5 ${dirColor(e.direction)}`}>{dirLabel(e.direction)}</td>
        <td className="px-2 py-1.5 text-right text-[#888899]">{e.entry_price.toFixed(2)}</td>
        <td className="px-2 py-1.5 text-right text-[#888899]">{e.exit_price.toFixed(2)}</td>
        <td className={`px-2 py-1.5 text-right font-bold ${pnlColor(e.realized_pnl)}`}>{pnlSign(e.realized_pnl)}</td>
        <td className={`px-2 py-1.5 text-right ${pnlColor(e.pnl_pct)}`}>{pctSign(e.pnl_pct)}</td>
        <td className="px-2 py-1.5 text-right text-[#888899]">{holdTime(e.hold_time_minutes)}</td>
        <td className="px-2 py-1.5">
          <span className="flex items-center gap-1">
            <span className={`text-[8px] ${e.status === "CLOSED" ? "text-[#888899]" : "text-amber-400"}`}>
              {e.status}
            </span>
            {e.regime_changed && <span className="text-amber-400" title="Regime changed">&#x2194;</span>}
          </span>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-[#12121a]">
          <td colSpan={9} className="px-4 py-3 border-b border-[#1e1e2e]">
            <div className="grid grid-cols-4 gap-x-6 gap-y-1 text-[10px] mb-2">
              <span className="text-[#888899]">Edge: <span className="text-bb-white">{(e.edge_at_entry * 100).toFixed(1)}%</span></span>
              <span className="text-[#888899]">Net Edge: <span className="text-bb-white">{(e.net_edge_at_entry * 100).toFixed(1)}%</span></span>
              <span className="text-[#888899]">FV: <span className="text-bb-white">{e.fair_value_at_entry.toFixed(3)}</span></span>
              <span className="text-[#888899]">Conf: <span className="text-bb-white">{(e.confidence_at_entry * 100).toFixed(0)}%</span></span>
              <span className="text-[#888899]">Regime: <span className="text-bb-white">{e.regime_at_entry}{e.regime_changed ? ` \u2192 ${e.regime_at_exit}` : ""}</span></span>
              <span className="text-[#888899]">Strategy: <span className="text-bb-white">{e.strategy_at_entry}</span></span>
              <span className="text-[#888899]">Kelly: <span className="text-bb-white">{e.kelly_fraction_at_entry.toFixed(3)}</span></span>
              <span className="text-[#888899]">Quality: <span className="text-bb-white">{e.meta_quality_at_entry.toFixed(2)}</span></span>
              <span className="text-[#888899]">Contracts: <span className="text-bb-white">{e.contracts}</span></span>
              <span className="text-[#888899]">Fees: <span className="text-bb-white">${e.fees_paid.toFixed(2)}</span></span>
              <span className="text-[#888899]">Exit: <span className="text-bb-white">{e.exit_reason}</span></span>
              <span className="text-[#888899]">Category: <span className="text-bb-white">{e.category}</span></span>
            </div>
            {/* Notes */}
            <div className="border-t border-[#1e1e2e] pt-2 mt-1">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[9px] text-bb-orange tracking-wider">NOTES</span>
                {!editingNote && (
                  <button onClick={(ev) => { ev.stopPropagation(); onStartEdit(); }} className="text-[9px] text-[#888899] hover:text-bb-white">
                    EDIT
                  </button>
                )}
              </div>
              {editingNote ? (
                <div className="flex flex-col gap-1" onClick={ev => ev.stopPropagation()}>
                  <textarea
                    value={noteText}
                    onChange={ev => onNoteChange(ev.target.value)}
                    className="bg-[#0a0a0f] border border-[#1e1e2e] text-[10px] text-bb-white p-2 h-16 resize-none focus:outline-none focus:border-bb-orange/50"
                    placeholder="Add trade notes..."
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={onSaveNote}
                      disabled={noteSaving}
                      className="text-[9px] text-bb-orange border border-bb-orange/30 px-2 py-0.5 hover:bg-bb-orange/10 disabled:opacity-50"
                    >
                      {noteSaving ? "SAVING..." : "SAVE"}
                    </button>
                    <button onClick={onCancelEdit} className="text-[9px] text-[#888899] border border-[#1e1e2e] px-2 py-0.5 hover:text-bb-white">
                      CANCEL
                    </button>
                  </div>
                </div>
              ) : (
                <div className="text-[10px] text-[#888899]">{e.journal_notes || "No notes yet."}</div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
