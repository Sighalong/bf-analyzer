# app.py - FastAPI wrapper to run the scraper on Render and serve outputs
import os
import subprocess
import shlex
from datetime import datetime
from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
import requests
import stat

APP_DIR = os.path.dirname(__file__)

def ensure_output_dir():
    # Decide output dir (default under /app)
    default_path = os.path.join(APP_DIR, "outputs")
    wanted = os.environ.get("OUTPUT_DIR", default_path)
    try:
        os.makedirs(wanted, exist_ok=True)
        testfile = os.path.join(wanted, ".writetest")
        with open(testfile, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(testfile)
        return wanted
    except Exception:
        tmpdir = "/tmp/prisjakt_outputs"
        os.makedirs(tmpdir, exist_ok=True)
        return tmpdir

def detect_persistent(path: str) -> bool:
    # 1) Allow explicit override
    override = os.environ.get("FORCE_STORAGE_MODE", "").strip().lower()
    if override in {"persistent", "ephemeral"}:
        return override == "persistent"

    # 2) Heuristic: different device than root or explicit mount point -> likely a mounted disk
    try:
        root_dev = os.stat("/").st_dev
        path_dev = os.stat(path).st_dev
        if os.path.ismount(path) or path_dev != root_dev:
            return True
    except Exception:
        pass

    # 3) Render Free: writable overlay looks like same device as '/'
    # Treat as ephemeral by default
    return False

OUTPUT_DIR = ensure_output_dir()
HAS_PERSISTENT = detect_persistent(OUTPUT_DIR)

app = FastAPI(title="Prisjakt Agent")

# Serve generated files (works even if OUTPUT_DIR is /tmp)
app.mount("/files", StaticFiles(directory=OUTPUT_DIR), name="files")

def upload_gist(file_map: dict, public=False):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None, "GITHUB_TOKEN not set; cannot upload Gist."
    files_payload = {}
    for name, path in file_map.items():
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                files_payload[name] = {"content": f.read()}
    if not files_payload:
        return None, "No files to upload."
    payload = {
        "public": public,
        "description": "Prisjakt agent output",
        "files": files_payload
    }
    r = requests.post("https://api.github.com/gists",
                      headers={"Authorization": f"token {token}",
                               "Accept": "application/vnd.github+json"},
                      json=payload, timeout=30)
    if r.status_code >= 300:
        return None, f"Gist upload failed: {r.status_code} {r.text[:200]}"
    url = r.json().get("html_url")
    return url, None

@app.get("/", response_class=PlainTextResponse)
def index():
    try:
        files = sorted(os.listdir(OUTPUT_DIR))
    except Exception:
        files = []
    lines = [
        "Prisjakt Agent is up.",
        f"Storage: {'persistent disk' if HAS_PERSISTENT else 'ephemeral (/tmp or container fs)'}",
        "POST /run to trigger a scrape.",
        "GET  /files to browse output files via /files/<name>",
        "",
        f"OUTPUT_DIR: {OUTPUT_DIR}",
        "",
        "Current files:",
        *[f"- {name}" for name in files]
    ]
    return "\n".join(lines)

@app.api_route("/run", methods=["GET", "POST"], response_class=PlainTextResponse)
def run(
    categories: list[str] = Query(default=["TV","Mobiltelefoner","Hodetelefoner","Skjermer"]),
    max_per_category: int = 6,   # lavere default for raskere kjøringer i nettleser
):
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_prefix = os.path.join(OUTPUT_DIR, f"prisjakt_{timestamp}")
    cmd = [
        "python", "prisjakt_agent.py",
        "--out-prefix", out_prefix,
        "--max-per-category", str(max_per_category)
    ]
    for c in categories:
        cmd += ["--categories", c]
    print("Running:", cmd, flush=True)
    try:
        res = subprocess.run(cmd, cwd=APP_DIR, text=True, capture_output=True, timeout=60*25)
    except subprocess.TimeoutExpired:
        return PlainTextResponse("Timed out while scraping.", status_code=504)

    # Summarize results
    csv_path = f"{out_prefix}.csv"
    md_path = f"{out_prefix}.md"
    lines = []
    lines.append("Scrape finished.")
    lines.append("Command: " + " ".join(shlex.quote(x) for x in cmd))
    lines.append("Return code: " + str(res.returncode))
    lines.append("--- stdout ---")
    lines.append(res.stdout[-2000:])
    lines.append("--- stderr ---")
    lines.append(res.stderr[-2000:])
    lines.append("--- outputs ---")
    if os.path.exists(csv_path): lines.append("/files/" + os.path.basename(csv_path))
    if os.path.exists(md_path):  lines.append("/files/" + os.path.basename(md_path))

    if not HAS_PERSISTENT:
        gist_url, err = upload_gist({
            os.path.basename(csv_path): csv_path,
            os.path.basename(md_path): md_path
        })
        if gist_url:
            lines.append(f"Gist: {gist_url}")
        else:
            lines.append(f"Gist: failed ({err})")

    return "\n".join(lines)
