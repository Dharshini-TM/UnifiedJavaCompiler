#!/usr/bin/env python3
"""
Unified Java Repo Compiler (Single Script):
- Reads API URLs from api-new.csv
- For each API, fetches repos (GitHub REST API)
- Detects Maven/Gradle/Ant/Javac
- Compiles with multiple JDKs (8,11,17,21)
- Logs results into CSV (all, success, failed)
- Marks processed APIs in api-new.csv
"""

import os
import subprocess
import tempfile
import csv
import shutil
import time
import signal
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import requests
import pandas as pd

# ===================== CONFIG =====================
APIS_CSV = "api-new.csv"
INPUT_CSV = "check.csv"     # optional local repos
PROCESS_LOCAL_CSV = True

MAX_WORKERS = 4
CLONE_TIMEOUT = 300
COMPILE_TIMEOUT = 600

# Paths
JDK_PATHS = {
    "jdk8":  r"C:\Users\Lenovo\Downloads\jdk-8u451-windows-x64\jdk1.8.0_451",
    "jdk11": r"C:\Users\Lenovo\Downloads\jdk-11.0.27_windows-x64_bin\jdk-11.0.27",
    "jdk17": r"C:\Users\Lenovo\Downloads\jdk-17.0.12_windows-x64_bin\jdk-17.0.12",
    "jdk21": r"C:\Users\Lenovo\Downloads\jdk-21.0.7_windows-x64_bin\jdk-21.0.7",
}
MAVEN_PATH = r"C:\Users\Lenovo\Downloads\apache-maven-3.9.11\bin\mvn.cmd"
GRADLE_PATH = r"C:\Users\Lenovo\Downloads\gradle-8.14.3-bin\gradle-8.14.3\bin\gradle.bat"
ANT_PATH = r"C:\Users\Lenovo\Downloads\apache-ant-1.10.15-bin\apache-ant-1.10.15\bin\ant.bat"

ALL_RESULTS_CSV     = "compilation_results.csv"
SUCCESS_RESULTS_CSV = "compilation_success.csv"
FAILED_RESULTS_CSV  = "compilation_failed.csv"
PROCESSED_FILE      = "processed_repos.txt"

# GitHub token (optional for higher rate limit)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # read from environment


stop_requested = False

# ===================== SIGNAL =====================
def signal_handler(sig, frame):
    global stop_requested
    print("\nStop signal received. Finishing current tasks...")
    stop_requested = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ===================== HELPERS =====================
def ensure_file_exists(path):
    Path(path).touch(exist_ok=True)

def clean_message(msg: str, limit: int = 300) -> str:
    if not msg:
        return ""
    cleaned = msg.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "..."
    return cleaned

def load_processed():
    ensure_file_exists(PROCESSED_FILE)
    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_processed(url):
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def append_csv(path, row, header):
    write_header = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(header)
        w.writerow(row)

CSV_HEADER = [
    "Repo Name","Repo URL","Description","Build Tool","Status",
    "Compile Time (s)","Message","NOF","LOC","Repo Size"
]

def append_all(row):    append_csv(ALL_RESULTS_CSV, row, CSV_HEADER)
def append_success(row):append_csv(SUCCESS_RESULTS_CSV, row, CSV_HEADER)
def append_failed(row): append_csv(FAILED_RESULTS_CSV, row, CSV_HEADER)

def format_size(size_bytes):
    size = float(size_bytes)
    for unit in ["B","KB","MB","GB","TB"]:
        if size < 1024: return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

