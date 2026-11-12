# IntelliDoc AI

A full-stack AI document assistant starter. Phase 1 sets up the React frontend, FastAPI backend, environment configuration, and GitHub-ready repository structure.

## Phase 1 scope
- React + Vite + Tailwind CSS frontend
- FastAPI backend with health-check endpoint
- Frontend-to-backend API connection
- Environment variable templates
- Git-ready project structure

> Groq, PDF ingestion, RAG, Supabase, and DuckDuckGo are intentionally added in later phases. Phase 1 does not require API keys or paid services.

## Requirements
- Python 3.10+
- Node.js 20.19+ or 22.12+
- Git
- VS Code (recommended)

## 1. Run the backend

Open a terminal in the repository root:

### Windows PowerShell
```powershell
cd backend
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If PowerShell blocks activation, run:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

### macOS / Linux
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Backend checks:
- API health: http://127.0.0.1:8000/health
- Interactive API docs: http://127.0.0.1:8000/docs

Expected health response:
```json
{"status":"ok","service":"intellidoc-api","phase":1}
```

## 2. Run the frontend

Open a **second terminal** at the repository root:

```bash
cd frontend
npm install
```

Create the local frontend environment file:
- Windows PowerShell: `Copy-Item .env.example .env`
- macOS/Linux: `cp .env.example .env`

Then start Vite:
```bash
npm run dev
```

Open the local URL printed by Vite (usually http://localhost:5173).

The page calls the FastAPI `/health` endpoint and shows whether the backend is reachable.

## 3. Create the GitHub repository

1. Sign in to GitHub and choose **New repository**.
2. Repository name: `IntelliDoc-AI`
3. Choose Public or Private. Do not initialize with a README if you already have this project folder.
4. In a terminal at the project root, run:

```bash
git init
git add .
git commit -m "Initial IntelliDoc AI full-stack scaffold"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/IntelliDoc-AI.git
git push -u origin main
```

Replace `YOUR_GITHUB_USERNAME` with your GitHub username.

## 4. Environment variables and secrets

- `backend/.env` and `frontend/.env` are local-only files and are ignored by Git.
- Never commit API keys, database passwords, Supabase service-role keys, or `.env` files.
- Commit only `.env.example` files with placeholder values.
- In later phases, production secrets will be entered in the hosting provider's environment-variable settings.

## 5. Troubleshooting

**Frontend says backend is offline**
- Ensure the backend terminal is still running on port 8000.
- Check `frontend/.env` contains `VITE_API_BASE_URL=http://127.0.0.1:8000`.
- Restart the Vite dev server after changing `.env`.

**`npm` is not recognized**
- Install Node.js LTS, close and reopen the terminal, then run `node -v` and `npm -v`.

**`uvicorn` is not recognized**
- Activate the Python virtual environment and run `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`.

**CORS errors**
- Confirm the frontend origin in `backend/.env` matches the Vite URL. The default is `http://localhost:5173`.

## Next phases
2. Build the polished chat interface and document library.
3. Add PDF upload, extraction, chunking, and page-level metadata.
4. Generate embeddings and implement RAG retrieval.
5. Add Supabase PostgreSQL/pgvector and persistent chat/document storage.
6. Integrate Groq for answer generation and DuckDuckGo for fallback web search.
7. Add authentication, security, testing, and deployment.
