"""
Strategy Optimizer — Distributed Worker Node (Python)

Lightweight HTTP server that accepts sweep jobs from the coordinator.
Run on any machine on the local network.

Usage:
  python worker.py                    # default port 9876
  python worker.py --port 9877        # custom port
  python worker.py --name "Gaming PC" # custom name

The coordinator (main app) sends:
  GET  /health            — liveness check
  GET  /status            — progress & stats
  POST /load-data         — receive CSV data
  POST /load-strategy     — receive strategy .py file
  POST /sweep             — start sweep chunk
  GET  /results           — retrieve results
  POST /stop              — abort current sweep
"""

import os
import sys
import json
import socket
import argparse
import threading
import time
import importlib
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

# Add parent dir so we can import backtest_engine, sweep, etc.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from backtest_engine import BacktestEngine, Bars
from strategy_base import Strategy


# ── Global Worker State ──────────────────────────────────────────

class WorkerState:
    def __init__(self, name: str):
        self.name = name
        self.lock = threading.Lock()
        
        # Data
        self.bars: Optional[Bars] = None
        self.bar_count: int = 0
        
        # Strategy
        self.strategy_name: str = ""
        self.strategy_class = None
        
        # Sweep state
        self.running: bool = False
        self.should_stop: bool = False
        self.total_combos: int = 0
        self.completed: int = 0
        self.results: list = []
        self.elapsed: float = 0.0
        self.error: str = ""

    def progress_pct(self) -> float:
        if self.total_combos == 0:
            return 0.0
        return self.completed / self.total_combos

    def status_str(self) -> str:
        if self.error:
            return "error"
        if self.running:
            return "running"
        if len(self.results) > 0:
            return "done"
        return "idle"


STATE: Optional[WorkerState] = None


# ── Sweep Runner ─────────────────────────────────────────────────

def run_sweep_chunk(state: WorkerState, config: dict, chunk_start: int, chunk_end: int):
    """Run a subset of the parameter grid."""
    try:
        from sweep import build_param_grid, _resolve_strategy
        
        settings = config.get("sweep_settings", {})
        fee_pct = settings.get("fee_pct", 0.035)
        initial_capital = settings.get("initial_capital", 10000.0)
        warmup_bars = settings.get("warmup_bars", 0)
        fill_on_bar_close = settings.get("fill_on_bar_close", False)
        calc_on_order_fills = settings.get("calc_on_order_fills", True)
        min_trades = settings.get("min_trades", 0)
        max_dd_pct = settings.get("max_dd_pct", 100.0)
        
        # Build full parameter grid, then slice our chunk
        strategy_class = _resolve_strategy(state.strategy_name)
        param_names, grid = build_param_grid(config.get("parameters", {}), strategy_class)
        
        # Clamp chunk to actual grid size
        chunk_end = min(chunk_end, len(grid))
        our_grid = grid[chunk_start:chunk_end]
        total = len(our_grid)
        
        state.total_combos = total
        state.completed = 0
        state.results = []
        state.error = ""
        
        print(f"  Running chunk [{chunk_start}..{chunk_end}] = {total} combos")
        
        t0 = time.time()
        results = []
        
        for i, params in enumerate(our_grid):
            if state.should_stop:
                print("  Sweep stopped by coordinator")
                break
            
            strategy = strategy_class()
            stats = BacktestEngine.run_fast(
                strategy, state.bars, params,
                initial_capital=initial_capital,
                fee_pct=fee_pct,
                warmup_bars=warmup_bars,
                fill_on_bar_close=fill_on_bar_close,
                calc_on_order_fills=calc_on_order_fills,
            )
            
            state.completed = i + 1
            
            if stats.total_trades < min_trades:
                continue
            if stats.max_drawdown_pct > max_dd_pct:
                continue
            
            results.append({
                "params": params,
                "pf": round(stats.profit_factor, 4),
                "net": round(stats.net_profit, 2),
                "dd": round(stats.max_drawdown, 2),
                "dd_pct": round(stats.max_drawdown_pct, 2),
                "wr": round(stats.win_rate, 1),
                "trades": stats.total_trades,
                "gross_profit": round(stats.gross_profit, 2),
                "gross_loss": round(stats.gross_loss, 2),
            })
        
        elapsed = time.time() - t0
        rate = total / max(elapsed, 0.001)
        
        state.results = results
        state.elapsed = elapsed
        state.running = False
        
        print(f"  Chunk done: {len(results)} results from {total} combos in {elapsed:.1f}s ({rate:.0f}/sec)")
        
    except Exception as e:
        state.error = str(e)
        state.running = False
        print(f"  Sweep error: {e}")
        import traceback
        traceback.print_exc()


