import { useState, useMemo, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";

export default function Results({ results, onSave, dataInfo, sweepStrategy, sweepSettings }) {
  const [sortCol, setSortCol] = useState("pf");
  const [sortDir, setSortDir] = useState("desc");
  const [detailLoading, setDetailLoading] = useState(null); // row index being loaded
  const [tradeLog, setTradeLog] = useState(null); // { rowIndex, stats, trades }

  // Normalize results to flat format
  const normalized = useMemo(() => {
    if (!results || results.length === 0) return [];
    return results.map(row => {
      if (row.pf !== undefined) return row;
      if (row.result) return {
        pf: row.result.profit_factor ?? 0,
        net: row.result.net_profit ?? 0,
        dd: row.result.max_drawdown ?? 0,
        dd_pct: row.result.max_drawdown_pct ?? row.result.max_drawdown ?? 0,
        wr: row.result.win_rate ?? 0,
        trades: row.result.total_trades ?? 0,
        gross_profit: row.result.gross_profit ?? 0,
        gross_loss: row.result.gross_loss ?? 0,
        params: row.params ?? {},
      };
      return row;
    });
  }, [results]);

  // Discover parameter columns dynamically
  const paramKeys = useMemo(() => {
    if (normalized.length === 0) return [];
    const keys = new Set();
    for (const row of normalized) {
      if (row.params) {
        Object.keys(row.params).forEach(k => keys.add(k));
      }
    }
    return [...keys];
  }, [normalized]);

  const sorted = useMemo(() => {
    return [...normalized].sort((a, b) => {
      let av, bv;
      if (paramKeys.includes(sortCol)) {
        av = a.params?.[sortCol] ?? 0;
        bv = b.params?.[sortCol] ?? 0;
      } else {
        av = a[sortCol] ?? 0;
        bv = b[sortCol] ?? 0;
      }
      return sortDir === "desc" ? bv - av : av - bv;
    });
  }, [normalized, sortCol, sortDir, paramKeys]);

  const toggleSort = (col) => {
    if (sortCol === col) {
      setSortDir(d => d === "desc" ? "asc" : "desc");
    } else {
      setSortCol(col);
      setSortDir("desc");
    }
  };

  // Fetch trade log for a specific row
  const fetchTradeLog = useCallback(async (rowIndex) => {
    const row = sorted[rowIndex];
    if (!row) { alert("No row data"); return; }
    if (!dataInfo?.file_path) { alert("No data loaded — load data first in the Data tab"); return; }
    if (!sweepStrategy) { alert("No strategy set — run a sweep first"); return; }

    setDetailLoading(rowIndex);
    try {
      const configJson = JSON.stringify({
        strategy: sweepStrategy,
        data_path: dataInfo.file_path,
        params: row.params || {},
        settings: {
          initial_capital: sweepSettings?.initial_capital || 1000000,
          fee_pct: (sweepSettings?.fee_pct || 0) / 2, // sweep stores RT, engine wants per-side
          warmup_bars: sweepSettings?.warmup_bars || 0,
          fill_on_bar_close: sweepSettings?.fill_on_bar_close || false,
          calc_on_order_fills: sweepSettings?.calc_on_order_fills !== false,
        },
      });

      const result = await invoke("run_python_detail", {
        strategy: sweepStrategy,
        configJson,
      });

      const parsed = JSON.parse(result);
      setTradeLog({ rowIndex, stats: parsed.stats, trades: parsed.trade_log, params: row.params });
    } catch (e) {
      alert("Failed to load trade log: " + e);
    } finally {
      setDetailLoading(null);
    }
  }, [sorted, dataInfo, sweepStrategy, sweepSettings]);

  // Export trade log as CSV
  const exportTradeCSV = useCallback(() => {
    if (!tradeLog?.trades?.length) return;

    const headers = ["#", "Direction", "Entry Price", "Exit Price", "Qty", "Gross P&L", "Fee", "Net P&L", "Bars Held", "Exit Reason", "Entry Time", "Exit Time"];
    const rows = tradeLog.trades.map(t => [
      t.trade_num,
      t.direction,
      t.entry_price,
      t.exit_price,
      t.qty,
      t.gross_pnl,
      t.fee,
      t.net_pnl,
      t.bars_held,
      t.exit_reason,
      t.entry_time ? new Date(t.entry_time).toISOString() : "",
      t.exit_time ? new Date(t.exit_time).toISOString() : "",
    ]);

    const csv = [headers.join(","), ...rows.map(r => r.join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const paramStr = Object.entries(tradeLog.params || {}).map(([k, v]) => `${k}=${v}`).join("_");
    a.download = `trades_${sweepStrategy}_${paramStr}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [tradeLog, sweepStrategy]);

  if (!results || results.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-600">
        <p>No results yet. Run a sweep from the Strategy Lab tab.</p>
      </div>
    );
  }

  const metricCols = [
    { key: "pf", label: "Profit Factor", shortLabel: "PF", fmt: v => (v ?? 0).toFixed(2) },
    { key: "net", label: "Net Profit", shortLabel: "Net $", fmt: v => v >= 0 ? `$${Number(v).toLocaleString()}` : `-$${Math.abs(v).toLocaleString()}` },
    { key: "dd_pct", label: "Max Drawdown %", shortLabel: "DD %", fmt: v => (v ?? 0).toFixed(1) + "%" },
    { key: "wr", label: "Win Rate", shortLabel: "Win %", fmt: v => (v ?? 0).toFixed(1) + "%" },
    { key: "trades", label: "Total Trades", shortLabel: "Trades", fmt: v => v ?? 0 },
    { key: "gross_profit", label: "Gross Profit", shortLabel: "Gross +", fmt: v => v ? `$${Number(v).toLocaleString()}` : "—" },
    { key: "gross_loss", label: "Gross Loss", shortLabel: "Gross -", fmt: v => v ? `-$${Math.abs(v).toLocaleString()}` : "—" },
  ];

  const sortIndicator = (col) => sortCol === col ? (sortDir === "desc" ? " ↓" : " ↑") : "";

  // Prettify param key for display (handles snake_case and camelCase)
  const prettyParam = (k) => {
    // Insert space before uppercase letters (camelCase -> spaced)
    return k
      .replace(/([a-z])([A-Z])/g, '$1 $2')
      .replace(/_/g, " ")
      .replace(/\b\w/g, c => c.toUpperCase());
  };

  return (
    <div className="p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm text-zinc-400">
          Top {sorted.length} results
          {sweepStrategy && <span className="text-zinc-600 ml-2">· {sweepStrategy}</span>}
        </h2>
        <button onClick={onSave}
          className="px-4 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs rounded border border-zinc-700 transition-all">
          Export Summary CSV
        </button>
      </div>

      {/* Trade log panel */}
      {tradeLog && (
        <div className="mb-4 border border-zinc-800 rounded-lg overflow-hidden">
          <div className="bg-zinc-900/60 px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
            <div>
              <span className="text-xs text-zinc-500 tracking-wider">TRADE LOG</span>
              <span className="text-xs text-zinc-600 ml-3">
                Row #{tradeLog.rowIndex + 1} · {tradeLog.trades.length} trades
                · PF {tradeLog.stats.pf} · Net ${Number(tradeLog.stats.net).toLocaleString()}
              </span>
            </div>
            <div className="flex gap-2">
              <button onClick={exportTradeCSV}
                className="px-3 py-1 bg-emerald-600/80 hover:bg-emerald-500 text-white text-xs rounded transition-all">
                Export Trades CSV
              </button>
              <button onClick={() => setTradeLog(null)}
                className="px-3 py-1 bg-zinc-700 hover:bg-zinc-600 text-zinc-300 text-xs rounded transition-all">
                Close
              </button>
            </div>
          </div>
          <div className="max-h-64 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-zinc-900 z-10">
                <tr className="border-b border-zinc-800">
                  <th className="text-left py-1.5 px-2 text-zinc-500">#</th>
                  <th className="text-left py-1.5 px-2 text-zinc-500">Dir</th>
                  <th className="text-right py-1.5 px-2 text-zinc-500">Entry</th>
                  <th className="text-right py-1.5 px-2 text-zinc-500">Exit</th>
                  <th className="text-right py-1.5 px-2 text-zinc-500">Qty</th>
                  <th className="text-right py-1.5 px-2 text-zinc-500">Gross P&L</th>
                  <th className="text-right py-1.5 px-2 text-zinc-500">Fee</th>
                  <th className="text-right py-1.5 px-2 text-zinc-500">Net P&L</th>
                  <th className="text-right py-1.5 px-2 text-zinc-500">Bars</th>
                  <th className="text-left py-1.5 px-2 text-zinc-500">Reason</th>
                </tr>
              </thead>
              <tbody>
                {tradeLog.trades.map((t, i) => (
                  <tr key={i} className="border-b border-zinc-800/30 hover:bg-zinc-900/50">
                    <td className="py-1 px-2 text-zinc-600">{t.trade_num}</td>
                    <td className={`py-1 px-2 ${t.direction === "long" ? "text-emerald-500" : "text-red-400"}`}>
                      {t.direction === "long" ? "LONG" : "SHORT"}
                    </td>
                    <td className="text-right py-1 px-2 text-zinc-400 font-mono">{t.entry_price.toFixed(2)}</td>
                    <td className="text-right py-1 px-2 text-zinc-400 font-mono">{t.exit_price.toFixed(2)}</td>
                    <td className="text-right py-1 px-2 text-zinc-500 font-mono">{t.qty}</td>
                    <td className={`text-right py-1 px-2 font-mono ${t.gross_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      ${t.gross_pnl.toLocaleString()}
                    </td>
                    <td className="text-right py-1 px-2 text-zinc-600 font-mono">${t.fee.toFixed(2)}</td>
                    <td className={`text-right py-1 px-2 font-mono ${t.net_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      ${t.net_pnl.toLocaleString()}
                    </td>
                    <td className="text-right py-1 px-2 text-zinc-500">{t.bars_held}</td>
                    <td className="py-1 px-2 text-zinc-600">{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Main results table */}
      <div className="overflow-auto max-h-[calc(100vh-200px)]">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-zinc-950 z-10">
            <tr className="border-b border-zinc-800">
              <th className="text-left py-2 px-2 text-zinc-600 w-8">#</th>
              <th className="text-center py-2 px-2 text-zinc-600 w-10" title="View trade log">
              </th>
              {metricCols.map(c => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.key)}
                  className="text-right py-2 px-2 text-zinc-500 cursor-pointer hover:text-zinc-300 transition-colors whitespace-nowrap"
                  title={c.label}
                >
                  {c.shortLabel}{sortIndicator(c.key)}
                </th>
              ))}
              {paramKeys.map(k => (
                <th
                  key={k}
                  onClick={() => toggleSort(k)}
                  className="text-right py-2 px-2 text-zinc-600 font-normal whitespace-nowrap cursor-pointer hover:text-zinc-400 transition-colors"
                  title={prettyParam(k)}
                >
                  {prettyParam(k)}{sortIndicator(k)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr key={i} className={`border-b border-zinc-800/40 hover:bg-zinc-900/50 ${
                tradeLog?.rowIndex === i ? "bg-blue-900/20" : ""
              }`}>
                <td className="py-1.5 px-2 text-zinc-600">{i + 1}</td>
                <td className="py-1.5 px-2 text-center">
                  <button
                    onClick={() => fetchTradeLog(i)}
                    disabled={detailLoading !== null}
                    className="px-1.5 py-0.5 text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800 rounded text-xs transition-all disabled:opacity-30"
                    title="View trade log"
                  >
                    {detailLoading === i ? "..." : "📋"}
                  </button>
                </td>
                {metricCols.map(c => (
                  <td key={c.key} className={`text-right py-1.5 px-2 font-mono ${
                    c.key === "net" ? (row[c.key] >= 0 ? "text-emerald-400" : "text-red-400") :
                    c.key === "pf" && row[c.key] >= 2.0 ? "text-emerald-400" :
                    c.key === "gross_profit" ? "text-emerald-400/70" :
                    c.key === "gross_loss" ? "text-red-400/70" :
                    "text-zinc-300"
                  }`}>
                    {c.fmt(row[c.key])}
                  </td>
                ))}
                {paramKeys.map(k => {
                  const v = row.params?.[k];
                  return (
                    <td key={k} className="text-right py-1.5 px-2 text-zinc-500 font-mono whitespace-nowrap">
                      {v === undefined || v === null ? "—" :
                       typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')) :
                       typeof v === "boolean" ? (v ? "✓" : "✗") :
                       String(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
