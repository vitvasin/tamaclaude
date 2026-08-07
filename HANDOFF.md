# TamaClaude → Windows 11 migration — session handoff

Date: 2026-08-07. Written for a cold-start next session.

## What this project is

Porting `github.com/vitvasin/tamaclaude` (a Tamagotchi-style Claude Code desk monitor:
ESP32-2432S028R "CYD" + ILI9341 320×240 landscape, driven over BLE by a **macOS-only Swift
host**) to run on **Windows 11**. Plan file:
`C:\Users\005514\.claude\plans\clone-https-github-com-vitvasin-tamaclau-kind-comet.md`.

Repo cloned to **`D:\tamaclaude`** (git clone, HEAD `1934dd5`). Sibling `D:\claude-monitor-main\
claude-monitor-main` is the user's **own working CYD firmware** — ground truth for this panel.

## Status by phase

- **Phase 0 (toolchain) — DONE.** `D:\tamaclaude\.venv` (uv, CPython 3.11.15). `pip` deps:
  bleak, pytest, pillow, esptool installed. `python tools\export_layout.py` regenerates
  `firmware\main\layout.h` byte-identical; `tools\preview.py` renders; `tools\test_thai.py`
  passes. **Do NOT use the `hermes-agent` venv on PATH** — use `D:\tamaclaude\.venv`.