# ===================== API CSV DRIVER =====================
def load_api_rows():
    rows = []
    if not os.path.exists(APIS_CSV):
        return rows
    with open(APIS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows

def save_api_rows(rows):
    fieldnames = ["api_url", "processed", "last_status"]
    with open(APIS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            out = {
                "api_url": (r.get("api_url") or "").strip(),
                "processed": (r.get("processed") or "").strip(),
                "last_status": (r.get("last_status") or "").strip(),
            }
            writer.writerow(out)

def ensure_api_csv_header():
    if not os.path.exists(APIS_CSV):
        save_api_rows([])

def fetch_repositories_from_api(api_url):
    try:
        headers = {}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        resp = requests.get(api_url, headers=headers, timeout=60)
        if resp.status_code != 200:
            print(f"API error {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        elif isinstance(data, list):
            if all(isinstance(x, str) for x in data):
                return [{"html_url": u, "description": "NA"} for u in data]
            elif all(isinstance(x, dict) for x in data):
                return data
        return []
    except Exception as e:
        print(f"API fetch failed: {e}")
        return []

# ===================== BUILD TOOL DETECTION =====================
def detect_build_tool(repo_dir: Path):
    if (repo_dir / "pom.xml").exists(): return "Maven"
    elif (repo_dir / "build.gradle").exists() or (repo_dir / "build.gradle.kts").exists(): return "Gradle"
    elif (repo_dir / "build.xml").exists(): return "Ant"
    else: return "Javac"

def run_command(cmd, cwd=None, env=None):
    try:
        # If the command is a batch file (.bat or .cmd), run via cmd.exe
        if cmd[0].endswith(".bat") or cmd[0].endswith(".cmd"):
            cmd = ["cmd.exe", "/c"] + cmd
        result = subprocess.run(
            cmd, cwd=cwd, env=env,
            capture_output=True, text=True,
            timeout=COMPILE_TIMEOUT, shell=False
        )
        return result.returncode, (result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        return 1, "Timeout expired"


def count_java_files_and_loc(repo_dir: Path):
    nof = 0; loc = 0
    for f in repo_dir.rglob("*.java"):
        nof += 1
        try:
            loc += sum(1 for _ in open(f,"r",encoding="utf-8",errors="ignore"))
        except: pass
    return nof, loc

def get_repo_size(repo_path):
    total = 0
    for root, _, files in os.walk(repo_path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root,f))
            except:
                pass
    return total

# ===================== UNIFIED COMPILER =====================
def compile_with_jdks(repo_dir: Path, build_tool: str):
    last_elapsed = 0
    for jdk, path in JDK_PATHS.items():
        env = os.environ.copy()
        env["JAVA_HOME"] = path
        env["PATH"] = os.path.join(path, "bin") + os.pathsep + env["PATH"]

        if build_tool == "Maven":
            cmd = [MAVEN_PATH, "clean", "install", "-DskipTests", "-U"]

        elif build_tool == "Gradle":
            gradlew = repo_dir / "gradlew"
            if gradlew.exists():
                cmd = [str(gradlew), "build", "-x", "test", "--refresh-dependencies"]
            else:
                cmd = [GRADLE_PATH, "build", "-x", "test", "--refresh-dependencies"]

        elif build_tool == "Ant":
            cmd = [ANT_PATH]

        else:  # Javac
            src_roots = [repo_dir / "src" / "main" / "java",
                         repo_dir / "src",
                         repo_dir]
            java_files = []
            for root in src_roots:
                if root.exists():
                    java_files.extend(root.rglob("*.java"))

            if not java_files:
                return False, clean_message("No Java files"), 0

            out_dir = repo_dir / "out"
            out_dir.mkdir(exist_ok=True)

            jars = list(repo_dir.rglob("lib/*.jar")) + list(repo_dir.rglob("libs/*.jar"))
            cp = "."
            if jars:
                cp += os.pathsep + os.pathsep.join(str(j) for j in jars)

            sources_file = repo_dir / "sources.txt"
            with open(sources_file, "w", encoding="utf-8") as sf:
                for f in java_files:
                    sf.write(str(f) + "\n")

            cmd = ["javac","-encoding","UTF-8","-d",str(out_dir),"-cp",cp,f"@{sources_file}"]

        start = time.time()
        code, out = run_command(cmd, cwd=repo_dir, env=env)
        last_elapsed = round(time.time() - start, 2)

        if code == 0:
            return True, clean_message(f"Compiled successfully ({build_tool}, {jdk})"), last_elapsed

    return False, clean_message(f"{build_tool} failed on all JDKs"), max(0.01, last_elapsed)

# ===================== PROCESS REPO =====================
def process_repo(repo_url, description="NA", force_recompile=False):
    repo_name = repo_url.rstrip("/").split("/")[-1]

    if not force_recompile and repo_url in load_processed():
        return

    tmp_root = Path(tempfile.mkdtemp())
    repo_dir = tmp_root / repo_name

    try:
        clone_url = repo_url
        if GITHUB_TOKEN and repo_url.startswith("https://github.com/"):
            clone_url = repo_url.replace("https://", f"https://{GITHUB_TOKEN}@")

        code, out = run_command(["git","clone","--depth","1",clone_url,str(repo_dir)])
        if code != 0:
            row = [repo_name, repo_url, description, "NA", "Failed", 0,
                   clean_message("Clone Failed"), 0, 0, "NA"]
            append_all(row); append_failed(row); save_processed(repo_url)
            return

        build_tool = detect_build_tool(repo_dir)
        success, msg, comp_time = compile_with_jdks(repo_dir, build_tool)
        nof, loc = count_java_files_and_loc(repo_dir)
        size_bytes = get_repo_size(repo_dir)
        row = [repo_name, repo_url, description, build_tool,
               "Success" if success else "Failed", comp_time,
               clean_message(msg), nof, loc,
               format_size(size_bytes)]
        append_all(row)
        (append_success if success else append_failed)(row)
        save_processed(repo_url)
        print(f"[{'Success' if success else 'Failed'}] {repo_name} — files:{nof} loc:{loc} size:{format_size(size_bytes)} time:{comp_time}s")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

# ===================== MAIN =====================
def main():
    print("Driver with hot reload started. Watching api-new.csv for new rows...")
    ensure_file_exists(PROCESSED_FILE)
    ensure_api_csv_header()
    idle_ticks = 0
    HEARTBEAT_EVERY = 6
    POLL_INTERVAL_WHEN_IDLE = 5.0  # seconds

    while not stop_requested:
        rows = load_api_rows()

        # Find first unprocessed API
        idx = None
        for i, r in enumerate(rows):
            if (r.get("processed") or "").lower() != "true" and (r.get("api_url") or "").strip():
                idx = i
                break

        if idx is None:
            idle_ticks += 1
            if idle_ticks % HEARTBEAT_EVERY == 0:
                print(f"[idle] No pending APIs. Polling again in {POLL_INTERVAL_WHEN_IDLE}s …")
            time.sleep(POLL_INTERVAL_WHEN_IDLE)
            continue

        idle_ticks = 0
        api_url = (rows[idx].get("api_url") or "").strip()
        print(f"\n🌐 Processing API: {api_url}")

        try:
            repos = fetch_repositories_from_api(api_url)
            if not repos:
                print("⚠️ No repos from API")
            else:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                    futures = [
                        ex.submit(
                            process_repo,
                            it.get("html_url"),
                            it.get("description") if it.get("description") else "NA"
                        )
                        for it in repos if it.get("html_url")
                    ]
                    for fut in futures:
                        try:
                            fut.result()
                        except Exception as e:
                            print("Worker error:", e)

            rows[idx]["last_status"] = "ok"
        except KeyboardInterrupt:
            print("\n⛔ Interrupted by user. Marking this API row.")
            rows[idx]["last_status"] = "interrupted"
            rows[idx]["processed"] = "true"
            save_api_rows(rows)
            raise
        except Exception as e:
            rows[idx]["last_status"] = f"error: {e}"
        finally:
            rows[idx]["processed"] = "true"
            save_api_rows(rows)

        time.sleep(1.0)  # short cooldown between jobs
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Fatal error in main(): {e}")
