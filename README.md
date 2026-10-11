# wri-kas-bot

Automates monthly organization dues via WhatsApp with local SQLite WAL storage and asynchronous Google Sheets outbox synchronization.

## Architecture

```text
Evolution API (WhatsApp Gateway)
       │
       ▼ Webhook POST /webhook/whatsapp
FastAPI Application (Port 8090)
       │
       ├─► Gemini Flash Vision (Receipt OCR)
       ├─► Domain Engine (Strict FIFO Allocation, Balance Rollover)
       │
       ▼ ACID Storage
SQLite WAL Database (data/kas_wri.db)
       │
       ▼ Async Outbox Loop
Google Workspace Sync (Google Drive Proofs + Google Sheets Ledger)
```

### Key Engineering Decisions

- **Transactional Outbox Pattern**: Decouples WhatsApp webhook latency from third-party Google Sheets/Drive network failures. Transactions are committed locally with ACID guarantees before being pushed to remote sheets.
- **Strict FIFO Allocation**: Automatically allocates lump-sum or irregular payments across chronological delinquent months and tracks fractional balances.
- **Auditable Rollback**: Dedicated `/undo` and `/koreksi` mechanisms allow treasurers to safely revert mistaken approvals and clear corresponding spreadsheet cells using Google Sheets `batchClear`.
- **Multimodal AI with Human Verification**: Gemini Flash Vision extracts transfer amounts and bank metadata for confirmation cards; approvals remain strictly under treasurer authority.

## Requirements

- Python >= 3.12
- SQLite 3 (with WAL support)
- Evolution API v2 (WhatsApp gateway)
- Google Cloud Service Account (Google Sheets & Google Drive API enabled)

## Development

Set up environment and run tests:

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest
```
