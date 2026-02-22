# Project-01 | AI-Powered Stock Research Platform

> **Pranav Ongole's Vision** — Build the research desk I always wanted: one that never sleeps,
> never misses a filing, and delivers institutional-grade conviction scores to any investor
> in under 60 seconds.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![Powered by Claude](https://img.shields.io/badge/AI-Claude%20API-orange)](https://anthropic.com)
[![Part of POV Series](https://img.shields.io/badge/Series-Pranav%20Ongole's%20Vision%20(POV)-blueviolet)](https://github.com/PranavOngole/Project-00)

---

## What This Platform Does

Project-01 is a **single-stock, deep-analysis engine**. Enter a ticker; receive a comprehensive
research report in seconds — the kind that would take a junior analyst half a day to assemble.

| Capability | Description |
|---|---|
| **Value Conviction Score** | Proprietary 0–100 composite score weighing fundamentals, technicals, sentiment, and competitive position |
| **Purchase Price Recommendation** | Agent-derived fair-value range with entry, target, and stop-loss levels |
| **Technical Analysis** | Moving averages, RSI, MACD, volume profile, support/resistance — rendered with interactive Plotly charts |
| **Fundamental Deep Dive** | Revenue trends, margins, balance sheet health, FCF, earnings quality, and ratio benchmarking |
| **Competitor Comparison** | Automated peer-set selection and side-by-side KPI table sourced live |
| **SEC Filing Digest** | Latest 10-K/10-Q highlights surfaced by the Finance Researcher agent |
| **QA-Validated Output** | Every report passes a dedicated QA agent before it reaches the UI |

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════╗
║                        PRESENTATION LAYER                        ║
║                                                                  ║
║   ┌─────────────────────────────────────────────────────────┐   ║
║   │              Streamlit Frontend  (app/main.py)          │   ║
║   │   Ticker Input ──► Report Pages ──► Interactive Charts  │   ║
║   └───────────────────────────┬─────────────────────────────┘   ║
╚═══════════════════════════════╪══════════════════════════════════╝
                                │
╔═══════════════════════════════╪══════════════════════════════════╗
║                    AGENT ORCHESTRATION LAYER                     ║
║                               │                                  ║
║              ┌────────────────▼───────────────┐                  ║
║              │       Manager Agent             │                  ║
║              │  (routes, sequences, merges)    │                  ║
║              └──┬──────┬──────┬──────┬────────┘                  ║
║                 │      │      │      │                            ║
║        ┌────────▼─┐ ┌──▼───┐ │  ┌───▼──────────┐                ║
║        │ Business │ │ Data │ │  │   Finance    │                 ║
║        │ Analyst  │ │ Eng. │ │  │  Researcher  │                 ║
║        └────────┬─┘ └──┬───┘ │  └───┬──────────┘                ║
║                 │      │     │      │                            ║
║        ┌────────▼─┐ ┌──▼───┐ │  ┌───▼──────────┐                ║
║        │Technical │ │ QA   │ │  │ Fundamental  │                 ║
║        │ Analyst  │ │Tester│ │  │   Analyst    │                 ║
║        └────────┬─┘ └──────┘ │  └───┬──────────┘                ║
║                 │            │      │                            ║
║        ┌────────▼────────────▼──────▼──────────┐                ║
║        │    Project Manager  +  AI Analyst      │                ║
║        │  (report assembly & conviction scoring) │               ║
║        └─────────────────────────────────────────┘               ║
╚══════════════════════════════════════════════════════════════════╝
                                │
╔═══════════════════════════════╪══════════════════════════════════╗
║                       DATA LAYER                                 ║
║                               │                                  ║
║              ┌────────────────▼───────────────┐                  ║
║              │         DuckDB  (local cache)   │                  ║
║              │   price history · fundamentals  │                  ║
║              │   peer tables  ·  report store  │                  ║
║              └────────────────┬───────────────┘                  ║
║                               │                                  ║
║              ┌────────────────▼───────────────┐                  ║
║              │    yfinance  (live market data) │                  ║
║              │  OHLCV · financials · metadata  │                  ║
║              └────────────────────────────────┘                  ║
╚══════════════════════════════════════════════════════════════════╝
```

**Agent Roster**

| Agent | Role |
|---|---|
| Manager | Orchestrates the full research pipeline; decides agent call order |
| Business Analyst | Synthesizes business model, moat, and strategic positioning |
| Data Engineer | Fetches, validates, and caches raw market and fundamental data |
| Finance Researcher | Surfaces SEC filings, earnings call highlights, analyst estimates |
| Technical Analyst | Computes indicators and interprets chart structure |
| Fundamental Analyst | Evaluates financial statements and calculates intrinsic value range |
| QA Tester | Validates report completeness, data freshness, and score consistency |
| Project Manager | Assembles section outputs into a coherent, structured report |
| AI Analyst | Produces the final Value Conviction Score and purchase price recommendation |

> Agent system prompts are loaded from environment variables at runtime and are **not stored in this repository**.

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Frontend | [Streamlit](https://streamlit.io) | Interactive web UI, no frontend build step |
| Charts | [Plotly](https://plotly.com/python/) | Interactive price charts, indicator overlays |
| Local DB | [DuckDB](https://duckdb.org) | Fast analytical queries on cached market data |
| Market Data | [yfinance](https://github.com/ranaroussi/yfinance) | Free OHLCV, financials, and metadata |
| AI / Agents | [Claude API (Anthropic)](https://docs.anthropic.com) | All nine research agents |
| Deployment | [Railway](https://railway.app) | Zero-config cloud hosting |
| Automation | [n8n](https://n8n.io) | Scheduled data refresh and alert workflows |

---

## Project Structure

```
Project-01/
│
├── app/                          # Streamlit application
│   ├── main.py                   # Entry point, sidebar, session state
│   ├── pages/                    # Multi-page app sections
│   │   ├── overview.py           # Executive summary & conviction score
│   │   ├── technical.py          # Chart suite & indicator dashboard
│   │   ├── fundamental.py        # Financial statement deep dive
│   │   └── competitors.py        # Peer comparison table
│   └── components/               # Reusable UI widgets
│       ├── score_gauge.py        # Value Conviction Score dial
│       ├── price_chart.py        # Annotated OHLCV chart
│       └── kpi_card.py           # Metric card component
│
├── agents/                       # Agent definitions (prompts via env)
│   ├── base_agent.py             # Shared Claude API wrapper & retry logic
│   ├── manager.py
│   ├── business_analyst.py
│   ├── data_engineer.py
│   ├── finance_researcher.py
│   ├── technical_analyst.py
│   ├── fundamental_analyst.py
│   ├── qa_tester.py
│   ├── project_manager.py
│   └── ai_analyst.py
│
├── data/                         # Data layer
│   ├── pipeline.py               # Fetch → validate → cache orchestration
│   ├── schema.py                 # DuckDB table definitions
│   └── cache.py                  # Cache read/write helpers
│
├── config/                       # Configuration & constants
│   ├── settings.py               # Env var loading, app-wide config
│   ├── ticker_universe.py        # NYSE/NASDAQ universe filters
│   └── prompts.py                # Prompt template keys (values from env)
│
├── tests/                        # Test suite
│
├── .env.example                  # Required environment variables (template)
├── .gitignore
├── LICENSE
└── README.md
```

---

## Stock Universe

Analysis is scoped to **NYSE and NASDAQ-listed equities** meeting all of the following criteria:

- Market capitalisation **>= $500M** (mid-cap and above)
- Minimum **2 years** of continuous price history in yfinance
- Active trading status (no OTC, pink sheets, or shell companies)

ADRs, ETFs, SPACs, and preferred shares are excluded from the default universe.

---

## Part of Pranav Ongole's Vision (POV) Series

This project is **Project-01** in a year-long, public build series.
Each project ships a working product and documents the full decision log.

| # | Project | Status |
|---|---|---|
| 00 | [POV Series — Kickoff & Manifesto](https://github.com/PranavOngole/Project-00) | Complete |
| 01 | AI-Powered Stock Research Platform | **In Progress** |
| 02–12 | Coming throughout 2026 | Planned |

---

## License

Distributed under the [MIT License](LICENSE). You are free to fork, adapt, and build on this work
with attribution.

---

## SEC Disclaimer

> **This platform is for informational and educational purposes only.**
> Nothing produced by Project-01 — including the Value Conviction Score, purchase price
> recommendations, or any analysis output — constitutes financial advice, investment advice,
> or a recommendation to buy or sell any security. Always conduct your own due diligence and
> consult a qualified financial professional before making investment decisions.
> Past performance of any stock referenced is not indicative of future results.
