import { useState, useCallback, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open, save } from "@tauri-apps/plugin-dialog";
import StrategyLab from "./components/StrategyLab";
import DataFetcher from "./components/DataFetcher";
import Results from "./components/Results";
import Workers from "./components/Workers";

const TABS = ["Strategy Lab", "Data", "Results"];

// ── Persistence helpers ──
function saveState(key, value) {
  try { localStorage.setItem(`so_${key}`, JSON.stringify(value)); } catch {}
}
function loadState(key, fallback) {
  try {
    const v = localStorage.getItem(`so_${key}`);
    return v ? JSON.parse(v) : fallback;
  } catch { return fallback; }
}

export default function App() {
  const [tab, setTab] = useState(0);
  const [dataInfo, setDataInfo] = useState(null);
  const [sweepResults, setSweepResults] = useState(() => loadState("sweepResults", null));
  const [progress, setProgress] = useState(0);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");
  const [workers, setWorkers] = useState(() => loadState("workers", []));
  const [sweepStart, setSweepStart] = useState(null);
  const [pineCode, setPineCode] = useState(() => loadState("pineCode", ""));
  const [lastDataPath, setLastDataPath] = useState(() => loadState("lastDataPath", null));

  // Restore data on mount if we had a CSV loaded
  const mounted = useRef(false);
  useEffect(() => {
    if (mounted.current) return;
    mounted.current = true;
    if (lastDataPath) {
      invoke("load_data", { path: lastDataPath })
        .then(info => {
          setDataInfo(info);
          setStatus(`Restored: ${info.bars.toLocaleString()} bars from ${info.symbol}`);
        })
        .catch(() => {
          setStatus("Previous data file not found — load a new one.");
          setLastDataPath(null);
          saveState("lastDataPath", null);
        });
    }
    // Re-check workers
    workers.forEach(w => {
      invoke("check_worker", { address: w.address })
        .then(info => {
          setWorkers(prev => prev.map(x => x.address === w.address ? info : x));
        })
        .catch(() => {
          setWorkers(prev => prev.map(x =>
            x.address === w.address ? { ...x, status: "offline" } : x
          ));
        });
    });
  }, []);

  // Auto-save state on change
  useEffect(() => { saveState("workers", workers.map(w => ({ address: w.address, name: w.name }))); }, [workers]);
  useEffect(() => { saveState("pineCode", pineCode); }, [pineCode]);
  useEffect(() => { if (sweepResults) saveState("sweepResults", sweepResults); }, [sweepResults]);
  useEffect(() => { if (dataInfo?.file_path) { setLastDataPath(dataInfo.file_path); saveState("lastDataPath", dataInfo.file_path); } }, [dataInfo]);

  // Format seconds into human readable
  function formatTime(secs) {
    if (secs < 60) return `${Math.round(secs)}s`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    return `${h}h ${m}m`;
  }

  function SweepProgress({ progress }) {
    const elapsed = sweepStart ? (Date.now() - sweepStart) / 1000 : 0;
    const pct = Math.max(0.001, progress); // avoid division by zero
    const eta = pct > 0 && pct < 1 ? (elapsed / pct) * (1 - pct) : 0;
    const rate = elapsed > 0 && pct > 0 ? "running" : "starting";

    return (
      <div className="flex items-center gap-3">
        <div className="w-36 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
          <div className="h-full bg-emerald-500 transition-all duration-300"
            style={{ width: `${pct * 100}%` }} />
        </div>
        <span className="text-xs text-zinc-400 font-mono whitespace-nowrap">
          {Math.round(pct * 100)}%
        </span>
        {elapsed > 2 && (
          <span className="text-xs text-zinc-500 whitespace-nowrap">
            {formatTime(elapsed)} elapsed
            {eta > 0 && pct < 0.99 && ` · ~${formatTime(eta)} remaining`}
          </span>
        )}
      </div>
    );
  }

  // Load CSV data file
  const handleLoadData = useCallback(async () => {
    try {
      const path = await open({
        filters: [{ name: "CSV", extensions: ["csv"] }],
        multiple: false,
      });
      if (!path) return;
      setStatus("Loading data...");
      const info = await invoke("load_data", { path });
      setDataInfo(info);
      setStatus(`Loaded ${info.bars.toLocaleString()} bars: ${info.symbol} (${info.first_date} → ${info.last_date})`);
    } catch (e) {
      setStatus(`Error: ${e}`);
    }
  }, []);

  // Stop all sweeps — local + workers
  const handleStop = useCallback(async () => {
    try {
      // Stop local sweep
      await invoke("stop_sweep").catch(() => {});

      // Stop all workers
      for (const w of workers) {
        if (w.status === "running") {
          await invoke("stop_worker", { address: w.address }).catch(() => {});
          setWorkers(prev => prev.map(x =>
            x.address === w.address ? { ...x, status: "stopped" } : x
          ));
        }
      }

      setRunning(false);
      setProgress(0);
      setSweepStart(null);
      setStatus("Sweep stopped.");
    } catch (e) {
      setStatus(`Stop error: ${e}`);
    }
  }, [workers]);

  // Run sweep — distributed if workers connected, local otherwise
  const handleRunSweep = useCallback(async (configJson) => {
    if (!dataInfo) {
      setStatus("Load data first!");
      return;
    }
    try {
      setRunning(true);
      setProgress(0);
      setSweepStart(Date.now());

      const config = JSON.parse(configJson);
      const activeWorkers = workers.filter(w => w.status !== "error");

      if (activeWorkers.length === 0) {
        // ── Local-only sweep ──
        setStatus("Running Pine sweep locally...");
        const unlisten = await listen("sweep-progress", (event) => {
          setProgress(event.payload);
        });
        const resultJson = await invoke("run_pine_sweep", { pineCode, configJson });
        const results = JSON.parse(resultJson);
        setSweepResults(results);
        setTab(2);
        setStatus(`Sweep complete! ${results.length} results.`);
        unlisten();
      } else {
        // ── Distributed sweep ──
        const nodeCount = activeWorkers.length + 1;
        setStatus(`Distributing sweep across ${nodeCount} nodes...`);

        // Send data to all workers
        for (const w of activeWorkers) {
          setStatus(`Sending data to ${w.name}...`);
          try {
            await invoke("send_data_to_worker", { address: w.address });
          } catch (e) {
            setWorkers(prev => prev.map(x =>
              x.address === w.address ? { ...x, status: "error" } : x
            ));
            setStatus(`Failed to send data to ${w.name}: ${e}`);
          }
        }

        // Estimate total combos from config structure (flat parameter map)
        let totalCombos = 1;
        for (const vals of Object.values(config.parameters || {})) {
          if (Array.isArray(vals)) {
            if (vals.length === 3 && typeof vals[0] === "number" && typeof vals[2] === "number" && vals[2] > 0) {
              totalCombos *= Math.max(1, Math.ceil((vals[1] - vals[0]) / vals[2]) + 1);
            } else {
              totalCombos *= Math.max(1, vals.length);
            }
          }
        }

        // Weighted chunk distribution — faster machines get more work
        // Load saved speeds or use defaults
        const speeds = loadState("workerSpeeds", {});
        const workingWorkers = activeWorkers.filter(w => w.status !== "error");

        // Build weight array: [local, worker0, worker1, ...]
        // Default weight 1.0, saved benchmarks override
        const weights = [speeds["local"] || 1.0];
        for (const w of workingWorkers) {
          weights.push(speeds[w.address] || 1.0);
        }
        const totalWeight = weights.reduce((a, b) => a + b, 0);

        // Assign combo ranges proportional to weight
        let cursor = 0;
        const chunks = [];
        for (let i = 0; i < weights.length; i++) {
          const share = Math.round(totalCombos * (weights[i] / totalWeight));
          const chunkStart = cursor;
          const chunkEnd = Math.min(cursor + share, totalCombos);
          chunks.push({ start: chunkStart, end: chunkEnd });
          cursor = chunkEnd;
        }
        // Ensure last chunk covers remainder
        if (chunks.length > 0) chunks[chunks.length - 1].end = totalCombos;

        const localChunk = chunks[0];
        const localCombos = localChunk.end - localChunk.start;

        // Log distribution
        const distLog = [`Local: ${localCombos.toLocaleString()} combos`];

        // Start workers on their chunks
        for (let i = 0; i < workingWorkers.length; i++) {
          const w = workingWorkers[i];
          const chunk = chunks[i + 1];
          const workerCombos = chunk.end - chunk.start;
          distLog.push(`${w.name}: ${workerCombos.toLocaleString()} combos`);
          try {
            await invoke("start_worker_sweep", {
              address: w.address,
              configJson,
              chunkStart: chunk.start,
              chunkEnd: chunk.end,
            });
            setWorkers(prev => prev.map(x =>
              x.address === w.address ? { ...x, status: "running", combos: workerCombos } : x
            ));
          } catch (e) {
            console.error(`Failed to start ${w.name}:`, e);
          }
        }

        setStatus(`Sweeping ${nodeCount} nodes: ${distLog.join(" | ")}`);
        const sweepStartMs = Date.now();

        // Run local chunk
        setStatus(`Running local chunk (${localCombos.toLocaleString()} combos) + ${workingWorkers.length} workers...`);
        let localProgress = 0;
        const unlisten = await listen("sweep-progress", (event) => {
          localProgress = event.payload;
        });

        // Start polling combined progress in parallel
        const progressInterval = setInterval(async () => {
          let totalProgress = localProgress; // local node contribution
          let nodeCount2 = 1;
          for (const w of workingWorkers) {
            try {
              const info = await invoke("poll_worker", { address: w.address });
              setWorkers(prev => prev.map(x =>
                x.address === w.address ? info : x
              ));
              totalProgress += info.progress;
              nodeCount2++;
            } catch (e) { /* skip */ }
          }
          setProgress(totalProgress / nodeCount2);
        }, 2000);

        const localResultJson = await invoke("run_pine_sweep", { pineCode, configJson });
        const localResults = JSON.parse(localResultJson);
        unlisten();

        // Stop progress polling
        clearInterval(progressInterval);

        // Poll workers until all done
        setStatus("Local done — waiting for workers to finish...");
        let allDone = false;
        while (!allDone) {
          await new Promise(r => setTimeout(r, 2000));
          allDone = true;
          let totalProgress = 1.0; // local is done
          let nodeCount2 = 1;
          for (const w of workingWorkers) {
            try {
              const info = await invoke("poll_worker", { address: w.address });
              setWorkers(prev => prev.map(x =>
                x.address === w.address ? info : x
              ));
              totalProgress += info.progress;
              nodeCount2++;
              if (info.status === "running") {
                allDone = false;
              }
            } catch (e) {
              // Worker unreachable — skip
            }
          }
          setProgress(totalProgress / nodeCount2);
        }

        // Collect results from all workers
        let allResults = [...localResults];
        for (const w of workingWorkers) {
          try {
            const json = await invoke("get_worker_results", { address: w.address });
            const workerResults = JSON.parse(json);
            allResults.push(...workerResults);
          } catch (e) {
            console.error(`Failed to get results from ${w.name}:`, e);
          }
        }

        // Sort merged results
        allResults.sort((a, b) => b.result.profit_factor - a.result.profit_factor);
        allResults = allResults.slice(0, config.sweep_settings?.top_n || 100);

        setSweepResults(allResults);
        setTab(2);

        // Save benchmarked speeds for next time (combos/sec per node)
        const elapsedSec = (Date.now() - sweepStartMs) / 1000;
        const newSpeeds = { ...loadState("workerSpeeds", {}) };
        newSpeeds["local"] = localCombos / Math.max(1, elapsedSec);
        for (let i = 0; i < workingWorkers.length; i++) {
          const w = workingWorkers[i];
          const chunk = chunks[i + 1];
          const workerCombos = chunk.end - chunk.start;
          newSpeeds[w.address] = workerCombos / Math.max(1, elapsedSec);
        }
        saveState("workerSpeeds", newSpeeds);

        setStatus(`Distributed sweep complete! ${allResults.length} results from ${nodeCount} nodes in ${formatTime(elapsedSec)}.`);
      }
    } catch (e) {
      setStatus(`Sweep error: ${e}`);
    } finally {
      setRunning(false);
      setProgress(1);
      setSweepStart(null);
    }
  }, [dataInfo, workers]);

  // Save results
  const handleSaveResults = useCallback(async () => {
    if (!sweepResults) return;
    try {
      const path = await save({
        filters: [{ name: "CSV", extensions: ["csv"] }],
        defaultPath: "sweep_results.csv",
      });
      if (!path) return;
      // Convert results to CSV
      const headers = Object.keys(sweepResults[0]?.params || {}).join(",") + ",pf,net,dd,wr,trades";
      const rows = sweepResults.map(r => {
        const pVals = Object.values(r.params).join(",");
        return `${pVals},${r.result.profit_factor.toFixed(3)},${r.result.net_profit.toFixed(2)},${r.result.max_drawdown.toFixed(2)},${r.result.win_rate.toFixed(1)},${r.result.total_trades}`;
      });
      await invoke("save_results", { path, csvData: [headers, ...rows].join("\n") });
      setStatus(`Saved ${sweepResults.length} results to ${path}`);
    } catch (e) {
      setStatus(`Save error: ${e}`);
    }
  }, [sweepResults]);

  return (
    <div className="h-screen flex flex-col bg-zinc-950">
      {/* Tab bar */}
      <div className="flex items-center bg-zinc-900 border-b border-zinc-800 px-4">
        <div className="flex gap-1 py-2">
          {TABS.map((t, i) => (
            <button
              key={t}
              onClick={() => setTab(i)}
              className={`px-4 py-1.5 rounded text-sm transition-all ${
                tab === i
                  ? "bg-zinc-800 text-zinc-100 font-medium"
                  : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50"
              }`}
            >
              {t}
              {i === 2 && sweepResults && (
                <span className="ml-1.5 text-xs text-emerald-400">{sweepResults.length}</span>
              )}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-3">
          {/* Data status */}
          {dataInfo ? (
            <div className="flex items-center gap-2 bg-zinc-800/60 rounded px-2.5 py-1">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
              <span className="text-xs text-emerald-400 font-medium">{dataInfo.symbol}</span>
              <span className="text-xs text-zinc-500">·</span>
              <span className="text-xs text-zinc-400">{dataInfo.timeframe || ""}</span>
              <span className="text-xs text-zinc-500">·</span>
              <span className="text-xs text-zinc-400">{dataInfo.bars.toLocaleString()} bars</span>
              <span className="text-xs text-zinc-500">·</span>
              <span className="text-xs text-zinc-500">{dataInfo.first_date} → {dataInfo.last_date}</span>
              <button onClick={handleLoadData} title="Change data file"
                className="ml-1 text-zinc-600 hover:text-zinc-300 transition-colors text-xs">⟳</button>
            </div>
          ) : (
            <button
              onClick={handleLoadData}
              className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              Load Data →
            </button>
          )}

          {/* Progress bar during sweep */}
          {running && (
            <>
              <SweepProgress progress={progress} />
              <button onClick={handleStop}
                className="px-2.5 py-1 bg-red-600/80 hover:bg-red-500 text-white text-xs rounded transition-all font-medium">
                Stop
              </button>
            </>
          )}
        </div>
      </div>

      {/* Status banner — shown during sweeps and after actions */}
      {status && (
        <div className="bg-zinc-900/80 border-b border-zinc-800 px-4 py-1.5">
          <span className="text-xs text-zinc-300">{status}</span>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {tab === 0 && (
          <div>
            <StrategyLab
              onRunSweep={handleRunSweep}
              onLoadData={handleLoadData}
              dataInfo={dataInfo}
              running={running}
              progress={progress}
              pineCode={pineCode}
              onPineCodeChange={setPineCode}
            />
            <div className="max-w-4xl mx-auto px-4 pb-4">
              <Workers workers={workers} setWorkers={setWorkers} />
            </div>
          </div>
        )}
        {tab === 1 && (
          <DataFetcher
            onDataLoaded={(info) => { setDataInfo(info); setStatus(`Loaded ${info.bars} bars`); }}
            onLoadFile={handleLoadData}
          />
        )}
        {tab === 2 && (
          <Results
            results={sweepResults}
            onSave={handleSaveResults}
          />
        )}
      </div>

      {/* Status bar */}
      <div className="bg-zinc-900 border-t border-zinc-800 px-4 py-1 flex items-center justify-between">
        <span className="text-xs text-zinc-600 truncate max-w-[70%] font-mono">
          {dataInfo?.file_path || "No data loaded"}
        </span>
        <span className="text-xs text-zinc-600">
          Strategy Optimizer v1.0{workers.length > 0 && ` · ${workers.length + 1} nodes`}
        </span>
      </div>
    </div>
  );
}
