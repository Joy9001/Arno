# Infrastructure & Hardware Architecture: Personal AI Financial Assistant

## 1. Executive Summary

This document outlines the infrastructure and hardware strategy for a privacy-first, self-hosted personal AI financial assistant. The primary architectural driver is **absolute data sovereignty**. To ensure zero exposure of unencrypted financial data to third-party cloud providers, the system (V1) will be hosted entirely on local, self-managed hardware.

## 2. Hardware Allocation

### Dedicated Local Server (The "Fortress")

- **Machine:** Asus TUF A15
- **Specifications:** AMD Ryzen 5 4600H (6-Core/12-Thread), 24 GB RAM, NVIDIA GTX 1650 Mobile (4GB VRAM).
- **Role:** This machine acts as the 24/7 dedicated local server. It will remain at the flat to ensure maximum possible uptime. The 24 GB of system RAM is sufficient for running an 8B parameter Small Language Model (SLM), utilizing the GTX 1650 for layer offloading to maintain conversational inference speeds.

### Excluded Hardware

- **Machine:** Apple MacBook Air M4 (Office)
- **Reason for Exclusion:** While highly capable for local LLM inference, this is a portable work machine. Relying on it for 24/7 server uptime introduces unacceptable downtime risks (lid closed, network switching). Furthermore, storing raw personal financial ledgers on company-managed hardware presents a privacy conflict.

## 3. Core Technology Stack

The stack prioritizes local execution, strict data typing, secure edge routing, and velocity. See [[Language Python over Go]] for the language rationale and [[Agent Framework PydanticAI]] for the agent framework rationale.

**Application layer (Python 3.12+):**
- **Packaging:** [`uv`](https://github.com/astral-sh/uv) — fast, modern Python package management (replaces pip/Poetry)
- **Web framework:** **FastAPI** — async, OpenAPI-native, Pydantic-backed
- **Agent framework:** **[PydanticAI](https://pydantic.dev/docs/ai/overview/)** v1.x — type-safe agents with native Ollama support
- **Money math:** `decimal.Decimal` + Pydantic v2 validators (recovers most of Go's compile-time safety)

**Data layer:**
- **Database:** Local **PostgreSQL** — single source of truth for the ledger
- **ORM:** **SQLAlchemy 2.0** (async) with Pydantic models at the boundary
- **Vector storage (when needed):** **pgvector** extension on the same Postgres instance
- **Memory layer (personalization):** **[mem0](https://mem0.ai/)** — low-lock-in fact-extraction memory bolted onto PydanticAI

**LLM & inference:**
- **LLM Engine:** **Ollama** running an 8B-class model (**Qwen3 8B** preferred in 2026) locally on the Asus
- **Hardware reality:** 4GB VRAM on the GTX 1650 → Q4 quantization + CPU offload required. Expect ~5–15 tok/sec.

**File processing:**
- **PDF (digital):** **pdfplumber** (tables) + **pymupdf4llm** (narrative)
- **PDF (password-protected):** **pikepdf**
- **OCR:** **pytesseract** (calls system `tesseract-ocr`)

**Interfaces:**
- **Telegram:** **aiogram** — async-native, asyncio-first
- **Web UI:** Vanilla HTML/JS over SSE (see [[FinAgent Chat Interface]] for architectural patterns)
- **Scheduler (briefings):** **APScheduler** — in-process, fits FastAPI lifespan

**Deploy & networking:**
- **Deployment:** **Docker Compose** (app + Postgres + Ollama as separate services)
- **Ingestion / Networking:** **Cloudflare Tunnels** (`cloudflared`) — securely exposes a local webhook endpoint to the internet (for Telegram) without opening home router ports or exposing the local network

## 4. Security & Backup Strategy

Because the cloud is treated as an untrusted environment, no plain-text data will ever leave the local network.

1. **Local Execution:** All LLM inference and database read/writes happen locally on the Asus TUF.
2. **Encrypted Cloud Backups:** To mitigate the single point of failure (the laptop dying), an automated cron job will execute the following pipeline nightly:
    - Run `pg_dump` to create a local snapshot of the PostgreSQL database.
    - Encrypt the SQL dump locally using strong encryption (e.g., AES-256 via OpenSSL or GPG).
    - Push the resulting encrypted blob to a cloud storage bucket (e.g., Cloudflare R2 or AWS S3).

## 5. Accepted V1 Trade-offs

By choosing a strictly local setup without cloud-based queueing or tunneling, we accept the **Downtime Trade-off**. If the Asus TUF loses power, enters sleep mode, or disconnects from the home Wi-Fi, incoming webhook messages from the chat interface will fail to process. Logging will need to be retried manually once the server is back online.