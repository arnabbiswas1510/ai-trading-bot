# CAN SLIM AI Trading Bot — Project Context & Memory

## Project Overview
A premium growth-stock screening and paper-trading bot implementing the methodology described in William J. O'Neil's classic, *"How to Make Money in Stocks (Fourth Edition)"*.
This full-stack application scores watchlists, visualizes price breakouts with moving averages, simulates paper trading with automated risk boundaries, and runs historical backtests.

## Tech Stack & Architecture
- **Backend**: FastAPI (Python), SQLite3 Database, `yfinance` API.
- **Frontend**: React (Vite), Recharts, Lucide-React, premium Vanilla CSS with space-navy glassmorphic theme.
- **Start Command**: `python run_app.py` (automatically installs dependencies and starts backend on port 8000 and frontend on port 5173).

## Key Rules & Guidelines
- This application can be dockerized via Docker Compose (`docker-compose up --build -d`), which bundles the React static build outputs directly inside the FastAPI backend (serving from port `8000`).
- Paper-trading runs simulated orders and risk boundaries against the local SQLite database.

## Memory & Decisions
* Add project-specific technical decisions and active work tasks here to keep the agent aligned across sessions.
