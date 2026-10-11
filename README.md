# wri-kas-bot

Automates monthly organization dues for Workshop Riset Informatika (WRI) via WhatsApp with local SQLite WAL storage and asynchronous Google Sheets outbox synchronization.

## Overview

1. Members submit payment proof images or check dues status via WhatsApp.
2. Inbound webhook triggers Gemini Flash Vision OCR to extract transfer amount and bank name.
3. Bot forwards receipt and extraction card to treasurer WhatsApp for review.
4. On approval (`/acc`), dues are allocated chronologically using strict FIFO queue logic. Any remainder rolls over into deposit balance.
5. An asynchronous outbox worker archives receipt images to Google Drive and syncs dues cells, member balances, and audit logs to Google Sheets without blocking WhatsApp responses.
6. Treasurers can roll back approvals (`/undo`) or adjust allocations (`/koreksi`), which clears spreadsheet cells via Google Sheets `batchClear`.

## Architecture

```text
Evolution API v2 (WhatsApp Gateway)
       │
       ▼ Webhook: POST /webhook or /webhook/wri
FastAPI Webhook Receiver
       │
       ├─► Gemini Flash Vision (Receipt OCR)
       ├─► Domain Engine (FIFO Allocation, Balance Tracking)
       │
       ▼ Local Storage
SQLite WAL Database (data/kas_wri.db)
       │
       ▼ Asynchronous Outbox Worker
Google Workspace Sync
       ├─► Google Drive (Receipt Image Archival)
       └─► Google Sheets (Matrix, Balance, Log Transaksi, Debit_Kredit)
```

## Commands

### Member Commands

- `/status` or `/cek`: Displays current deposit balance, cleared months, delinquent months, and net dues owed.
- `/bayar`: Displays payment methods, official bank account details, and QRIS instructions.

### Treasurer Commands

All moderation commands require sender JID to match `BENDAHARA_NUMBER`.

- `/acc [amount]`: Approves pending receipt. Swipe-reply an inbound receipt notification. Uses OCR-detected amount if omitted.
- `/reject [reason]`: Rejects pending receipt with an explanatory note to the member.
- `/tunai <phone> <amount>`: Records a direct cash payment, allocating dues and marking cell as "Tunai".
- `/undo`: Reverts the latest approved transaction, restoring balance, reopening delinquent months, and clearing Google Sheets cells.
- `/koreksi <new_amount>`: Atomically rolls back previous allocation and re-allocates with the corrected amount.
- `/tambahsaldo <phone> <amount>`: Adds credit to a member balance (e.g. committee surplus).
- `/setsaldo <phone> <amount>`: Sets an absolute balance for audit reconciliation.

## Configuration

Copy `.env.example` to `.env` and configure required values:

```bash
# Database & File Storage
DATABASE_URL=sqlite:///data/kas_wri.db
RECEIPTS_DIR=data/receipts

# Evolution API v2 (WhatsApp Gateway)
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=your_evolution_key
BOT_INSTANCE_NAME=kas-wri

# WhatsApp Configuration (International digits only, no + or dashes)
BENDAHARA_NUMBER=6281100000099
BOT_NUMBER=6281100000002
WEBHOOK_SECRET=your_webhook_secret

# Multimodal Vision OCR
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-1.5-flash

# Google Workspace Integration
GOOGLE_SPREADSHEET_ID=your_spreadsheet_id
DRIVE_PROOF_FOLDER_ID=your_drive_folder_id
GOOGLE_SERVICE_ACCOUNT_JSON=credentials.json

# Organization Payment Details
BANK_ACCOUNT_NUMBER=1000 0000 0001
BANK_ACCOUNT_HOLDER=Kas WRI / Bendahara
QRIS_FILE_URL=https://example.com/qris.jpg

# Billing Schedule (WIB)
DUE_AMOUNT_PER_MONTH=10000
BILLING_HOUR=19
BILLING_MINUTE=0
```

## Development

Requires Python >= 3.12 and `uv`.

```bash
# Install dependencies
uv sync

# Run linter and formatting checks
uv run ruff check .
uv run ruff format --check .

# Run test suite
uv run pytest

# Run service (web server, outbox worker, and billing scheduler)
uv run python -m wri_kas_bot.main

# Run stranded transaction reconciler CLI
uv run python -m wri_kas_bot.reconcile --dry-run
uv run python -m wri_kas_bot.reconcile --apply
```

## Deployment

### Production

Connects to existing `wri_network` and Evolution API gateway on port 8090:

```bash
# Ensure shared docker network exists
docker network create wri_network || true

# Build and start container
docker compose up -d --build
```

### Local Development

For standalone testing with local Evolution API, PostgreSQL, and Redis:

```bash
docker compose -f docker-compose.standalone.yml up -d --build
```
