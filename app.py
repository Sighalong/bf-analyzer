# app.py - FastAPI wrapper to run the scraper on Render and serve outputs
import os
import subprocess
import shlex
from datetime import datetime
from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(APP_DIR, "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="Prisjakt Agent")

# Serve generated files
app.mount("/files", StaticFiles(directory=OUTPUT_DIR), name="files")

@app.get("/", response_class=PlainTextResponse)
def index():
    files = sorted(os.listdir(OUTPUT_DIR))
    lines = [
        "Prisjakt Agent is up.",
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
    lines.append(res.stdout[-2000:])  # last part
    lines.append("--- stderr ---")
    lines.append(res.stderr[-2000:])
    lines.append("--- outputs ---")
    if os.path.exists(csv_path): lines.append("/files/" + os.path.basename(csv_path))
    if os.path.exists(md_path):  lines.append("/files/" + os.path.basename(md_path))
    return "\n".join(lines)