# ── HTTP Handler ─────────────────────────────────────────────────

class WorkerHandler(BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        # Quieter logging
        pass
    
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)
    
    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return ""
        return self.rfile.read(length).decode("utf-8")
    
    def do_OPTIONS(self):
        self._send_json(200, {})
    
    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "name": STATE.name,
                "version": "2.0-python",
                "bars": STATE.bar_count,
                "strategy": STATE.strategy_name,
            })
        
        elif self.path == "/status":
            self._send_json(200, {
                "name": STATE.name,
                "running": STATE.running,
                "progress": STATE.progress_pct(),
                "total_combos": STATE.total_combos,
                "completed": STATE.completed,
                "result_count": len(STATE.results),
                "status": STATE.status_str(),
                "error": STATE.error,
                "elapsed": round(STATE.elapsed, 1),
            })
        
        elif self.path == "/results":
            self._send_json(200, {
                "results": STATE.results,
                "total_combos": STATE.total_combos,
                "elapsed": round(STATE.elapsed, 1),
            })
        
        else:
            self._send_json(404, {"error": f"Unknown endpoint: {self.path}"})
    
    def do_POST(self):
        body = self._read_body()
        
        if self.path == "/load-data":
            # Receive CSV data as body text
            try:
                lines = body.strip().split("\n")
                # Skip header
                data_lines = [l for l in lines[1:] if l.strip()]
                data = np.array([l.split(",") for l in data_lines], dtype=float)
                
                o = data[:, 1]
                h = data[:, 2]
                l = data[:, 3]
                c = data[:, 4]
                
                STATE.bars = Bars(
                    timestamp=data[:, 0],
                    open=o, high=h, low=l, close=c,
                    volume=data[:, 5] if data.shape[1] > 5 else np.zeros(len(data)),
                    hl2=(h + l) / 2.0,
                    hlc3=(h + l + c) / 3.0,
                    ohlc4=(o + h + l + c) / 4.0,
                    hlcc4=(h + l + c + c) / 4.0,
                )
                STATE.bar_count = len(data)
                print(f"  Loaded {STATE.bar_count:,} bars")
                self._send_json(200, {"bars": STATE.bar_count})
            except Exception as e:
                self._send_json(400, {"error": f"Failed to parse data: {e}"})
        
        elif self.path == "/load-strategy":
            # Receive strategy Python code + name
            try:
                payload = json.loads(body)
                name = payload["name"]
                code = payload.get("code", "")
                
                # If code provided, save to a worker-specific temp strategies dir
                # (avoid overwriting source files which can trigger Tauri dev rebuilds)
                if code:
                    import tempfile
                    worker_strategies_dir = os.path.join(tempfile.gettempdir(), "strategy_optimizer_worker", "strategies")
                    os.makedirs(worker_strategies_dir, exist_ok=True)
                    filepath = os.path.join(worker_strategies_dir, f"{name}.py")
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(code)
                    # Add temp dir to Python path so import can find it
                    parent = os.path.join(tempfile.gettempdir(), "strategy_optimizer_worker")
                    if parent not in sys.path:
                        sys.path.insert(0, parent)
                    print(f"  Saved strategy: {filepath}")
                    
                    # Reload module if already imported
                    mod_name = f"strategies.{name}"
                    if mod_name in sys.modules:
                        del sys.modules[mod_name]
                
                # Verify we can import it
                mod = importlib.import_module(f"strategies.{name}")
                # Use MRO name check to handle cross-path import identity issues
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if isinstance(attr, type) and attr.__name__ != "Strategy":
                        bases = [b.__name__ for b in attr.__mro__]
                        if "Strategy" in bases:
                            STATE.strategy_class = attr
                            STATE.strategy_name = name
                            print(f"  Strategy loaded: {name} ({attr.__name__})")
                            self._send_json(200, {"strategy": name, "class": attr.__name__})
                            return
                
                self._send_json(400, {"error": f"No Strategy subclass found in {name}"})
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_json(400, {"error": f"Failed to load strategy: {e}"})
        
        elif self.path == "/sweep":
            if STATE.running:
                self._send_json(409, {"error": "Sweep already running"})
                return
            
            if STATE.bars is None:
                self._send_json(400, {"error": "No data loaded. POST to /load-data first."})
                return
            
            if not STATE.strategy_name:
                self._send_json(400, {"error": "No strategy loaded. POST to /load-strategy first."})
                return
            
            try:
                payload = json.loads(body)
                config = payload["config"]
                chunk_start = payload.get("chunk_start", 0)
                chunk_end = payload.get("chunk_end", 0)
                
                STATE.running = True
                STATE.should_stop = False
                STATE.error = ""
                STATE.results = []
                STATE.elapsed = 0.0
                
                thread = threading.Thread(
                    target=run_sweep_chunk,
                    args=(STATE, config, chunk_start, chunk_end),
                    daemon=True,
                )
                thread.start()
                
                self._send_json(200, {
                    "started": True,
                    "chunk_start": chunk_start,
                    "chunk_end": chunk_end,
                })
            except Exception as e:
                STATE.running = False
                self._send_json(400, {"error": f"Invalid sweep config: {e}"})
        
        elif self.path == "/stop":
            STATE.should_stop = True
            self._send_json(200, {"stopped": True})
        
        else:
            self._send_json(404, {"error": f"Unknown endpoint: {self.path}"})