- **Phase 1 (firmware build) — DONE.** ESP-IDF v5.5 at `D:\esp\esp-idf`, tools at
  `D:\esp\tools` (IDF_TOOLS_PATH). Builds clean. **Build/flash incantation** (PowerShell,
  strip hermes from PATH or IDF's python-env step fails):
  ```powershell
  $env:PATH = (($env:PATH -split ';' | Where-Object { $_ -notmatch 'hermes' }) -join ';')
  $env:PATH = "C:\Users\005514\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none;$env:PATH"
  Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
  $env:IDF_TOOLS_PATH="D:\esp\tools"; . D:\esp\esp-idf\export.ps1
  Set-Location D:\tamaclaude\firmware; idf.py -p COM7 flash monitor
  ```
  **Board is on COM7** (CH340; the port number changes across replugs — re-check with
  `[System.IO.Ports.SerialPort]::GetPortNames()`).
- **Phase 2 (BLE spike) — DONE, verified on hardware.** See memory
  `tamaclaude-windows-ble-verified`. Key facts: char UUIDs are `7a9b000X-...-1d5c3f0a000X`
  (the macro doubles the last byte; CLAUDE.md's table misleads). MTU 517 (usable 514 ≥ 500).
  `CHR_CONFIG` (…0003) needs `BleakClient.pair()` first (returns None on WinRT — don't test
  truthiness). Board announces pages on `CHR_EVENT` subscribe: `{"t":"cap","p":[0,1,2,3,4]}`.
- **Phase 3 (host core loop) — DONE, 62 pytest green, verified end-to-end.** Python host at
  `D:\tamaclaude\host-win\tamaclaude\`: protocol.py, text.py, tool_map.py, session_store.py,
  process_tree.py, hook_client.py, ipc.py, ble.py, daemon.py, paths.py, __main__.py. Hooks →
  loopback TCP → daemon → BLE → board proven live. Run:
  `cd D:\tamaclaude\host-win; D:\tamaclaude\.venv\Scripts\python.exe -m tamaclaude --daemon -v`
- **Phase 4 (quota) — DONE, 118 pytest green.** Ported: `usage_reader.py`, `usage_writer.py`
  (foreign keys survive, percent-only-increases within a window), `usage_poll.py`
  (`--usage-poll`: reads `~/.tamaclaude/session-key` via `secret_file.py`, fetches claude.ai,
  writes cache, exit 0/2/3/1), `usage_poller.py` (tick-driven scheduler + `subprocess_launcher`),
  `secret_file.py`/`session_key_file.py` (Windows ACL lock at write via `icacls`),
  `settings_file.py`, `hook_installer.py`, `statusline_installer.py` (+ generated
  `~/.tamaclaude/statusline.ps1`), `autostart.py` (HKCU Run key). Daemon injects usage every
  tick and runs the poll timer. `--usage-cache`, `--install[-hook|-statusline]`,
  `--autostart` wired into `__main__`. **Entry point shipped**: `host-win/pyproject.toml` —
  run `uv pip install -e host-win` (the `.venv` is uv, no pip) so `tamaclaude` and
  `python -m tamaclaude` resolve from any cwd (needed for the hook command). Already installed
  this session. To finish setup on a machine: `tamaclaude --install`, then paste the claude.ai
  sessionKey into `~/.tamaclaude/session-key`.
- **Phase 5 (data pages) — weather + crypto done, 162 pytest green.** Page foundation
  `pages.py` (PageKind, PageFrame, PageRetire, PagePlan, PageHub with age-diffing + capability
  gating). Weather vertical: `weather.py` + `weather_service.py` (Open-Meteo, no key). Crypto
  vertical: `crypto.py` + `crypto_service.py` (CoinGecko, no key) — includes the sparkline fold
  with integer index math matching `ct_trend_fold`/`gen/trend.py`, both ends kept, last point =
  `now`; `text.grouped` added for thousands separators. The daemon parses the board
  `{"t":"cap","p":[...]}` announce, drains changed frames each tick → separate payloads, runs
  both fetch timers, submits a default rotation plan (mascot + each configured page). Configs
  (no GUI on Windows): `~/.tamaclaude/weather.json` `{"place":"Bangkok","unit":"C"}` and
  `~/.tamaclaude/crypto.json` `{"coins":["btc","eth"]}`. `--no-pages` disables all.
  Stocks vertical: `stocks.py` + `stocks_service.py` (Finnhub) — StocksFrame with the day-range
  band + market-closed key + its four-stage squeeze; `MarketHours` via `zoneinfo`
  America/New_York (needs the `tzdata` package on Windows — now a dependency); the Finnhub key
  reuses `secret_file` (mode-600/ACL rule) at `~/.tamaclaude/finnhub-key`; per-symbol fetch,
  unknown-symbol cache, last-quote replay when closed, key-rejected latch. Configured via
  `~/.tamaclaude/stocks.json` `{"symbols":["AAPL","MSFT"]}`. Wired into the daemon + rotation
  plan. **178 pytest green.**
  **Remaining phase 5:** calendar only (mac-only EventKit — skip on Windows; the four
  network-backed pages are all done). Phases 6-7 (tray UI, LAN) not started, independently
  abandonable.

## THE BLOCKER: display renders garbage — RESOLVED 2026-08-07

Fixed on hardware. Root cause was **the firmware's full ILI9341 power/gamma init block**, not
DMA, not transfer size, not MADCTL (all of which earlier sessions wrongly chased).

### What actually fixed it (all in `firmware/main/ct_lcd.c`)

1. **`panel_init` cut to the probe's minimal sequence + gamma only.** Removed the whole
   power-on/manufacturer group (`0xEF 0xCF 0xED 0xE8 0xCB 0xF7 0xEA`), power control
   (`0xC0 0xC1 0xC5 0xC7`), `0xB1`, `0xB6`. Kept: SWRESET, SLPOUT, COLMOD `0x55`, MADCTL,
   INVOFF, NORON, gamma (`0xF2 0x26 0xE0 0xE1`), DISPON. That block was the garbage; minimal
   init renders full-screen fills perfectly clean, gamma restores correct (non-washed) colour.
   Not bisected to the single guilty command — core+gamma already gives a complete image.
2. **MADCTL = `0x60` (MV | MX, BGR bit clear → panel is RGB).** Verified walk: `0x28` clean but
   mirrored → `0x68` upright but mascot orange showed **blue** (R/B swap) → `0x60` upright,
   non-mirrored, orange correct. **This panel is RGB, not BGR.**
3. **DMA disabled** (`SPI_DMA_DISABLED`), blit chunked to 64 B FIFO bursts, matching the
   known-good `Arduino_ESP32SPI` bus. This did NOT fix garbage on its own (tested), but it's
   the regime the working library uses on this panel, so it's kept as the defensive baseline.

### Diagnostic tooling left in place (harmless, for future bring-up)

- `ct_lcd.c` exposes `ct_lcd_blit_chunked(...,chunk)` — send at an arbitrary transaction size.
  `ct_lcd_blit` calls it with chunk=64. Kept in `ct_lcd.h` for the next panel bring-up.
- The TEMP diagnostic block in `main.c` was **removed**.

**Do NOT flash the prebuilt v1.2.2 again** — build from source so fixes are included.
Build/flash: use the incantation in Phase 1 below; `idf.py` needs IDF's own venv python — call
`D:\esp\tools\python_env\idf5.5_py3.11_env\Scripts\python.exe "$IDF_PATH\tools\idf.py" ...`
(the uv cpython on PATH shadows it and lacks `click`).

## Facts learned (also in memory files — read those at session start)

- `cyd-panel-madctl-differs`: **rewritten 2026-08-07 with the resolved facts** — garbage was
  the ILI9341 init block; MADCTL `0x60` (RGB, not BGR); DMA disabled. Accurate now, keep.
- `tamaclaude-windows-ble-verified`: accurate, keep.
- `cyd-touch-z1-only`: firmware already reads Z1 only; no change needed.
- BLE churn: Windows auto-reconnects the board once bonded (from `pair()`), causing rapid
  connect/disconnect in the serial log. Remove the bond (Settings ▸ Bluetooth ▸ tamaclaude-eeba
  ▸ Remove, or `pnputil /remove-device "BTHLE\DEV_20500D2CEEBA\..."` elevated). It came back
  after a reconnect — expect to redo it. Independent of the display bug.

## Immediate next step

Display is fixed and the Windows host (Phases 0-3) is done. **Next: Phase 4 (quota) — the ship
point.** Files to write under `host-win/tamaclaude/`: usage_reader/writer/poll/poller.py,
statusline.ps1 + .cmd shim, statusline_installer.py, hook_installer.py, autostart.py, plus a
console-script entry point so `tamaclaude --daemon` runs without the `cd`. Phases 5-7 (data
pages, UI, LAN) are independently abandonable.
