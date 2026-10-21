# ai-trader-core

The local market-data service for **AI Trader**: daily NSE stock and NIFTY/BANKNIFTY data, end-of-day index-option snapshots, a secure Zerodha Kite login, and data-quality checks.
**This codebase contains no order placement.**

- Design: `docs/specs/2026-10-20-data-foundation-design.md`
- Product brief: `D:\ai-trader\AI_Trader_README.md`

## One-time setup (Windows, Git Bash or PowerShell)

1. Install and start **Docker Desktop**. Then run `docker compose up -d`, which starts Postgres on 127.0.0.1:5433.
2. Create the Python environment:
   ```
   python -m venv .venv
   .venv/Scripts/python -m pip install -e ".[dev]"
   ```
3. Copy `.env.example` to `.env`. Generate a `TOKEN_ENCRYPTION_KEY` with the command shown in the file. Fill in `KITE_API_KEY` and `KITE_API_SECRET` once you have a Kite Connect app. **Never commit `.env`.**
4. In the Kite developer console, set the app's redirect URL to `http://127.0.0.1:8765/local/kite/callback`.
5. Create the tables: `.venv/Scripts/python -m aitrader migrate`
6. Download the **Nifty 200 constituents CSV** by hand from niftyindices.com (Indices → Broad Market → NIFTY 200 → "Download Index Constituents"). Then run:
   `.venv/Scripts/python -m aitrader seed-universe --csv path/to/ind_nifty200list.csv`
7. Load about 5 years of daily history (it takes a while): `.venv/Scripts/python -m aitrader run-job backfill_history`
8. Fill in `aitrader/data/nse_holidays_2026.yaml` from NSE's official holiday circular.

## Daily use

- Start the service: `.venv/Scripts/python -m aitrader serve`. It serves http://127.0.0.1:8765 and runs these jobs on weekdays (IST):

  | Time | Job |
  |---|---|
  | 08:30 | instrument sync |
  | 15:35 | option snapshot |
  | 16:30 | daily bars |
  | 16:45 | quality checks |

- Open the dashboard (`D:\ai-trader`, `npm run dev`) and go to the **Data** tab. Click **Log in to Kite** each trading morning, before 15:35, so the option snapshot can run.
- Once your Kite data plan is active, run: `.venv/Scripts/python -m aitrader live-check`

## Tests

`docker compose up -d` and then `.venv/Scripts/python -m pytest -q`. Database tests are skipped, with a reason, if Postgres isn't running. CI requires them.

## Known limitations

- **Survivorship bias.** Delisted stocks are missing from free data, and Nifty 200 membership before the first seed is an explicit assumption.
- **yfinance prices are split-adjusted** and meant for personal use. Kite becomes the main source later.
- **No historical option prices** until vendor data is bought. Snapshots only build up from the day Kite is active.
