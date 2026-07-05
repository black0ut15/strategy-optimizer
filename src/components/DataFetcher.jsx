import { useState, useCallback, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";

const CRYPTO_SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","AVAXUSDT","ADAUSDT","XRPUSDT","LTCUSDT","DOGEUSDT","BNBUSDT","DOTUSDT"];
const CRYPTO_SOURCES = [
  { value: "binance", label: "Binance Spot (free)" },
  { value: "futures", label: "Binance Futures USDT-M (free)" },
  { value: "coinbase", label: "Coinbase (free)" },
];

const FUTURES_SYMBOLS = {
  "Equity Index": ["ES","NQ","YM","RTY","MES","MNQ","MYM","M2K"],
  "Crypto": ["BTC","MBT","ETH","MET"],
  "FX": ["6E","6J","6B","6A","6C","6S","6N","6M","M6E","MJY"],
  "Energy": ["CL","NG","HO","RB","MCL","PA","PL"],
  "Metals": ["GC","SI","HG","MGC","SIL","MHG"],
  "Bonds": ["ZB","ZN","ZF","ZT","UB","TN"],
  "Grains": ["ZC","ZS","ZW","ZM","ZL","ZO","KE"],
  "Livestock": ["LE","HE","GF"],
  "Volatility": ["VX"],
};

// Unified intervals across all asset classes
const INTERVALS = ["1m","3m","5m","10m","15m","30m","1h","2h","4h","6h","1d"];

// Alpaca uses different interval format
const ALPACA_INTERVAL_MAP = {
  "1m": "1Min", "3m": "3Min", "5m": "5Min", "10m": "10Min", "15m": "15Min",
  "30m": "30Min", "1h": "1Hour", "2h": "2Hour", "4h": "4Hour", "6h": "6Hour", "1d": "1Day",
};

// Extra days to prepend for indicator warmup
// MESA needs ~500 bars, EMA(200) on 5m needs ~200*5min=~4 days of 24h data,
// but strategies with session logic (ORB, VWAP) need full trading days.
// Use generous buffers to ensure indicators are fully converged before trading.
const WARMUP_BUFFER_DAYS = {
  "1m": 5, "3m": 7, "5m": 14, "10m": 21, "15m": 30,
  "30m": 45, "1h": 60, "2h": 90, "4h": 120, "6h": 180, "1d": 500,
};

function ls(key, fallback) {
  try { const v = localStorage.getItem(key); return v !== null ? v : fallback; } catch { return fallback; }
}

export default function DataFetcher({ onDataLoaded, onLoadFile }) {
  const [assetClass, setAssetClass] = useState("crypto");
  const [fetching, setFetching] = useState(false);
  const [status, setStatus] = useState("");
  const [log, setLog] = useState("");

  // Crypto state
  const [cryptoSymbol, setCryptoSymbol] = useState(() => ls("df_cryptoSymbol", "BTCUSDT"));
  const [cryptoCustom, setCryptoCustom] = useState("");
  const [cryptoInterval, setCryptoInterval] = useState(() => ls("df_cryptoInterval", "5m"));
  const [cryptoDays, setCryptoDays] = useState(() => parseInt(ls("df_cryptoDays", "365")) || 365);
  const [cryptoSource, setCryptoSource] = useState(() => ls("df_cryptoSource", "binance"));
  const [cryptoDateMode, setCryptoDateMode] = useState(() => ls("df_cryptoDateMode", "days")); // "days" or "range"
  const [cryptoStartDate, setCryptoStartDate] = useState(() => ls("df_cryptoStartDate", ""));
  const [cryptoEndDate, setCryptoEndDate] = useState(() => ls("df_cryptoEndDate", ""));

  // Futures state
  const [futuresSymbol, setFuturesSymbol] = useState(() => ls("df_futuresSymbol", "ES"));
  const [futuresInterval, setFuturesInterval] = useState(() => ls("df_futuresInterval", "5m"));
  const [futuresDays, setFuturesDays] = useState(() => parseInt(ls("df_futuresDays", "365")) || 365);
  const [rapidapiKey, setRapidapiKey] = useState(() => ls("df_rapidapiKey", ""));

  // Stocks state
  const [stockSymbol, setStockSymbol] = useState(() => ls("df_stockSymbol", "SPY"));
  const [stockInterval, setStockInterval] = useState(() => ls("df_stockInterval", "5m"));
  const [stockDays, setStockDays] = useState(() => parseInt(ls("df_stockDays", "365")) || 365);
  const [alpacaKey, setAlpacaKey] = useState(() => ls("df_alpacaKey", ""));
  const [alpacaSecret, setAlpacaSecret] = useState(() => ls("df_alpacaSecret", ""));

  // Persist settings
  useEffect(() => { try { localStorage.setItem("df_cryptoSymbol", cryptoSymbol); } catch {} }, [cryptoSymbol]);
  useEffect(() => { try { localStorage.setItem("df_cryptoInterval", cryptoInterval); } catch {} }, [cryptoInterval]);
  useEffect(() => { try { localStorage.setItem("df_cryptoDays", String(cryptoDays)); } catch {} }, [cryptoDays]);
  useEffect(() => { try { localStorage.setItem("df_cryptoSource", cryptoSource); } catch {} }, [cryptoSource]);
  useEffect(() => { try { localStorage.setItem("df_cryptoDateMode", cryptoDateMode); } catch {} }, [cryptoDateMode]);
  useEffect(() => { try { localStorage.setItem("df_cryptoStartDate", cryptoStartDate); } catch {} }, [cryptoStartDate]);
  useEffect(() => { try { localStorage.setItem("df_cryptoEndDate", cryptoEndDate); } catch {} }, [cryptoEndDate]);
  useEffect(() => { try { localStorage.setItem("df_futuresSymbol", futuresSymbol); } catch {} }, [futuresSymbol]);
  useEffect(() => { try { localStorage.setItem("df_futuresInterval", futuresInterval); } catch {} }, [futuresInterval]);
  useEffect(() => { try { localStorage.setItem("df_futuresDays", String(futuresDays)); } catch {} }, [futuresDays]);
  useEffect(() => { try { if (rapidapiKey) localStorage.setItem("df_rapidapiKey", rapidapiKey); } catch {} }, [rapidapiKey]);
  useEffect(() => { try { localStorage.setItem("df_stockSymbol", stockSymbol); } catch {} }, [stockSymbol]);
  useEffect(() => { try { localStorage.setItem("df_stockInterval", stockInterval); } catch {} }, [stockInterval]);
  useEffect(() => { try { localStorage.setItem("df_stockDays", String(stockDays)); } catch {} }, [stockDays]);
  useEffect(() => { try { if (alpacaKey) localStorage.setItem("df_alpacaKey", alpacaKey); } catch {} }, [alpacaKey]);
  useEffect(() => { try { if (alpacaSecret) localStorage.setItem("df_alpacaSecret", alpacaSecret); } catch {} }, [alpacaSecret]);

  const handleFetch = useCallback(async () => {
    setFetching(true);
    setLog("");
    try {
      let result;
      let warmupBars = 0;
      if (assetClass === "crypto") {
        const sym = cryptoCustom || cryptoSymbol;
        const warmupDays = WARMUP_BUFFER_DAYS[cryptoInterval] || 3;
        const minsPerBar = {"1m":1,"3m":3,"5m":5,"10m":10,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"6h":360,"1d":1440}[cryptoInterval] || 5;
        warmupBars = Math.round(warmupDays * 1440 / minsPerBar);

        let fetchDays;
        if (cryptoDateMode === "range" && cryptoStartDate && cryptoEndDate) {
          // Calculate days from date range
          const startMs = new Date(cryptoStartDate + "T00:00:00Z").getTime();
          const endMs = new Date(cryptoEndDate + "T23:59:59Z").getTime();
          fetchDays = Math.ceil((endMs - startMs) / 86400000) + warmupDays;
          setStatus(`Fetching ${sym} ${cryptoInterval} (${cryptoStartDate} → ${cryptoEndDate} + ${warmupDays}d warmup) from ${cryptoSource}...`);
        } else {
          fetchDays = cryptoDays + warmupDays;
          setStatus(`Fetching ${sym} ${cryptoInterval} × ${cryptoDays}d + ${warmupDays}d warmup from ${cryptoSource}...`);
        }

        result = await invoke("fetch_crypto", {
          symbol: sym, interval: cryptoInterval, days: fetchDays, source: cryptoSource,
        });
      } else if (assetClass === "futures") {
        if (!rapidapiKey) { setStatus("Enter your RapidAPI key first"); setFetching(false); return; }
        const warmupDays = WARMUP_BUFFER_DAYS[futuresInterval] || 3;
        const totalDays = futuresDays + warmupDays;
        const minsPerBar = {"1m":1,"3m":3,"5m":5,"10m":10,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"6h":360,"1d":1440}[futuresInterval] || 5;
        warmupBars = Math.round(warmupDays * 1440 / minsPerBar);
        setStatus(`Fetching ${futuresSymbol} ${futuresInterval} × ${futuresDays}d + ${warmupDays}d warmup from InsightSentry...`);
        result = await invoke("fetch_futures", {
          symbol: futuresSymbol, interval: futuresInterval, days: totalDays, apiKey: rapidapiKey,
        });
      } else {
        if (!alpacaKey || !alpacaSecret) { setStatus("Enter Alpaca API credentials first"); setFetching(false); return; }
        const alpacaInterval = ALPACA_INTERVAL_MAP[stockInterval] || "5Min";
        const warmupDays = WARMUP_BUFFER_DAYS[stockInterval] || 3;
        const totalDays = stockDays + warmupDays;
        const minsPerBar = {"1m":1,"3m":3,"5m":5,"10m":10,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"6h":360,"1d":1440}[stockInterval] || 5;
        warmupBars = Math.round(warmupDays * 1440 / minsPerBar);
        setStatus(`Fetching ${stockSymbol} ${stockInterval} × ${stockDays}d + ${warmupDays}d warmup from Alpaca...`);
        result = await invoke("fetch_stocks", {
          symbol: stockSymbol, interval: alpacaInterval, days: totalDays,
          apiKey: alpacaKey, apiSecret: alpacaSecret,
        });
      }

      setStatus(`Fetched ${result.bars.toLocaleString()} bars (incl. ${warmupBars} warmup): ${result.symbol} (${result.first_date} → ${result.last_date})`);

      // Save warmup_bars so sweep settings can use it
      try { localStorage.setItem("so_warmup_bars", String(warmupBars)); } catch {}

      const info = await invoke("load_data", { path: result.csv_path });
      onDataLoaded({ ...info, warmup_bars: warmupBars });
      setStatus(`Loaded ${info.bars.toLocaleString()} bars (${warmupBars} warmup): ${info.symbol} — ready to optimize`);

    } catch (e) {
      setStatus(`Error: ${e}`);
      setLog(String(e));
    } finally {
      setFetching(false);
    }
  }, [assetClass, cryptoSymbol, cryptoCustom, cryptoInterval, cryptoDays, cryptoSource,
      futuresSymbol, futuresInterval, futuresDays, rapidapiKey,
      stockSymbol, stockInterval, stockDays, alpacaKey, alpacaSecret, onDataLoaded]);

  const barEstimate = (interval, days) => {
    const mins = {"1m":1,"3m":3,"5m":5,"10m":10,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240,"6h":360,"1d":1440};
    const m = mins[interval] || 5;
    const warmupDays = WARMUP_BUFFER_DAYS[interval] || 3;
    const tradingBars = Math.round(days * 1440 / m);
    const warmupBars = Math.round(warmupDays * 1440 / m);
    return { trading: tradingBars, warmup: warmupBars, total: tradingBars + warmupBars };
  };

  // ── Shared UI components ──

  const Input = ({label, value, onChange, type="text", placeholder=""}) => (
    <div className="flex items-center justify-between py-1.5">
      <label className="text-sm text-zinc-400">{label}</label>
      <input type={type} value={value}
        onChange={e => onChange(type === "number" ? (parseInt(e.target.value) || 0) : e.target.value)}
        placeholder={placeholder}
        className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 w-48 text-right focus:border-blue-500 focus:outline-none hover:border-zinc-600 transition-colors" />
    </div>
  );

  const Select = ({label, value, onChange, options}) => (
    <div className="flex items-center justify-between py-1.5">
      <label className="text-sm text-zinc-400">{label}</label>
      <div className="relative w-48">
        <select value={value} onChange={e => onChange(e.target.value)}
          className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 pr-8 focus:border-blue-500 focus:outline-none hover:border-zinc-600 transition-colors appearance-none cursor-pointer">
          {options.map(o => typeof o === "string"
            ? <option key={o} value={o}>{o}</option>
            : <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <svg className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500 pointer-events-none" viewBox="0 0 12 12" fill="none">
          <path d="M2.5 4.5L6 8L9.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </div>
    </div>
  );

  const DaysInput = ({label, value, onChange}) => (
    <div className="flex items-center justify-between py-1.5">
      <label className="text-sm text-zinc-400">{label}</label>
      <input type="number" value={value} min={1} max={3650}
        onChange={e => onChange(parseInt(e.target.value) || 1)}
        className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 w-48 text-right focus:border-blue-500 focus:outline-none hover:border-zinc-600 transition-colors" />
    </div>
  );

  return (
    <div className="max-w-lg mx-auto py-6 px-4">
      {/* Load existing file */}
      <button onClick={onLoadFile}
        className="w-full py-2.5 mb-5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded border border-zinc-700 transition-all text-sm">
        Load Existing CSV File
      </button>

      <div className="flex items-center gap-3 mb-5">
        <div className="h-px flex-1 bg-zinc-800" />
        <span className="text-xs text-zinc-600">OR FETCH FROM PROVIDER</span>
        <div className="h-px flex-1 bg-zinc-800" />
      </div>

      {/* Asset class tabs */}
      <div className="flex gap-1 mb-5 bg-zinc-900 rounded p-1">
        {[{k:"crypto",l:"Crypto"},{k:"futures",l:"Futures"},{k:"stocks",l:"Stocks"}].map(({k,l}) => (
          <button key={k} onClick={() => setAssetClass(k)}
            className={`flex-1 py-1.5 rounded text-sm transition-all ${
              assetClass === k ? "bg-zinc-700 text-zinc-100 font-medium" : "text-zinc-500 hover:text-zinc-300"
            }`}>{l}</button>
        ))}
      </div>

      {/* Crypto form */}
      {assetClass === "crypto" && (
        <div className="space-y-1">
          <Select label="Symbol" value={cryptoSymbol} onChange={setCryptoSymbol} options={CRYPTO_SYMBOLS} />
          <Input label="Custom Symbol" value={cryptoCustom} onChange={setCryptoCustom} placeholder="e.g. PEPEUSDT" />
          <Select label="Interval" value={cryptoInterval} onChange={setCryptoInterval} options={INTERVALS} />
          <Select label="Source" value={cryptoSource} onChange={setCryptoSource} options={CRYPTO_SOURCES} />

          {/* Date range mode toggle */}
          <div className="flex items-center justify-between py-1.5">
            <label className="text-sm text-zinc-400">Date Range</label>
            <div className="flex bg-zinc-800 rounded overflow-hidden border border-zinc-700 w-48">
              <button onClick={() => setCryptoDateMode("days")}
                className={`flex-1 py-1.5 text-xs transition-all ${cryptoDateMode === "days" ? "bg-zinc-600 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}`}>
                Days Back
              </button>
              <button onClick={() => setCryptoDateMode("range")}
                className={`flex-1 py-1.5 text-xs transition-all ${cryptoDateMode === "range" ? "bg-zinc-600 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}`}>
                Start / End
              </button>
            </div>
          </div>

          {cryptoDateMode === "days" ? (
            <DaysInput label="Days" value={cryptoDays} onChange={setCryptoDays} />
          ) : (
            <>
              <div className="flex items-center justify-between py-1.5">
                <label className="text-sm text-zinc-400">Start Date</label>
                <input type="date" value={cryptoStartDate} onChange={e => setCryptoStartDate(e.target.value)}
                  className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 w-48 focus:border-blue-500 focus:outline-none hover:border-zinc-600 transition-colors" />
              </div>
              <div className="flex items-center justify-between py-1.5">
                <label className="text-sm text-zinc-400">End Date</label>
                <input type="date" value={cryptoEndDate} onChange={e => setCryptoEndDate(e.target.value)}
                  className="bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 w-48 focus:border-blue-500 focus:outline-none hover:border-zinc-600 transition-colors" />
              </div>
            </>
          )}

          <div className="text-xs text-zinc-600 pt-2">
            {cryptoDateMode === "days"
              ? `~ ${barEstimate(cryptoInterval, cryptoDays).total.toLocaleString()} bars (${barEstimate(cryptoInterval, cryptoDays).warmup} warmup) · No API key needed`
              : cryptoStartDate && cryptoEndDate
                ? `${cryptoStartDate} → ${cryptoEndDate} + warmup · No API key needed`
                : "Select start and end dates · No API key needed"
            }
          </div>
        </div>
      )}

      {/* Futures form */}
      {assetClass === "futures" && (
        <div className="space-y-1">
          {/* Grouped Symbol Select */}
          <div className="flex items-center justify-between py-1.5">
            <label className="text-sm text-zinc-400">Symbol</label>
            <div className="relative w-48">
              <select value={futuresSymbol} onChange={e => setFuturesSymbol(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm text-zinc-200 pr-8 focus:border-blue-500 focus:outline-none hover:border-zinc-600 transition-colors appearance-none cursor-pointer">
                {Object.entries(FUTURES_SYMBOLS).map(([group, syms]) => (
                  <optgroup key={group} label={group}>
                    {syms.map(s => <option key={s} value={s}>{s}</option>)}
                  </optgroup>
                ))}
              </select>
              <svg className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500 pointer-events-none" viewBox="0 0 12 12" fill="none">
                <path d="M2.5 4.5L6 8L9.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
          </div>
          <Select label="Interval" value={futuresInterval} onChange={setFuturesInterval} options={INTERVALS} />
          <DaysInput label="Days" value={futuresDays} onChange={setFuturesDays} />
          <Input label="RapidAPI Key" value={rapidapiKey} onChange={setRapidapiKey} type="password" placeholder="InsightSentry Ultra key" />
          <div className="text-xs text-zinc-600 pt-2">
            ~ {barEstimate(futuresInterval, futuresDays).total.toLocaleString()} bars ({barEstimate(futuresInterval, futuresDays).warmup} warmup) ·
            Continuous contract via InsightSentry · Rate: ~2s/month
          </div>
        </div>
      )}

      {/* Stocks form */}
      {assetClass === "stocks" && (
        <div className="space-y-1">
          <Input label="Symbol" value={stockSymbol} onChange={setStockSymbol} placeholder="SPY, AAPL, QQQ..." />
          <Select label="Interval" value={stockInterval} onChange={setStockInterval} options={INTERVALS} />
          <DaysInput label="Days" value={stockDays} onChange={setStockDays} />
          <Input label="Alpaca API Key" value={alpacaKey} onChange={setAlpacaKey} type="password" placeholder="API Key ID" />
          <Input label="Alpaca Secret" value={alpacaSecret} onChange={setAlpacaSecret} type="password" placeholder="Secret Key" />
          <div className="text-xs text-zinc-600 pt-2">
            ~ {barEstimate(stockInterval, stockDays).total.toLocaleString()} bars ({barEstimate(stockInterval, stockDays).warmup} warmup) ·
            Live account for SIP feed · Market hours only for intraday
          </div>
        </div>
      )}

      {/* Fetch button */}
      <button onClick={handleFetch} disabled={fetching}
        className="w-full py-2.5 mt-5 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white text-sm rounded font-medium transition-all">
        {fetching ? "Fetching..." : "Fetch Data"}
      </button>

      {/* Status */}
      {status && (
        <div className="mt-4 text-xs text-zinc-400 bg-zinc-900 border border-zinc-800 rounded p-3 break-all">
          {status}
        </div>
      )}
      {log && (
        <details className="mt-2">
          <summary className="text-xs text-zinc-600 cursor-pointer">Show full output</summary>
          <pre className="mt-1 text-xs text-zinc-500 bg-zinc-900 border border-zinc-800 rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap">{log}</pre>
        </details>
      )}
    </div>
  );
}
