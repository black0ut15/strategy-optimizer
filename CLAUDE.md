# Strategy Optimizer — Zulu Foxtrot Alpha LLC

## Project Overview
Tauri/Rust desktop application with a Python backtesting engine for TradingView strategy optimization. Distributed backtesting workers on AWS EC2 (IP: 3.151.48.9, port 9876).

## Pine Script Rules
- ALWAYS write Pine Script in v6 syntax. Every script MUST start with `//@version=6`
- NEVER use deprecated v4/v5 syntax
- After writing or modifying ANY Pine Script code, ALWAYS validate it using the `validate_pine_script` MCP tool
- Fix ALL errors and warnings before considering the code complete
- Use proper v6 namespaces: ta., math., str., array., matrix., map., chart., etc.

## Key Architecture
- Pine Script strategies get translated to Python for backtesting
- The Python engine is a ranking/wind-tunnel tool, NOT for absolute PnL
- Post-translation validator checks for: _size_at_fill, fake VWAP, missing dateBlocked, contract_value, hasattr patterns
- contract_value (futures point multiplier) is implemented in engine/sweep/UI
- Market event date filter system (FOMC, NFP, CPI, Triple Witching, OpEx, Early Close, Post-Holiday) using Finnhub API with hardcoded fallbacks
- Sweep setting toggles block new entries on specified event dates; exits are unaffected
- Blocked dates feature passes serialized JSON through multiprocessing `spawn` initargs (child processes don't inherit parent globals)

## GitHub
- Account: black0ut15
- Tracked in Notion under Zulu Foxtrot Alpha LLC HQ workspace

## Finnhub API
- Key: d3seg11r01qvii72tlf0d3seg11r01qvii72tlfg
