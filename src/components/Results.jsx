import { useState, useMemo } from "react";

export default function Results({ results, onSave, onRunDetail, onSaveTrades, sweptParams }) {
  const [sortCol, setSortCol] = useState("pf");
  const [sortDir, setSortDir] = useState("desc");
  const [sweptOnly, setSweptOnly] = useState(true);
  const [detailData, setDetailData] = useState(null);  // { trades, stats, params }
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedRow, setSelectedRow] = useState(null);

  const getResultVal = (row, key) => {
    if (row[key] !== undefined) return row[key];
    if (row.result) {
      if (row.result[key] !== undefined) return row.result[key];
      const map = { pf: "profit_factor", net: "net_profit", dd_pct: "max_drawdown", wr: "win_rate", trades: "total_trades" };
      const alt = map[key];
      if (alt && row.result[alt] !== undefined) return row.result[alt];
    }
    return 0;
  };

  const sorted = useMemo(() => {
    if (!results) return [];
    return [...results].sort((a, b) => {
      const av = getResultVal(a, sortCol);
      const bv = getResultVal(b, sortCol);
      return sortDir === "desc" ? bv - av : av - bv;
    });
  }, [results, sortCol, sortDir]);

  const toggleSort = (col) => {
    if (sortCol === col) {
      setSortDir(d => d === "desc" ? "asc" : "desc");
    } else {
      setSortCol(col);
      setSortDir("desc");
    }
  };

  const handleRowClick = async (row, idx) => {
    if (!onRunDetail) return;
    if (selectedRow === idx && detailData) {
      // Toggle off
      setSelectedRow(null);
      setDetailData(null);
      return;
    }
    setSelectedRow(idx);
    setDetailLoading(true);
    setDetailData(null);
    const result = await onRunDetail(row.params);
    if (result) {
      setDetailData({ trades: result.trade_log || [], stats: result.stats, params: row.params });
    }
    setDetailLoading(false);
  };

  if (!results || results.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-600">
        <p>No results yet. Run a sweep from the Strategy Lab tab.</p>
      </div>
    );
  }

  const cols = [
    { key: "pf", label: "PF", fmt: v => (v ?? 0).toFixed(2) },
    { key: "net", label: "Net $", fmt: v => (v ?? 0) >= 0 ? `$${(v ?? 0).toFixed(0)}` : `-$${Math.abs(v ?? 0).toFixed(0)}` },
    { key: "dd_pct", label: "Max DD %", fmt: v => (v ?? 0).toFixed(1) + "%" },
    { key: "wr", label: "Win %", fmt: v => (v ?? 0).toFixed(1) + "%" },
    { key: "trades", label: "Trades", fmt: v => v ?? 0 },
  ];

  const allParamKeys = Object.keys(sorted[0]?.params || {});
  const sweptParamSet = new Set(sweptParams || []);
  const displayParamKeys = sweptOnly && sweptParamSet.size > 0
    ? allParamKeys.filter(k => sweptParamSet.has(k))
    : allParamKeys;

  return (
    <div className="p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm text-zinc-400">
          Top {sorted.length} results
        </h2>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 cursor-pointer group">
            <input
              type="checkbox"
              checked={sweptOnly}
              onChange={e => setSweptOnly(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-zinc-600 bg-zinc-800 text-emerald-500 focus:ring-emerald-500/30 cursor-pointer"
            />
            <span className="text-xs text-zinc-500 group-hover:text-zinc-300 transition-colors">Swept params only</span>
          </label>
          <button onClick={onSave}
            className="px-4 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs rounded border border-zinc-700 transition-all">
            Export Sweep CSV
          </button>
        </div>
      </div>

      <div className="overflow-auto" style={{ maxHeight: detailData ? "40vh" : "calc(100vh - 12rem)" }}>
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-zinc-950 z-10">
            <tr className="border-b border-zinc-800">
              <th className="text-left py-2 px-2 text-zinc-600">#</th>
              {cols.map(c => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.key)}
                  className="text-right py-2 px-2 text-zinc-500 cursor-pointer hover:text-zinc-300 transition-colors"
                >
                  {c.label} {sortCol === c.key ? (sortDir === "desc" ? "↓" : "↑") : ""}
                </th>
              ))}
              {displayParamKeys.map(k => (
                <th key={k} className="text-right py-2 px-2 text-zinc-600 font-normal">{k}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr
                key={i}
                onClick={() => handleRowClick(row, i)}
                className={`border-b border-zinc-800/40 cursor-pointer transition-colors ${
                  selectedRow === i ? "bg-emerald-900/20 border-emerald-800/40" : "hover:bg-zinc-900/50"
                }`}
              >
                <td className="py-1.5 px-2 text-zinc-600">{i + 1}</td>
                {cols.map(c => (
                  <td key={c.key} className={`text-right py-1.5 px-2 font-mono ${
                    c.key === "net" ? (getResultVal(row, c.key) >= 0 ? "text-emerald-400" : "text-red-400") :
                    c.key === "pf" && getResultVal(row, c.key) >= 2.0 ? "text-emerald-400" :
                    "text-zinc-300"
                  }`}>
                    {c.fmt(getResultVal(row, c.key))}
                  </td>
                ))}
                {displayParamKeys.map(k => (
                  <td key={k} className="text-right py-1.5 px-2 text-zinc-500 font-mono">
                    {typeof row.params[k] === "number" ?
                      (Number.isInteger(row.params[k]) ? row.params[k] : row.params[k].toFixed(3)) :
                      String(row.params[k] ?? "—")
                    }
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Trade Detail Panel */}
      {detailLoading && (
        <div className="mt-4 p-4 bg-zinc-900 rounded border border-zinc-800 text-center">
          <span className="text-zinc-400 text-sm">Loading trades...</span>
        </div>
      )}
      {detailData && !detailLoading && (
        <div className="mt-4 bg-zinc-900 rounded border border-zinc-800">
          <div className="flex items-center justify-between p-3 border-b border-zinc-800">
            <div className="flex items-center gap-4">
              <span className="text-sm text-zinc-300 font-medium">
                {detailData.trades.length} Trades
              </span>
              {detailData.stats && (
                <span className="text-xs text-zinc-500">
                  PF {detailData.stats.pf?.toFixed(3)} · Net ${detailData.stats.net?.toFixed(0)} · DD {detailData.stats.dd_pct?.toFixed(1)}%
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {onSaveTrades && (
                <button
                  onClick={() => onSaveTrades(detailData.trades)}
                  className="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs rounded border border-zinc-700 transition-all"
                >
                  Export Trades
                </button>
              )}
              <button
                onClick={() => { setDetailData(null); setSelectedRow(null); }}
                className="px-2 py-1 text-zinc-600 hover:text-zinc-400 text-xs transition-colors"
              >✕</button>
            </div>
          </div>
          <div className="overflow-auto" style={{ maxHeight: "35vh" }}>
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-zinc-900 z-10">
                <tr className="border-b border-zinc-800">
                  <th className="text-left py-1.5 px-2 text-zinc-600">#</th>
                  <th className="text-left py-1.5 px-2 text-zinc-600">Dir</th>
                  <th className="text-right py-1.5 px-2 text-zinc-600">Entry</th>
                  <th className="text-right py-1.5 px-2 text-zinc-600">Exit</th>
                  <th className="text-right py-1.5 px-2 text-zinc-600">Qty</th>
                  <th className="text-right py-1.5 px-2 text-zinc-600">Net P&L</th>
                  <th className="text-right py-1.5 px-2 text-zinc-600">Bars</th>
                  <th className="text-left py-1.5 px-2 text-zinc-600">Reason</th>
                  <th className="text-left py-1.5 px-2 text-zinc-600">Entry Time</th>
                  <th className="text-left py-1.5 px-2 text-zinc-600">Exit Time</th>
                </tr>
              </thead>
              <tbody>
                {detailData.trades.map((t, i) => (
                  <tr key={i} className="border-b border-zinc-800/30 hover:bg-zinc-800/30">
                    <td className="py-1 px-2 text-zinc-600">{t.trade_num}</td>
                    <td className={`py-1 px-2 ${t.direction === "long" ? "text-emerald-400" : "text-red-400"}`}>
                      {t.direction}
                    </td>
                    <td className="text-right py-1 px-2 text-zinc-300 font-mono">{t.entry_price?.toFixed(1)}</td>
                    <td className="text-right py-1 px-2 text-zinc-300 font-mono">{t.exit_price?.toFixed(1)}</td>
                    <td className="text-right py-1 px-2 text-zinc-500 font-mono">{t.qty?.toFixed(3)}</td>
                    <td className={`text-right py-1 px-2 font-mono ${t.net_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      ${t.net_pnl?.toFixed(2)}
                    </td>
                    <td className="text-right py-1 px-2 text-zinc-500">{t.bars_held}</td>
                    <td className="py-1 px-2 text-zinc-500">{t.exit_reason || "—"}</td>
                    <td className="py-1 px-2 text-zinc-600 font-mono">{typeof t.entry_time === "number" ? new Date(t.entry_time > 1e12 ? t.entry_time : t.entry_time * 1000).toISOString().replace("T", " ").slice(0, 16) : String(t.entry_time || "").replace("T", " ").replace(".000Z", "")}</td>
                    <td className="py-1 px-2 text-zinc-600 font-mono">{typeof t.exit_time === "number" ? new Date(t.exit_time > 1e12 ? t.exit_time : t.exit_time * 1000).toISOString().replace("T", " ").slice(0, 16) : String(t.exit_time || "").replace("T", " ").replace(".000Z", "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
