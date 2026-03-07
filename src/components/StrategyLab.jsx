import { useState, useMemo, useCallback, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";

// ============================================================
// Pine Script Parameter Parser
// ============================================================
function parsePineParams(code) {
  const params = [];
  const lines = code.split("\n");

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed.includes("input.")) continue;
    if (trimmed.startsWith("//")) continue;

    // Extract variable name (left of =)
    const assignMatch = trimmed.match(/^(\w+)\s*=\s*input\./);
    if (!assignMatch) continue;
    const varName = assignMatch[1];

    // Determine input type
    let type = null;
    let typeMatch = trimmed.match(/input\.(float|int|bool|string|source)\s*\(/);
    if (!typeMatch) continue;
    type = typeMatch[1];

    // Extract group
    const groupMatch = trimmed.match(/group\s*=\s*(?:grp\w+|"([^"]*)")/);
    let group = "Other";
    if (groupMatch) {
      if (groupMatch[1]) {
        group = groupMatch[1];
      } else {
        // It's a variable reference like grpExit — find the variable definition
        const varRef = trimmed.match(/group\s*=\s*(\w+)/);
        if (varRef) {
          const grpDef = code.match(new RegExp(varRef[1] + '\\s*=\\s*"([^"]*)"'));
          if (grpDef) group = grpDef[1];
        }
      }
    }

    // Extract label (second string argument typically)
    const labelMatch = trimmed.match(/input\.(?:float|int|bool|string|source)\s*\([^,]*,\s*"([^"]*)"/);
    let label = labelMatch ? labelMatch[1] : varName;

    // Extract step
    const stepMatch = trimmed.match(/step\s*=\s*([\d.]+)/);
    const step = stepMatch ? parseFloat(stepMatch[1]) : (type === "int" ? 1 : 0.01);

    // Extract tooltip
    const tipMatch = trimmed.match(/tooltip\s*=\s*"([^"]*)"/);
    const tooltip = tipMatch ? tipMatch[1] : "";

    if (type === "float") {
      const defMatch = trimmed.match(/input\.float\s*\(\s*(-?[\d.]+)/);
      if (!defMatch) continue;
      const def = parseFloat(defMatch[1]);
      params.push({ id: varName, label, type: "float", default: def, value: def, step, group, tooltip, sweep: false, sweepMin: def, sweepMax: def, sweepStep: step });
    }
    else if (type === "int") {
      const defMatch = trimmed.match(/input\.int\s*\(\s*(-?\d+)/);
      if (!defMatch) continue;
      const def = parseInt(defMatch[1]);
      params.push({ id: varName, label, type: "int", default: def, value: def, step, group, tooltip, sweep: false, sweepMin: def, sweepMax: def, sweepStep: step });
    }
    else if (type === "bool") {
      const defMatch = trimmed.match(/input\.bool\s*\(\s*(true|false)/);
      if (!defMatch) continue;
      const def = defMatch[1] === "true";
      params.push({ id: varName, label, type: "bool", default: def, value: def, group, tooltip, sweep: false, testBoth: false });
    }
    else if (type === "string") {
      const defMatch = trimmed.match(/input\.string\s*\(\s*"([^"]*)"/);
      if (!defMatch) continue;
      const def = defMatch[1];
      // Extract options array: ["opt1", "opt2", ...]
      const optionsMatch = trimmed.match(/\[([^\]]+)\]/);
      let options = [def];
      if (optionsMatch) {
        options = optionsMatch[1].match(/"([^"]*)"/g)?.map(s => s.replace(/"/g, '')) || [def];
      }
      params.push({ id: varName, label, type: "string", default: def, value: def, options, group, tooltip, sweep: false });
    }
    else if (type === "source") {
      const defMatch = trimmed.match(/input\.source\s*\(\s*(\w+)/);
      if (!defMatch) continue;
      const sourceOptions = ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"];
      const def = defMatch[1];
      params.push({ id: varName, label, type: "string", default: def, value: def, options: sourceOptions, group, tooltip, sweep: false });
    }
  }

  // Handle special case: posPct has / 100 on the same line
  const posPctParam = params.find(p => p.id === "posPct");
  if (posPctParam) {
    // The Pine code does: input.float(10.0, ...) / 100
    // So the actual input value shown to user is 10.0, not 0.1
    // We keep it as-is since the user sees 10.0 in TV
  }

  return params;
}

// ============================================================
// Combo counting
// ============================================================
function countVals(p) {
  if (!p.sweep) return 1;
  if (p.type === "bool") return p.testBoth ? 2 : 1;
  if (p.type === "string") return p.sweep ? (p.sweepOptions?.length || 1) : 1;
  if (p.sweepStep <= 0) return 1;
  return Math.max(1, Math.floor((p.sweepMax - p.sweepMin) / p.sweepStep + 1.001));
}

// ============================================================
// Components
// ============================================================
function NumInput({ value, onChange, step, disabled, small, className = "" }) {
  return (
    <input
      type="number"
      value={value}
      onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) onChange(v); }}
      step={step}
      disabled={disabled}
      className={`bg-zinc-800 border border-zinc-700 rounded text-zinc-200 text-right
        focus:border-blue-500 focus:outline-none
        disabled:opacity-40 disabled:cursor-not-allowed
        hover:border-zinc-600 transition-colors
        ${small ? "px-1.5 py-1 text-xs" : "px-2.5 py-1.5 text-sm"}
        ${className}`}
    />
  );
}

