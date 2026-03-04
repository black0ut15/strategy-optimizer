import { useState, useMemo } from "react";

export default function Results({ results, onSave }) {
  const [sortCol, setSortCol] = useState("profit_factor");
  const [sortDir, setSortDir] = useState("desc");

  const sorted = useMemo(() => {
    if (!results) return [];
    return [...results].sort((a, b) => {
      const av = sortCol.includes(".") ? a.result[sortCol.split(".")[1]] : a.result[sortCol];
      const bv = sortCol.includes(".") ? b.result[sortCol.split(".")[1]] : b.result[sortCol];
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

  if (!results || results.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-600">
        <p>No results yet. Run a sweep from the Strategy Lab tab.</p>
      </div>
    );
  }

  const cols = [
    { key: "profit_factor", label: "PF", fmt: v => v.toFixed(2) },
    { key: "net_profit", label: "Net $", fmt: v => v >= 0 ? `$${v.toFixed(0)}` : `-$${Math.abs(v).toFixed(0)}` },
    { key: "max_drawdown", label: "Max DD %", fmt: v => v.toFixed(2) + "%" },
    { key: "win_rate", label: "Win %", fmt: v => v.toFixed(1) + "%" },
    { key: "total_trades", label: "Trades", fmt: v => v },
  ];

  // Key params to show
  const paramKeys = ["fast_limit", "slow_limit", "sep_threshold", "regime_ema_len", "min_hold_bars",
    "be_trigger_pct", "lock_bars", "lock_retrace_pct", "regime_mode", "trade_direction"];

  return (
    <div className="p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm text-zinc-400">
          Top {sorted.length} results
        </h2>
        <button onClick={onSave}
          className="px-4 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs rounded border border-zinc-700 transition-all">
          Export CSV
        </button>
      </div>

      <div className="overflow-auto">
        <table className="w-full text-xs">
          <thead>
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
              {paramKeys.map(k => (
                <th key={k} className="text-right py-2 px-2 text-zinc-600 font-normal">{k}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr key={i} className="border-b border-zinc-800/40 hover:bg-zinc-900/50">
                <td className="py-1.5 px-2 text-zinc-600">{i + 1}</td>
                {cols.map(c => (
                  <td key={c.key} className={`text-right py-1.5 px-2 font-mono ${
                    c.key === "net_profit" ? (row.result[c.key] >= 0 ? "text-emerald-400" : "text-red-400") :
                    c.key === "profit_factor" && row.result[c.key] >= 2.0 ? "text-emerald-400" :
                    "text-zinc-300"
                  }`}>
                    {c.fmt(row.result[c.key])}
                  </td>
                ))}
                {paramKeys.map(k => (
                  <td key={k} className="text-right py-1.5 px-2 text-zinc-500 font-mono">
                    {typeof row.params[k] === "number" ? 
                      (Number.isInteger(row.params[k]) ? row.params[k] : row.params[k].toFixed(3)) :
                      row.params[k] || "—"
                    }
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
