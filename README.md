# Strategy Optimizer

Desktop application for optimizing trading strategies. Upload Pine Script or Python strategies, configure parameter sweep ranges, and run distributed parallel optimization.

Built with Tauri (Rust + React) with a Python backtester engine.

## Architecture

```
Pine Script (.pine) → Translator → Python strategy (.py) ─┐
                                                            ├→ Python Backtester → Results
Python strategy (.py) ────────────────────────────────────┘
```

## Features
- **Strategy Lab**: Upload Pine Script or Python → auto-extract parameters → configure sweeps
- **Pine → Python Translator**: Converts Pine Script to editable Python
- **Python Backtester**: numpy-based, fast (summary) and detail (trade list) modes
- **Data Fetcher**: Crypto (Binance/Coinbase), Futures (InsightSentry), Stocks (Alpaca)
- **Results Viewer**: Sortable table, click any row for full trade list + equity curve
- **Distributed Sweep**: Coordinator/worker system across local network

## Quick Start (Windows)

### Prerequisites
1. Rust — `winget install Rustlang.Rust.MSVC`
2. Node.js 18+ — `winget install OpenJS.NodeJS`
3. Python 3.10+ — `winget install Python.Python.3.12`
4. VS Build Tools — `winget install Microsoft.VisualStudio.2022.BuildTools`

### Setup
```powershell
cd C:\dev\strategy-optimizer
python -m venv venv
.\venv\Scripts\Activate
pip install -r requirements.txt
npm install
copy .env.example .env   # Then fill in your API keys
```

### Run
```powershell
npm run tauri dev      # Development (hot reload)
npm run tauri build    # Build installer
```

## API Keys
| Provider | Key | Source |
|----------|-----|--------|
| Binance/Coinbase | None needed | Free public endpoints |
| InsightSentry | `RAPIDAPI_KEY` | rapidapi.com/insightsentry |
| Alpaca | `ALPACA_KEY` + `ALPACA_SECRET` | app.alpaca.markets |