# ── Main ─────────────────────────────────────────────────────────

def get_local_ip():
    """Get the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser(description="Strategy Optimizer — Worker Node")
    parser.add_argument("--port", "-p", type=int, default=9876, help="Listen port (default: 9876)")
    parser.add_argument("--name", "-n", type=str, default=None, help="Worker name (default: hostname)")
    args = parser.parse_args()
    
    name = args.name or socket.gethostname()
    port = args.port
    local_ip = get_local_ip()
    
    global STATE
    STATE = WorkerState(name)
    
    print()
    print("=" * 50)
    print("  Strategy Optimizer -- Worker Node")
    print("=" * 50)
    print(f"  Name:    {name}")
    print(f"  Port:    {port}")
    print(f"  LAN IP:  {local_ip}")
    print(f"  Address: {local_ip}:{port}")
    print("-" * 50)
    print("  Endpoints:")
    print("    GET  /health        -- liveness check")
    print("    GET  /status        -- progress & stats")
    print("    POST /load-data     -- send CSV bar data")
    print("    POST /load-strategy -- send strategy .py")
    print("    POST /sweep         -- start sweep chunk")
    print("    GET  /results       -- retrieve results")
    print("    POST /stop          -- abort current sweep")
    print("=" * 50)
    print()
    print(f"  Add this address in the main app: {local_ip}:{port}")
    print()
    print("Waiting for jobs from coordinator...")
    print()
    
    server = HTTPServer(("0.0.0.0", port), WorkerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down worker...")
        server.shutdown()


if __name__ == "__main__":
    main()
