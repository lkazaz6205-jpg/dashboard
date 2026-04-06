# Deploy on Railway

## 1. Repo layout

Railway builds from the directory that contains `requirements.txt` and `app.py`. Point the Railway service **root directory** at that folder if the repo is a monorepo.

## 2. Deploy

1. Create a project on [Railway](https://railway.app/) → **New** → **GitHub Repo** (or empty service + connect repo).
2. Railway detects **Nixpacks** and runs `pip install -r requirements.txt` **during the build** (you do not run this as the start command).
3. **Start command** should run Streamlit. Prefer leaving **Custom Start Command** empty so `railway.json` applies, or paste exactly:

   ```bash
   streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false
   ```

### Common mistake (long queue / app never listens)

If **Settings → Deploy → Custom Start Command** is set to `pip install -r requirements.txt`, remove it or replace it with the Streamlit line above.

- `pip install` belongs in the **build**, not as the process that keeps running.
- A start command of only `pip install` never starts a web server on `$PORT`, so deploys look stuck or fail.
- **Dashboard “Custom Start Command” overrides** `railway.json` when it is set—empty it to use the repo config.

Generate a public URL: service → **Settings** → **Networking** → **Generate domain**.

## 3. Data files (important)

The container filesystem is **ephemeral**. Your `.xlsx` files are **not** on Railway unless you:

- **Commit them** in the repo (OK only if size and privacy allow), or  
- Add a **Railway Volume**, mount it (e.g. at `/data`), copy Excel files into the volume (SSH one-off, or a small sync job), then set:

| Variable    | Example | Meaning                          |
|------------|---------|----------------------------------|
| `DATA_DIR` | `/data` | Folder where `*.xlsx` live       |

Optional:

| Variable           | Example              | Meaning                    |
|--------------------|----------------------|----------------------------|
| `THRESHOLDS_PATH`  | `/data/thresholds.yaml` | Override thresholds file |

If `DATA_DIR` is wrong, the sidebar will show “No .xlsx files in that folder.”

## 4. Health check

`railway.json` uses `/_stcore/health` (Streamlit built-in). If deploys fail health checks, increase `healthcheckTimeout` or confirm your Streamlit version supports that path.

## 5. Local parity

```bash
export PORT=8501
export DATA_DIR=/path/to/your/xlsx
streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless true
```
