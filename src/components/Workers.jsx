import { useState, useCallback, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";

function loadState(key, fallback) {
  try { const v = localStorage.getItem("so_" + key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
}

export default function Workers({ workers, setWorkers }) {
  const [newAddr, setNewAddr] = useState("");
  const [checking, setChecking] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const intervalRef = useRef(null);

  useEffect(() => {
    if (!autoRefresh || workers.length === 0) {
      if (intervalRef.current) clearInterval(intervalRef.current);
      return;
    }
    intervalRef.current = setInterval(() => {
      workers.forEach(w => {
        invoke("poll_worker", { address: w.address })
          .then(info => {
            setWorkers(prev => prev.map(x =>
              x.address === w.address ? { ...x, ...info, status: info.status || "idle" } : x
            ));
          })
          .catch(() => {
            setWorkers(prev => prev.map(x =>
              x.address === w.address ? { ...x, status: "offline" } : x
            ));
          });
      });
    }, 5000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh, workers.length]);

  const addWorker = useCallback(async () => {
    if (!newAddr.trim()) return;
    const cleanAddr = newAddr.trim().replace(/^https?:\/\//, "");
    setChecking(true);
    try {
      const info = await invoke("check_worker", { address: cleanAddr });
      setWorkers(prev => [...prev.filter(w => w.address !== cleanAddr), { ...info, address: cleanAddr }]);
      setNewAddr("");
    } catch (e) {
      setWorkers(prev => [...prev.filter(w => w.address !== cleanAddr), {
        address: cleanAddr, name: cleanAddr, status: "offline", progress: 0, combos: 0,
      }]);
      setNewAddr("");
    }
    setChecking(false);
  }, [newAddr, setWorkers]);

  const removeWorker = useCallback((addr) => {
    setWorkers(prev => prev.filter(w => w.address !== addr));
  }, [setWorkers]);

  const resetWorker = useCallback(async (addr) => {
    try {
      await invoke("reset_worker", { address: addr });
      setWorkers(prev => prev.map(x =>
        x.address === addr ? { ...x, status: "idle", progress: 0, combos: 0 } : x
      ));
    } catch (e) {
      alert("Reset failed: " + e);
    }
  }, [setWorkers]);

  const refreshWorker = useCallback(async (addr) => {
    try {
      const info = await invoke("check_worker", { address: addr });
      setWorkers(prev => prev.map(x =>
        x.address === addr ? { ...x, ...info, address: addr } : x
      ));
    } catch {
      setWorkers(prev => prev.map(x =>
        x.address === addr ? { ...x, status: "offline" } : x
      ));
    }
  }, [setWorkers]);

  const refreshAllWorkers = useCallback(async () => {
    for (const w of workers) {
      try {
        const info = await invoke("check_worker", { address: w.address });
        setWorkers(prev => prev.map(x =>
          x.address === w.address ? { ...x, ...info, address: w.address } : x
        ));
      } catch {
        setWorkers(prev => prev.map(x =>
          x.address === w.address ? { ...x, status: "offline" } : x
        ));
      }
    }
  }, [workers, setWorkers]);

  const resetAllWorkers = useCallback(async () => {
    for (const w of workers) {
      try {
        await invoke("reset_worker", { address: w.address });
      } catch (e) { /* skip offline */ }
    }
    setWorkers(prev => prev.map(x => ({ ...x, status: "idle", progress: 0, combos: 0 })));
  }, [workers, setWorkers]);

  const speeds = loadState("workerSpeeds", {});
  const onlineCount = workers.filter(w => w.status !== "offline" && w.status !== "error").length;
  const runningCount = workers.filter(w => w.status === "running").length;
  const doneOrErrorCount = workers.filter(w => w.status === "done" || w.status === "error").length;

  const statusColor = (s) => ({
    idle: "bg-emerald-400", running: "bg-amber-400 animate-pulse",
    done: "bg-blue-400", offline: "bg-red-400", error: "bg-red-400",
  }[s] || "bg-zinc-600");

  const statusLabel = (s) => ({
    idle: "Ready", running: "Running", done: "Done", offline: "Offline", error: "Error",
  }[s] || s);

  return (
    <div className="border border-zinc-800 rounded-lg overflow-hidden">
      <div className="bg-zinc-900/60 px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xs text-zinc-500 tracking-wider">DISTRIBUTED WORKERS</span>
          {workers.length > 0 && (
            <span className="text-xs text-zinc-600">
              {onlineCount} online{runningCount > 0 ? ` \u00b7 ${runningCount} running` : ""}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {workers.length > 0 && (
            <button onClick={refreshAllWorkers}
              className="text-xs text-zinc-500 hover:text-blue-400 transition-colors"
              title="Refresh all worker connections">
              ⟳ Refresh
            </button>
          )}
          {doneOrErrorCount > 0 && (
            <button onClick={resetAllWorkers}
              className="text-xs text-zinc-500 hover:text-amber-400 transition-colors"
              title="Reset all workers">
              Reset All
            </button>
          )}
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input type="checkbox" checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
              className="accent-blue-500 w-3 h-3" />
            <span className="text-xs text-zinc-600">Auto-refresh</span>
          </label>
        </div>
      </div>

      <div className="p-4">
        <div className="flex gap-2 mb-3">
          <input type="text" value={newAddr} onChange={e => setNewAddr(e.target.value)}
            placeholder="192.168.1.100:9876" onKeyDown={e => e.key === "Enter" && addWorker()}
            className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 focus:border-blue-500 focus:outline-none font-mono" />
          <button onClick={addWorker} disabled={checking || !newAddr.trim()}
            className="px-4 py-1.5 bg-zinc-700 hover:bg-zinc-600 disabled:bg-zinc-800 disabled:text-zinc-600 text-zinc-200 text-sm rounded transition-all">
            {checking ? "..." : "Add"}
          </button>
        </div>

        {workers.length === 0 ? (
          <div className="text-center py-6 space-y-2">
            <p className="text-sm text-zinc-500">No workers connected</p>
            <div className="text-xs text-zinc-600 space-y-1">
              <p>To distribute sweeps across your local network:</p>
              <div className="bg-zinc-900 rounded-lg p-3 text-left font-mono mt-2">
                <p className="text-zinc-500"># On each worker machine, run:</p>
                <p className="text-emerald-400">python worker.py --name "Gaming PC"</p>
                <p className="text-zinc-600 mt-1"># Then add the displayed address above</p>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {workers.map(w => {
              const speed = speeds[w.address];
              const isRunning = w.status === "running";
              const progressPct = (w.progress || 0) * 100;
              return (
                <div key={w.address} className="bg-zinc-900 rounded-lg overflow-hidden">
                  <div className="flex items-center justify-between px-3 py-2.5">
                    <div className="flex items-center gap-2.5 flex-1 min-w-0">
                      <div className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor(w.status)}`} />
                      <div className="min-w-0">
                        <div className="text-sm text-zinc-200 font-medium truncate">{w.name || w.address}</div>
                        <div className="text-xs text-zinc-600 font-mono">{w.address}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        w.status === "idle" ? "bg-emerald-900/30 text-emerald-400" :
                        w.status === "running" ? "bg-amber-900/30 text-amber-400" :
                        w.status === "done" ? "bg-blue-900/30 text-blue-400" :
                        "bg-red-900/30 text-red-400"
                      }`}>{statusLabel(w.status)}</span>
                      {speed && (
                        <span className="text-xs text-zinc-600" title="Combos/sec from last sweep">
                          {speed >= 1000 ? `${(speed/1000).toFixed(1)}K` : speed.toFixed(0)}/s
                        </span>
                      )}
                      {(w.status === "done" || w.status === "error" || w.status === "running") && (
                        <button onClick={() => resetWorker(w.address)}
                          className="text-xs px-1.5 py-0.5 text-zinc-500 hover:text-amber-400 hover:bg-amber-900/20 rounded transition-colors"
                          title="Reset worker">Reset</button>
                      )}
                      {(w.status === "offline" || w.status === "idle") && (
                        <button onClick={() => refreshWorker(w.address)}
                          className="text-xs px-1.5 py-0.5 text-zinc-500 hover:text-blue-400 hover:bg-blue-900/20 rounded transition-colors"
                          title="Refresh connection">⟳</button>
                      )}
                      <button onClick={() => removeWorker(w.address)}
                        className="text-zinc-600 hover:text-red-400 text-xs transition-colors p-1" title="Remove worker">✕</button>
                    </div>
                  </div>
                  {isRunning && (
                    <div className="px-3 pb-2">
                      <div className="flex items-center justify-between text-xs text-zinc-500 mb-1">
                        <span>{w.combos ? `${w.combos.toLocaleString()} combos` : ""}</span>
                        <span>{progressPct.toFixed(0)}%</span>
                      </div>
                      <div className="w-full h-1 bg-zinc-800 rounded-full overflow-hidden">
                        <div className="h-full bg-amber-500 rounded-full transition-all duration-500"
                          style={{ width: `${Math.min(100, progressPct)}%` }} />
                      </div>
                    </div>
                  )}
                  {w.status === "error" && w.error && (
                    <div className="px-3 pb-2"><p className="text-xs text-red-400">{w.error}</p></div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {workers.length > 0 && (
          <div className="mt-3 pt-3 border-t border-zinc-800/50 flex items-center justify-between">
            <span className="text-xs text-zinc-600">{workers.length} worker{workers.length !== 1 ? "s" : ""}</span>
            {speeds["local"] && (
              <span className="text-xs text-zinc-600" title="Local machine speed">
                Local: {speeds["local"] >= 1000 ? `${(speeds["local"]/1000).toFixed(1)}K` : speeds["local"].toFixed(0)}/s
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
