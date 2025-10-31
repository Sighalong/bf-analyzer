# app.py - FastAPI wrapper to run the scraper on Render and serve outputs
import os
import subprocess
import shlex
from datetime import datetime
from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
import requests

APP_DIR = os.path.dirname(__file__)

def ensure_output_dir():
    # Use persistent dir if mounted, else /tmp
    default_path = os.path.join(APP_DIR, "outputs")
    wanted = os.environ.get("OUTPUT_DIR", default_path)
    try:
        os.makedirs(wanted, exist_ok=True)
        testfile = os.path.join(wanted, ".writetest")
        with open(testfile, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(testfile)
        return wanted, True
    except Exception:
        tmpdir = "/tmp/prisjakt_outputs"
        os.makedirs(tmpdir, exist_ok=True)
        return tmpdir, False

OUTPUT_DIR, HAS_PERSISTENT = ensure_output_dir()

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
        f"Storage: {'persistent disk' if HAS_PERSISTENT else 'ephemeral (/tmp)'}",
        "POST /run to trigger a scrape.",
        "GET  /files to browse output files via /files/<name>",
        "",
        "Current files:",
        *[f"- {name}" for name in files]
    ]
    return "\n".join(lines)

@app.post("/run", response_class=PlainTextResponse)
def run(categories: list[str] = Query(default=["TV","Mobiltelefoner","Bærbare PC-er","Hodetelefoner","Robotstøvsugere","Skjermer","Smartklokker"]),
        max_per_category: int = 20):
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