function SelectInput({ value, onChange, options, className = "" }) {
  const opts = options || [value];
  return (
    <div className="relative">
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className={`bg-zinc-800 border border-zinc-700 rounded px-2.5 py-1.5 text-sm text-zinc-200
          focus:border-blue-500 focus:outline-none hover:border-zinc-600 transition-colors
          cursor-pointer pr-7 ${className}`}
        style={{ appearance: 'none', WebkitAppearance: 'none', MozAppearance: 'none' }}
      >
        {opts.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
      <div className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-zinc-500">
        <svg width="10" height="6" viewBox="0 0 10 6"><path d="M1 1l4 4 4-4" stroke="currentColor" fill="none" strokeWidth="1.5" strokeLinecap="round"/></svg>
      </div>
    </div>
  );
}

function ParamRow({ p, onUpdate }) {
  const cnt = countVals(p);

  // String/select params with multi-select sweep
  if (p.type === "string" || p.type === "str") {
    const hasOptions = p.options && p.options.length > 0;
    const selected = p.sweepOptions || [p.value];
    const sweepCount = p.sweep ? selected.length : 1;
    return (
      <div className="py-2 px-1 border-b border-zinc-800/40">
        <div className="flex items-center justify-between">
          <span className="text-sm text-zinc-300">{p.label}</span>
          {hasOptions ? (
            <SelectInput value={p.value} onChange={v => onUpdate({...p, value: v})} options={p.options} className="min-w-[180px]" />
          ) : (
            <input type="text" value={p.value} onChange={e => onUpdate({...p, value: e.target.value})}
              className="bg-zinc-800 border border-zinc-700 rounded px-2.5 py-1.5 text-sm text-zinc-200 min-w-[180px] text-right focus:border-blue-500 focus:outline-none" />
          )}
        </div>
        {hasOptions && p.options.length > 1 && (
          <div className="mt-1.5">
            <div className="flex items-center gap-2 mb-1.5">
              <button
                onClick={() => {
                  if (!p.sweep) {
                    onUpdate({...p, sweep: true, sweepOptions: [p.value]});
                  } else {
                    onUpdate({...p, sweep: false, sweepOptions: [p.value]});
                  }
                }}
                className={`px-2 py-0.5 rounded text-xs font-medium transition-all shrink-0 ${
                  p.sweep
                    ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/40"
                    : "bg-zinc-800/60 text-zinc-600 border border-zinc-700/60 hover:border-zinc-600 hover:text-zinc-500"
                }`}
              >
                SWEEP
              </button>
              {!p.sweep && <span className="text-xs text-zinc-600 italic">Click to pick options to test</span>}
              {p.sweep && <span className="text-xs font-mono text-amber-400 ml-auto">×{sweepCount}</span>}
            </div>
            {p.sweep && (
              <div className="flex flex-wrap gap-1.5">
                {p.options.map(opt => {
                  const isSelected = selected.includes(opt);
                  return (
                    <button
                      key={opt}
                      onClick={() => {
                        let next;
                        if (isSelected) {
                          next = selected.filter(s => s !== opt);
                          if (next.length === 0) next = [opt]; // must have at least 1
                        } else {
                          next = [...selected, opt];
                        }
                        onUpdate({...p, sweepOptions: next});
                      }}
                      className={`px-2.5 py-1 rounded text-xs transition-all border ${
                        isSelected
                          ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
                          : "bg-zinc-800/60 text-zinc-500 border-zinc-700/60 hover:border-zinc-600 hover:text-zinc-400"
                      }`}
                    >
                      {opt}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  // Source param (not sweepable)
  if (p.type === "source") {
    return (
      <div className="flex items-center justify-between py-2 px-1 border-b border-zinc-800/40">
        <span className="text-sm text-zinc-300">{p.label}</span>
        <span className="text-sm text-zinc-400 bg-zinc-800 px-2.5 py-1.5 rounded border border-zinc-700">{p.value}</span>
      </div>
    );
  }

  // Bool params
  if (p.type === "bool") {
    return (
      <div className="flex items-center justify-between py-2 px-1 border-b border-zinc-800/40">
        <button onClick={() => onUpdate({...p, value: !p.value})} className="flex items-center gap-2.5 group">
          <div className={`w-4 h-4 rounded-sm border flex items-center justify-center transition-all ${
            p.value ? "bg-blue-500 border-blue-500 text-white" : "border-zinc-600 bg-zinc-800/50 text-transparent group-hover:border-zinc-500"
          }`}>
            <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
              <path d="M2.5 6L5 8.5L9.5 3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </div>
          <span className="text-sm text-zinc-300">{p.label}</span>
        </button>
        <button
          onClick={() => onUpdate({...p, sweep: !p.testBoth, testBoth: !p.testBoth})}
          title={p.testBoth ? "Testing ON and OFF — click to fix" : "Click to test ON and OFF"}
          className={`px-2.5 py-1 rounded text-xs transition-all ${
            p.testBoth
              ? "bg-amber-500/20 text-amber-400 border border-amber-500/40"
              : "bg-zinc-800/80 text-zinc-500 border border-zinc-700 hover:text-zinc-400 hover:border-zinc-600"
          }`}
        >
          {p.testBoth ? "Test ON + OFF ×2" : "Fixed"}
        </button>
      </div>
    );
  }

  // Numeric params (float & int) — ALL get SWEEP
  return (
    <div className="py-2 px-1 border-b border-zinc-800/40">
      <div className="flex items-center justify-between">
        <div className="flex flex-col">
          <span className="text-sm text-zinc-300">{p.label}</span>
          {p.tooltip && <span className="text-xs text-zinc-600 mt-0.5 max-w-xs">{p.tooltip}</span>}
        </div>
        <NumInput
          value={p.value}
          onChange={v => onUpdate({...p, value: v})}
          step={p.step}
          className="w-28"
        />
      </div>
      <div className="flex items-center gap-2 mt-1.5">
        <button
          onClick={() => onUpdate({...p, sweep: !p.sweep, sweepMin: !p.sweep ? p.value : p.sweepMin, sweepMax: !p.sweep ? p.value : p.sweepMax})}
          className={`px-2 py-0.5 rounded text-xs font-medium transition-all shrink-0 ${
            p.sweep
              ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/40"
              : "bg-zinc-800/60 text-zinc-600 border border-zinc-700/60 hover:border-zinc-600 hover:text-zinc-500"
          }`}
        >
          SWEEP
        </button>
        {p.sweep ? (
          <div className="flex items-center gap-1.5 flex-1">
            <NumInput value={p.sweepMin} onChange={v => onUpdate({...p, sweepMin: v})} step={p.step} small className="w-20" />
            <span className="text-zinc-600 text-xs">→</span>
            <NumInput value={p.sweepMax} onChange={v => onUpdate({...p, sweepMax: v})} step={p.step} small className="w-20" />
            <span className="text-zinc-600 text-xs">step</span>
            <NumInput value={p.sweepStep} onChange={v => onUpdate({...p, sweepStep: v})} step={p.step} small className="w-16" />
            <span className={`text-xs font-mono ml-auto ${cnt > 1 ? "text-amber-400 font-semibold" : "text-zinc-600"}`}>
              ×{cnt}
            </span>
          </div>
        ) : (
          <span className="text-xs text-zinc-600 italic">Click to set range</span>
        )}
      </div>
    </div>
  );
}

// ============================================================
// Config JSON Generator
// ============================================================
function generateConfig(params, settings) {
  // Flat parameter map for Pine interpreter sweep (keyed by variable name)
  const config = { parameters: {}, sweep_settings: settings };

  for (const p of params) {
    if (p.type === "bool") {
      if (p.testBoth) {
        config.parameters[p.id] = [true, false];
      } else {
        config.parameters[p.id] = [p.value];
      }
    } else if (p.type === "string") {
      if (p.sweep && p.sweepOptions?.length > 0) {
        config.parameters[p.id] = p.sweepOptions;
      } else {
        config.parameters[p.id] = [p.value];
      }
    } else if (p.type === "source") {
      config.parameters[p.id] = [p.value];
    } else {
      // numeric
      if (p.sweep && p.sweepMin !== p.sweepMax) {
        config.parameters[p.id] = [p.sweepMin, p.sweepMax, p.sweepStep];
      } else {
        config.parameters[p.id] = [p.value];
      }
    }
  }

  return JSON.stringify(config, null, 2);
}

// ============================================================
// Main App
// ============================================================
export default function StrategyLab({ onRunSweep, onRunPythonSweep, onLoadData, dataInfo, running, progress, pineCode: externalPineCode, onPineCodeChange }) {
  const [mode, setMode] = useState(() => {
    try { return localStorage.getItem("so_mode") || "python"; } catch { return "python"; }
  });
  const [pineCode, setPineCodeInternal] = useState(externalPineCode || "");
  const [params, setParams] = useState(() => {
    try {
      const s = localStorage.getItem("so_params");
      return s ? JSON.parse(s) : [];
    } catch { return []; }
  });
  const [parsed, setParsed] = useState(() => {
    try {
      const s = localStorage.getItem("so_params");
      const p = s ? JSON.parse(s) : [];
      return p.length > 0;
    } catch { return false; }
  });
  const [settings, setSettings] = useState(() => {
    try {
      const s = localStorage.getItem("so_sweepSettings");
      const defaults = {
        fee_pct: 0, max_dd_pct: 100, min_trades: 0, top_n: 200, sort_by: "pf", output: "results.csv",
        initial_capital: 10000, warmup_bars: 0,
        calc_on_order_fills: true, fill_on_bar_close: false, on_every_tick: true, use_standard_ohlc: false,
      };
      return s ? { ...defaults, ...JSON.parse(s) } : defaults;
    } catch { return {
      fee_pct: 0, max_dd_pct: 100, min_trades: 0, top_n: 200, sort_by: "pf", output: "results.csv",
      initial_capital: 10000, warmup_bars: 0,
      calc_on_order_fills: true, fill_on_bar_close: false, on_every_tick: true, use_standard_ohlc: false,
    }; }
  });
  const [showJson, setShowJson] = useState(false);
  const [copied, setCopied] = useState(false);

  // Python mode state
  const [pyStrategies, setPyStrategies] = useState([]);
  const [selectedStrategy, setSelectedStrategy] = useState(() => {
    try { return localStorage.getItem("so_selectedStrategy") || ""; } catch { return ""; }
  });
  const [pyLoading, setPyLoading] = useState(false);
  const [pyEditing, setPyEditing] = useState(false);
  const [pyEditorCode, setPyEditorCode] = useState("");
  const [pyEditorName, setPyEditorName] = useState("");
  const [translating, setTranslating] = useState(false);
  const [anthropicKey, setAnthropicKey] = useState(() => {
    try { return localStorage.getItem("so_anthropicKey") || ""; } catch { return ""; }
  });

  useEffect(() => {
    try { if (anthropicKey) localStorage.setItem("so_anthropicKey", anthropicKey); } catch {}
  }, [anthropicKey]);

  // Persist mode, strategy, settings
  useEffect(() => {
    try { localStorage.setItem("so_mode", mode); } catch {}
  }, [mode]);
  useEffect(() => {
    try { if (selectedStrategy) localStorage.setItem("so_selectedStrategy", selectedStrategy); } catch {}
  }, [selectedStrategy]);
  useEffect(() => {
    try { localStorage.setItem("so_sweepSettings", JSON.stringify(settings)); } catch {}
  }, [settings]);
  useEffect(() => {
    try { if (params.length > 0) localStorage.setItem("so_params", JSON.stringify(params)); } catch {}
  }, [params]);

  // Load Python strategies on mount (needed for both modes — Pine needs mapping dropdown)
  useEffect(() => {
    invoke("list_python_strategies").then(list => {
      setPyStrategies(list);
      if (list.length > 0 && !selectedStrategy) {
        setSelectedStrategy(list[0].name);
      }
    }).catch(e => console.error("Failed to list strategies:", e));
  }, []);

  // Load Python strategy params when selected
  const loadPythonParams = useCallback(async (name) => {
    if (!name) return;
    setPyLoading(true);
    try {
      const json = await invoke("get_python_params", { strategy: name });
      const paramDefs = JSON.parse(json);

      // Try to load saved params so we can preserve user edits (sweep ranges, values)
      let savedParams = [];
      try {
        const s = localStorage.getItem("so_params");
        if (s) savedParams = JSON.parse(s);
      } catch {}
      const savedMap = {};
      for (const sp of savedParams) savedMap[sp.id] = sp;

      // Convert to same format as Pine parser output, merging saved values
      const converted = paramDefs.map(p => {
        const saved = savedMap[p.id];
        const type = p.type === "str" ? "string" : p.type === "int" ? "int" : p.type === "bool" ? "bool" : "float";
        return {
          id: p.name,
          label: p.name.replace(/_/g, " "),
          type,
          value: saved?.value ?? p.default,
          min: p.min,
          max: p.max,
          step: p.step || (p.type === "int" ? 1 : 0.01),
          group: p.group || "Parameters",
          tooltip: p.tooltip || "",
          options: p.options,
          sweep: saved?.sweep ?? false,
          sweepMin: saved?.sweepMin ?? p.min ?? p.default,
          sweepMax: saved?.sweepMax ?? p.max ?? p.default,
          sweepStep: saved?.sweepStep ?? p.step ?? (p.type === "int" ? 1 : 0.01),
          testBoth: saved?.testBoth ?? false,
          sweepOptions: p.options || [],
        };
      });
      setParams(converted);
      setParsed(true);
      setParamsForStrategy(name);
    } catch (e) {
      console.error("Failed to load params:", e);
      alert("Failed to load strategy params: " + e);
    }
    setPyLoading(false);
  }, []);

  // Track which strategy the current params belong to
  const [paramsForStrategy, setParamsForStrategy] = useState(() => {
    try { return localStorage.getItem("so_paramsForStrategy") || ""; } catch { return ""; }
  });
  useEffect(() => {
    try { if (paramsForStrategy) localStorage.setItem("so_paramsForStrategy", paramsForStrategy); } catch {}
  }, [paramsForStrategy]);

  // When strategy changes and we have params, reload from Python strategy 
  // (ensures param names match the Python engine)
  useEffect(() => {
    if (selectedStrategy && selectedStrategy !== paramsForStrategy) {
      loadPythonParams(selectedStrategy);
    }
  }, [selectedStrategy]);

  // Sync internal state with external prop
  const setPineCode = useCallback((val) => {
    setPineCodeInternal(val);
    if (onPineCodeChange) onPineCodeChange(val);
  }, [onPineCodeChange]);

  // Auto-parse on mount if we have restored pine code but no saved params
  useState(() => {
    if (externalPineCode && params.length === 0) {
      const p = parsePineParams(externalPineCode);
      if (p.length > 0) {
        setParams(p);
        setParsed(true);
      }
    }
  });

  const handleParse = useCallback(() => {
    const p = parsePineParams(pineCode);
    setParams(p);
    setParsed(true);
  }, [pineCode]);

  const handleUpload = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result;
      setPineCode(text);
      const p = parsePineParams(text);
      setParams(p);
      setParsed(true);
    };
    reader.readAsText(file);
  }, []);

  const update = useCallback((u) => setParams(prev => prev.map(p => p.id === u.id ? u : p)), []);

  const totalCombos = useMemo(() => params.length === 0 ? 0 : params.reduce((a, p) => a * countVals(p), 1), [params]);
  const sweepCount = useMemo(() => params.filter(p => p.sweep).length, [params]);
  const estTime = useMemo(() => {
    const s = totalCombos / 1100;
    if (s < 60) return `${Math.max(1, Math.round(s))}s`;
    if (s < 3600) return `${(s / 60).toFixed(1)} min`;
    return `${(s / 3600).toFixed(1)} hr`;
  }, [totalCombos]);

  const configJson = useMemo(() => generateConfig(params, settings), [params, settings]);

  const grouped = useMemo(() => {
    const g = [];
    const seen = new Set();
    for (const p of params) {
      if (!seen.has(p.group)) {
        seen.add(p.group);
        g.push({ name: p.group, params: params.filter(pp => pp.group === p.group) });
      }
    }
    return g;
  }, [params]);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-200" style={{ fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" }}>
      {/* Header */}
      <div className="bg-zinc-900/95 backdrop-blur border-b border-zinc-800 px-5 py-3 flex items-center justify-between sticky top-0 z-20">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-zinc-100">Strategy Optimizer</span>
          {/* Mode toggle */}
          <div className="flex bg-zinc-800 rounded p-0.5 ml-2">
            <button onClick={() => setMode("pine")}
              className={`px-3 py-1 text-xs rounded transition-all ${mode === "pine" ? "bg-zinc-700 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}`}>
              Pine Script
            </button>
            <button onClick={() => setMode("python")}
              className={`px-3 py-1 text-xs rounded transition-all ${mode === "python" ? "bg-zinc-700 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}`}>
              Python
            </button>
          </div>
          {parsed && <span className="text-xs text-zinc-500 border-l border-zinc-700 pl-3">{params.length} parameters</span>}
        </div>
        {parsed && (
          <div className="flex items-center gap-5 text-xs">
            <div><span className="text-zinc-500">Sweeping </span><span className="text-emerald-400 font-bold">{sweepCount}</span></div>
            <div className="h-3 w-px bg-zinc-700" />
            <div>
              <span className="text-zinc-500">Combos </span>
              <span className={`font-bold ${totalCombos > 10_000_000 ? "text-red-400" : totalCombos > 1_000_000 ? "text-amber-400" : "text-emerald-400"}`}>
                {totalCombos.toLocaleString()}
              </span>
            </div>
            <div className="h-3 w-px bg-zinc-700" />
            <div><span className="text-zinc-500">Est </span><span className="text-zinc-200 font-bold">{estTime}</span></div>
          </div>
        )}
      </div>

      <div className="max-w-xl mx-auto py-4 px-2">
        {/* Upload / Paste — Pine mode */}
        {!parsed && mode === "pine" && (
          <div>
            <div className="border border-zinc-800 rounded-lg overflow-hidden">
              <div className="bg-zinc-900/60 px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
                <span className="text-xs text-zinc-500 tracking-wider">PINE SCRIPT INPUT</span>
                <div className="flex items-center gap-2">
                  {pineCode.trim() && (
                    <button
                      onClick={() => { setPineCode(""); }}
                      className="px-3 py-1 bg-zinc-800 hover:bg-red-900/50 hover:text-red-300 rounded text-xs text-zinc-500 transition-all"
                      title="Clear Pine Script"
                    >
                      Clear
                    </button>
                  )}
                  <label className="cursor-pointer px-3 py-1 bg-zinc-800 hover:bg-zinc-700 rounded text-xs text-zinc-300 transition-all">
                    Upload .pine file
                    <input type="file" accept=".pine,.txt,.ps" onChange={handleUpload} className="hidden" />
                  </label>
                </div>
              </div>
              <textarea
                value={pineCode}
                onChange={e => setPineCode(e.target.value)}
                placeholder="Paste your Pine Script strategy here, or upload a file..."
                className="w-full h-64 bg-zinc-950 text-zinc-300 text-xs p-4 resize-none focus:outline-none placeholder-zinc-700 font-mono"
                spellCheck={false}
              />
            </div>
            <button
              onClick={handleParse}
              disabled={!pineCode.trim()}
              className="mt-3 w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium text-sm rounded transition-all"
            >
              Parse Parameters
            </button>

            {/* Translate Pine → Python */}
            <div className="mt-3 border border-zinc-800 rounded-lg overflow-hidden">
              <div className="bg-zinc-900/60 px-4 py-2 border-b border-zinc-800">
                <span className="text-xs text-zinc-500 tracking-wider">TRANSLATE TO PYTHON</span>
              </div>
              <div className="p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <input
                    type="password"
                    value={anthropicKey}
                    onChange={e => setAnthropicKey(e.target.value)}
                    placeholder="Anthropic API Key (sk-ant-...)"
                    className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <button
                  onClick={async () => {
                    if (!pineCode.trim()) { alert("Paste Pine Script first"); return; }
                    setTranslating(true);
                    try {
                      const titleMatch = pineCode.match(/strategy\s*\(\s*["']([^"']+)/);
                      const rawName = titleMatch ? titleMatch[1] : "translated_strategy";
                      let name = rawName.toLowerCase().replace(/[^\w\s]/g, '').replace(/\s+/g, '_').substring(0, 40);

                      // Check for duplicate name
                      const existingNames = pyStrategies.map(s => s.name);
                      if (existingNames.includes(name)) {
                        const choice = prompt(
                          `A strategy named "${name}" already exists.\n\n` +
                          `• Click OK with the name below to overwrite it\n` +
                          `• Or type a new name to save as a different strategy\n` +
                          `• Click Cancel to abort`,
                          name
                        );
                        if (choice === null) { setTranslating(false); return; } // Cancelled
                        name = choice.trim().toLowerCase().replace(/[^\w]/g, '_').substring(0, 40);
                        if (!name) { alert("Invalid name"); setTranslating(false); return; }
                      }

                      const resultJson = await invoke("translate_pine", {
                        pineCode,
                        name,
                        apiKey: anthropicKey.trim() || "",
                      });
                      const result = JSON.parse(resultJson);
                      if (result.success) {
                        const source = result.source || "deterministic";
                        const testResult = result.test_result || result.deterministic_error || "";
                        const isReady = testResult === "pass";
                        
                        let msg = isReady ? `Translation successful!\n\n` : `Translation needs attention.\n\n`;
                        msg += `File: ${result.name}.py (${result.lines} lines)\n`;
                        msg += `Parameters found: ${result.inputs ?? "unknown"}\n`;
                        
                        // Explain the translation method
                        if (source === "cache") {
                          msg += `Translation method: Previously cached (instant)\n`;
                        } else if (source === "deterministic") {
                          msg += `Translation method: Deterministic parser (no API call)\n`;
                        } else if (source.includes("claude_repair")) {
                          msg += `Translation method: Deterministic parser + Claude repair\n`;
                        }
                        
                        msg += `\n`;
                        
                        if (isReady) {
                          msg += `Verification: Passed. The strategy is ready to run sweeps.\n`;
                          msg += `\nTo use: switch to Python mode and select "${result.name}".`;
                        } else if (testResult) {
                          msg += `Verification: Failed.\n\n`;
                          msg += `Error: "${testResult}"\n\n`;
                          msg += `The strategy has complex custom indicators (like recursive filters or stateful functions) that could not be automatically converted. The file has been saved but needs editing before it can run.\n`;
                          if (!anthropicKey.trim()) {
                            msg += `\nNext steps:\n`;
                            msg += `1. Enter an Anthropic API key above and click Translate again — Claude will attempt to fix the issue automatically.\n`;
                            msg += `2. Or switch to Python mode and click "Edit" on "${result.name}" to fix the code manually.\n`;
                          } else if (source.includes("claude_repair")) {
                            msg += `\nNext steps:\n`;
                            msg += `1. Click Translate again — Claude may produce a different fix on retry.\n`;
                            msg += `2. Switch to Python mode and click "Edit" on "${result.name}" to fix the remaining issue manually.\n`;
                            msg += `3. If a hand-validated version of this strategy exists, use that instead.\n`;
                          }
                        }
                        
                        alert(msg);
                        const list = await invoke("list_python_strategies");
                        setPyStrategies(list);
                      } else {
                        alert("Translation failed: " + result.error);
                      }
                    } catch (e) {
                      alert("Translation error: " + e);
                    } finally {
                      setTranslating(false);
                    }
                  }}
                  disabled={!pineCode.trim() || translating}
                  className="w-full py-2 bg-purple-600 hover:bg-purple-500 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium text-sm rounded transition-all"
                >
                  {translating ? "Translating..." : "Translate Pine → Python"}
                </button>
                <p className="text-xs text-zinc-600">
                  Deterministic translator handles most strategies. API key enables Claude repair for complex custom functions.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Python strategy selector */}
        {!parsed && mode === "python" && (
          <div>
            <div className="border border-zinc-800 rounded-lg overflow-hidden">
              <div className="bg-zinc-900/60 px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
                <span className="text-xs text-zinc-500 tracking-wider">SELECT PYTHON STRATEGY</span>
                <div className="flex gap-2">
                  <label className="cursor-pointer px-3 py-1 bg-zinc-800 hover:bg-zinc-700 rounded text-xs text-zinc-300 transition-all">
                    Upload .py
                    <input type="file" accept=".py" onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      const text = await file.text();
                      setPyEditorCode(text);
                      setPyEditorName(file.name.replace('.py', ''));
                      setPyEditing(true);
                    }} className="hidden" />
                  </label>
                  <button onClick={() => { setPyEditing(true); setPyEditorCode(""); setPyEditorName(""); }}
                    className="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 rounded text-xs text-zinc-300 transition-all">
                    + New
                  </button>
                </div>
              </div>

              {/* Strategy editor */}
              {pyEditing && (
                <div className="p-4 border-b border-zinc-800">
                  <div className="flex gap-2 mb-2">
                    <input
                      type="text" value={pyEditorName} onChange={e => setPyEditorName(e.target.value)}
                      placeholder="strategy_name (no spaces)"
                      className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 focus:border-blue-500 focus:outline-none font-mono"
                    />
                    <button onClick={async () => {
                      if (!pyEditorName.trim() || !pyEditorCode.trim()) { alert("Name and code required"); return; }
                      let saveName = pyEditorName.trim().toLowerCase().replace(/ /g, '_').replace('.py', '');
                      
                      // Check for duplicate name (unless editing the same strategy)
                      const existingNames = pyStrategies.map(s => s.name);
                      const isEditing = existingNames.includes(saveName);
                      if (isEditing) {
                        const choice = prompt(
                          `A strategy named "${saveName}" already exists.\n\n` +
                          `• Click OK to overwrite it\n` +
                          `• Or type a new name to save as a copy\n` +
                          `• Click Cancel to abort`,
                          saveName
                        );
                        if (choice === null) return;
                        saveName = choice.trim().toLowerCase().replace(/[^\w]/g, '_').replace('.py', '');
                        if (!saveName) { alert("Invalid name"); return; }
                      }
                      
                      try {
                        const msg = await invoke("save_python_strategy", { name: saveName, code: pyEditorCode });
                        setPyEditing(false);
                        const list = await invoke("list_python_strategies");
                        setPyStrategies(list);
                        setSelectedStrategy(saveName);
                        alert(msg);
                      } catch (e) { alert("Save failed: " + e); }
                    }}
                      className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded text-xs text-white font-medium transition-all">
                      Save
                    </button>
                    <button onClick={() => setPyEditing(false)}
                      className="px-3 py-1.5 bg-zinc-700 hover:bg-zinc-600 rounded text-xs text-zinc-300 transition-all">
                      Cancel
                    </button>
                  </div>
                  <textarea
                    value={pyEditorCode} onChange={e => setPyEditorCode(e.target.value)}
                    placeholder={"from strategy_base import Strategy, Param\n\nclass MyStrategy(Strategy):\n    params = {\n        \"fast\": Param(0.5, min=0.1, max=0.9, step=0.01),\n    }\n\n    def init(self, ctx):\n        pass\n\n    def on_bar(self, bar, ctx):\n        pass"}
                    className="w-full h-64 bg-zinc-950 text-zinc-300 text-xs p-3 resize-none focus:outline-none placeholder-zinc-700 font-mono rounded border border-zinc-800"
                    spellCheck={false}
                  />
                </div>
              )}

              <div className="p-4">
                {pyStrategies.length === 0 ? (
                  <div className="text-sm text-zinc-500 text-center py-8">
                    No Python strategies found.<br />
                    <span className="text-xs">Upload a .py file or click + New to create one</span>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {pyStrategies.map(s => (
                      <div key={s.name} className={`flex items-center gap-2 w-full px-4 py-3 rounded-lg border transition-all ${
                        selectedStrategy === s.name
                          ? "bg-blue-600/15 border-blue-500/40 text-zinc-100"
                          : "bg-zinc-900 border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-300"
                      }`}>
                        <button onClick={() => setSelectedStrategy(s.name)} className="flex-1 text-left">
                          <div className="font-medium text-sm">{s.display_name}</div>
                          <div className="text-xs text-zinc-500 mt-0.5">{s.name}.py</div>
                        </button>
                        <button onClick={async () => {
                          try {
                            const code = await invoke("read_python_strategy", { name: s.name });
                            setPyEditorCode(code);
                            setPyEditorName(s.name);
                            setPyEditing(true);
                          } catch (e) { alert("Cannot read: " + e); }
                        }}
                          className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 rounded transition-all"
                          title="Edit strategy">
                          Edit
                        </button>
                        <button onClick={async () => {
                          const builtins = ["mesa_mama_fama", "larry_williams", "pure_orb"];
                          if (builtins.includes(s.name)) { alert("Cannot delete built-in strategy."); return; }
                          if (!confirm(`Delete "${s.display_name}" (${s.name}.py)?\n\nThis cannot be undone.`)) return;
                          try {
                            await invoke("delete_python_strategy", { name: s.name });
                            const list = await invoke("list_python_strategies");
                            setPyStrategies(list);
                            if (selectedStrategy === s.name) setSelectedStrategy("");
                          } catch (e) { alert("Delete failed: " + e); }
                        }}
                          className="px-2 py-1 text-xs text-zinc-500 hover:text-red-400 hover:bg-red-900/30 rounded transition-all"
                          title="Delete strategy">
                          Del
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <button
              onClick={() => loadPythonParams(selectedStrategy)}
              disabled={!selectedStrategy || pyLoading}
              className="mt-3 w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium text-sm rounded transition-all"
            >
              {pyLoading ? "Loading..." : "Load Parameters"}
            </button>
          </div>
        )}

        {/* Parameter list */}
        {parsed && (
          <>
            {/* Back button */}
            <button
              onClick={() => { setParsed(false); setParams([]); setParamsForStrategy(""); try { localStorage.removeItem("so_params"); } catch {} }}
              className="mb-4 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 text-xs rounded transition-all"
            >
              ← Back to editor
            </button>

            {/* Pine mode: strategy mapping selector */}
            {mode === "pine" && pyStrategies.length > 0 && (
              <div className="mb-5 border border-zinc-800 rounded-lg overflow-hidden">
                <div className="bg-zinc-900/60 px-4 py-2 border-b border-zinc-800">
                  <span className="text-xs text-zinc-500 tracking-wider">EXECUTION ENGINE</span>
                </div>
                <div className="p-3">
                  <p className="text-xs text-zinc-500 mb-2">
                    Pine Script parameters parsed. Select the Python strategy to execute the sweep:
                  </p>
                  <div className="space-y-1.5">
                    {pyStrategies.map(s => (
                      <div
                        key={s.name}
                        className={`flex items-center gap-2 px-3 py-2 rounded border transition-all ${
                          selectedStrategy === s.name
                            ? "bg-blue-600/15 border-blue-500/40 text-zinc-100"
                            : "bg-zinc-900 border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-300"
                        }`}
                      >
                        <button onClick={() => setSelectedStrategy(s.name)} className="flex-1 text-left">
                          <span className="text-sm font-medium">{s.display_name}</span>
                          <span className="text-xs text-zinc-500 ml-2">{s.name}.py</span>
                        </button>
                        <button onClick={async (e) => {
                          e.stopPropagation();
                          const builtins = ["mesa_mama_fama", "larry_williams", "pure_orb"];
                          if (builtins.includes(s.name)) { alert("Cannot delete built-in strategy."); return; }
                          if (!confirm(`Delete "${s.display_name}" (${s.name}.py)?\n\nThis cannot be undone.`)) return;
                          try {
                            await invoke("delete_python_strategy", { name: s.name });
                            const list = await invoke("list_python_strategies");
                            setPyStrategies(list);
                            if (selectedStrategy === s.name) setSelectedStrategy("");
                          } catch (e2) { alert("Delete failed: " + e2); }
                        }}
                          className="px-2 py-1 text-xs text-zinc-500 hover:text-red-400 hover:bg-red-900/30 rounded transition-all"
                          title="Delete strategy">
                          Del
                        </button>
                      </div>
                    ))}
                  </div>
                  {!selectedStrategy && (
                    <p className="text-xs text-amber-400 mt-2">Select a strategy to enable sweep.</p>
                  )}
                </div>
              </div>
            )}

            {grouped.map(({ name, params: gp }) => (
              <div key={name} className="mb-5">
                <div className="text-xs text-zinc-500 tracking-wider font-medium mb-1 px-1 py-1">{name.toUpperCase()}</div>
                {gp.map(p => <ParamRow key={p.id} p={p} onUpdate={update} />)}
              </div>
            ))}

            {/* Sweep Settings */}
            <div className="mb-5">
              <div className="text-xs text-zinc-500 tracking-wider font-medium mb-1 px-1 py-1">SWEEP SETTINGS</div>
              {[
                { k: "initial_capital", l: "Initial Capital $", s: 1000 },
                { k: "fee_pct", l: "Fee % (round-trip)", s: 0.01 },
                { k: "max_dd_pct", l: "Max Drawdown %", s: 1 },
                { k: "min_trades", l: "Min Trades", s: 5 },
                { k: "top_n", l: "Top N Results", s: 10 },
                { k: "warmup_bars", l: "Warmup Bars", s: 50 },
              ].map(({ k, l, s }) => (
                <div key={k} className="flex items-center justify-between py-2.5 px-1 border-b border-zinc-800/40">
                  <span className="text-sm text-zinc-300">{l}</span>
                  <NumInput value={settings[k]} onChange={v => setSettings(prev => ({...prev, [k]: v}))} step={s} className="w-28" />
                </div>
              ))}
              <div className="flex items-center justify-between py-2.5 px-1 border-b border-zinc-800/40">
                <span className="text-sm text-zinc-300">Sort By</span>
                <SelectInput
                  value={settings.sort_by}
                  onChange={v => setSettings(prev => ({...prev, sort_by: v}))}
                  options={["pf", "net", "calmar", "sharpe"]}
                  className="min-w-[140px]"
                />
              </div>
              <div className="flex items-center justify-between py-2.5 px-1 border-b border-zinc-800/40">
                <span className="text-sm text-zinc-300">Output File</span>
                <input
                  type="text"
                  value={settings.output}
                  onChange={e => setSettings(prev => ({...prev, output: e.target.value}))}
                  className="bg-zinc-800 border border-zinc-700 rounded px-2.5 py-1.5 text-sm text-zinc-200 w-36 text-right focus:border-blue-500 focus:outline-none hover:border-zinc-600 transition-colors"
                />
              </div>

              {/* TradingView Recalculate / Fill Order modes */}
              <div className="text-xs text-zinc-600 tracking-wider font-medium mt-4 mb-1 px-1 py-1">RECALCULATE</div>
              {[
                { k: "calc_on_order_fills", l: "After order is filled", tip: "Re-sizes entries using post-fill equity (TV: calc_on_order_fills)" },
                { k: "on_every_tick", l: "On every tick", tip: "In backtesting, equivalent to confirmed-bar-only for historical data" },
              ].map(({ k, l, tip }) => (
                <label key={k} className="flex items-center justify-between py-2 px-1 border-b border-zinc-800/40 cursor-pointer group">
                  <span className="text-sm text-zinc-300 group-hover:text-zinc-100 transition-colors" title={tip}>{l}</span>
                  <input
                    type="checkbox"
                    checked={!!settings[k]}
                    onChange={e => setSettings(prev => ({...prev, [k]: e.target.checked}))}
                    className="w-4 h-4 rounded border-zinc-600 bg-zinc-800 text-emerald-500 focus:ring-emerald-500/30 cursor-pointer"
                  />
                </label>
              ))}
              <div className="text-xs text-zinc-600 tracking-wider font-medium mt-4 mb-1 px-1 py-1">FILL ORDERS</div>
              {[
                { k: "fill_on_bar_close", l: "On bar close", tip: "Fill at previous bar's close instead of current bar's open (TV: process_orders_on_close)" },
                { k: "use_standard_ohlc", l: "Using standard OHLC", tip: "Use synthesized OHLC — minimal effect on historical backtests" },
              ].map(({ k, l, tip }) => (
                <label key={k} className="flex items-center justify-between py-2 px-1 border-b border-zinc-800/40 cursor-pointer group">
                  <span className="text-sm text-zinc-300 group-hover:text-zinc-100 transition-colors" title={tip}>{l}</span>
                  <input
                    type="checkbox"
                    checked={!!settings[k]}
                    onChange={e => setSettings(prev => ({...prev, [k]: e.target.checked}))}
                    className="w-4 h-4 rounded border-zinc-600 bg-zinc-800 text-emerald-500 focus:ring-emerald-500/30 cursor-pointer"
                  />
                </label>
              ))}
            </div>

            {/* Warning */}
            {totalCombos > 5_000_000 && (
              <div className={`rounded border px-3 py-2 text-xs mb-4 ${
                totalCombos > 50_000_000
                  ? "bg-red-500/10 border-red-500/30 text-red-400"
                  : "bg-amber-500/10 border-amber-500/30 text-amber-400"
              }`}>
                {totalCombos > 50_000_000
                  ? `⚠ ${(totalCombos / 1e6).toFixed(0)}M combos — this will take ${(totalCombos / 1100 / 3600).toFixed(1)} hours. Reduce ranges.`
                  : `${(totalCombos / 1e6).toFixed(1)}M combos ≈ ${estTime}. Manageable.`}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2 mb-2">
              {!dataInfo && onLoadData && (
                <button onClick={onLoadData}
                  className="flex-1 py-2.5 bg-amber-600 hover:bg-amber-500 text-white text-sm rounded font-medium transition-all">
                  Load Data First
                </button>
              )}
              {dataInfo && onRunPythonSweep && (
                <button
                  onClick={() => onRunPythonSweep(selectedStrategy, configJson)}
                  disabled={running || !selectedStrategy}
                  className="flex-1 py-2.5 bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white text-sm rounded font-medium transition-all">
                  {running ? `Sweeping... ${Math.round((progress || 0) * 100)}%` : `Run Sweep (${totalCombos.toLocaleString()} combos)`}
                </button>
              )}
            </div>
            <div className="flex gap-2 mb-4">
              <button onClick={() => setShowJson(v => !v)}
                className="flex-1 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm rounded border border-zinc-700 transition-all">
                {showJson ? "Hide JSON" : "Preview JSON"}
              </button>
              <button onClick={() => { navigator.clipboard.writeText(configJson); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
                className="flex-1 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm rounded border border-zinc-700 transition-all">
                {copied ? "✓ Copied" : "Copy JSON"}
              </button>
              <button onClick={() => {
                const b = new Blob([configJson], { type: "application/json" });
                const u = URL.createObjectURL(b);
                Object.assign(document.createElement("a"), { href: u, download: "sweep_config.json" }).click();
                URL.revokeObjectURL(u);
              }}
                className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded font-medium transition-all">
                Export Config
              </button>
            </div>

            {/* JSON */}
            {showJson && (
              <div className="border border-zinc-800 rounded overflow-hidden mb-8">
                <div className="bg-zinc-900 px-3 py-1.5 border-b border-zinc-800 text-xs text-zinc-500">sweep_config.json</div>
                <pre className="p-3 text-xs text-emerald-400/80 overflow-auto max-h-96 bg-zinc-950">{configJson}</pre>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
