# Free Register Tool

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/Node.js-18%2B-339933?style=for-the-badge&logo=node.js&logoColor=white" alt="Node.js 18+" />
  <img src="https://img.shields.io/badge/FastAPI-Backend-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/React-Frontend-61DAFB?style=for-the-badge&logo=react&logoColor=0A0A0A" alt="React" />
  <img src="https://img.shields.io/badge/License-MIT-F7C948?style=for-the-badge" alt="MIT License" />
</p>

<p align="center">
  <strong>A self-hosted account registration and operations workspace</strong><br />
  FastAPI backend, React admin UI, plugin-based platform integrations, mailbox adapters, proxy management, scheduling, and external sync support.
</p>

<p align="center">
  <a href="README_CN.md">Simplified Chinese</a>
</p>

---

## Contents

- [Overview](#overview)
- [Screenshots](#screenshots)
- [Highlights](#highlights)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Typical Workflow](#typical-workflow)
- [Configuration Notes](#configuration-notes)
- [Testing](#testing)
- [Privacy And Safe Publishing](#privacy-and-safe-publishing)
- [Docker](#docker)
- [Development Notes](#development-notes)
- [License](#license)

## Overview

Free Register Tool is built for local deployment, controlled automation, development, and research workflows.
It combines a backend API, a responsive admin interface, and platform-specific plugins so you can manage:

- accounts
- registration tasks
- mailboxes
- proxies
- logs
- scheduled jobs
- external sync workflows

Use it only in ways that comply with platform terms, local law, and your own risk controls.

## Screenshots

### Dashboard

![Dashboard](docs/images/dashboard.png)

### Settings Center

![Settings Overview](docs/images/settings-overview.png)

## Highlights

- Plugin-based multi-platform workflow architecture under `platforms/`
- Responsive admin UI for accounts, tasks, settings, logs, and proxies
- Batch registration with progress tracking and resumable task state
- Mailbox provider abstraction for temporary mail and self-hosted mail flows
- Captcha and browser automation support for supported registration pipelines
- ChatGPT-oriented token management, local probing, payment-link retrieval, and Sub2API sync
- Scheduled execution for recurring jobs
- Safer repository defaults for local secrets, logs, and debug artifacts

## Tech Stack

| Layer | Stack |
|---|---|
| Backend | FastAPI, Uvicorn, SQLModel, APScheduler |
| Frontend | React, TypeScript, Vite, Ant Design |
| Automation | Playwright, Camoufox |
| Networking | `curl_cffi`, `httpx` |
| Storage | SQLite |

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/0311119/Free_RegisterTool.git
cd Free_RegisterTool
```

### 2. Create a Python environment

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
source .venv/bin/activate
```

### 3. Install backend dependencies

```bash
pip install -r requirements.txt
```

### 4. Install browser automation dependencies

```bash
python -m playwright install chromium
python -m camoufox fetch
```

### 5. Install frontend dependencies

```bash
cd frontend
npm install
npm run build
cd ..
```

### 6. Create local config

```bash
cp .env.example .env
```

Then edit `.env` for your local environment and integrations.

### 7. Start the backend

```bash
python main.py
```

Default API docs:

```text
http://localhost:8000/docs
```

### Frontend development

```bash
cd frontend
npm install
npm run dev
```

Default dev URL:

```text
http://localhost:5173
```

## Typical Workflow

1. Configure mailbox, captcha, proxy, and external sync settings in `Settings`.
2. Launch batch registration from `Register Task`.
3. Review generated accounts in `Accounts`.
4. Run local probe or remote sync actions when needed.
5. Use `Task History` and `Scheduled Tasks` for long-running maintenance flows.

## Configuration Notes

The project reads configuration from `.env` plus persisted runtime settings stored by the app.

Common categories:

- server host and port
- captcha solver settings
- proxy settings
- mailbox provider credentials
- external sync endpoints such as Sub2API
- platform-specific runtime options

Start from [`.env.example`](./.env.example).

## Testing

```bash
pytest tests/
```

Some repository scripts are operator helpers rather than repeatable tests. Prefer the `tests/` suite for validation.

## Privacy And Safe Publishing

This repository is configured to keep local machine state, secrets, and personal runtime artifacts out of version control.

Ignored local-only content includes:

- `.env`
- `data/`
- `logs/`
- `runtime/`
- `static/`
- `*.db`, `*.sqlite`, `*.sqlite3`
- local tokens, Gmail OAuth files, temporary screenshots, and debug logs
- local helper scripts and machine-specific operational files

Before publishing your own fork, double-check that you are not committing:

- API keys
- mailbox credentials
- OAuth token exports
- backend logs
- screenshots containing private account data
- screenshots showing local paths, ports, or machine-specific admin pages

## Docker

Basic Docker workflow:

```bash
docker-compose up -d
docker-compose logs -f
docker-compose down
```

## Development Notes

- Add new platform logic under `platforms/`
- Shared abstractions live under `core/`
- API routes live under `api/`
- Frontend pages live under `frontend/src/pages/`
- UI primitives live under `frontend/src/components/` and `frontend/src/components/ui/`

## License

MIT. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
