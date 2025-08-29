#!/usr/bin/env python3
import os, subprocess, tempfile, csv, shutil, time, signal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests
import pandas as pd
from dotenv import load_dotenv

# ===================== ENV =====================
load_dotenv()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# ===================== CONFIG =====================
APIS_CSV = "api-new.csv"
ALL_RESULTS_CSV = "compilation_results.csv"
SUCCESS_RESULTS_CSV = "compilation_success.csv"
FAILED_RESULTS_CSV = "compilation_failed.csv"
PROCESSED_FILE = "processed_repos.txt"

MAX_WORKERS = 4
COMPILE_TIMEOUT = 600

# ===================== BUILD TOOL PATHS =====================
JDK_PATHS = {
    "jdk8": "/usr/lib/jvm/java-8-openjdk",
    "jdk11": "/usr/lib/jvm/java-11-openjdk",
    "jdk17": "/usr/lib/jvm/java-17-openjdk"
}
MAVEN_PATH = "/usr/bin/mvn"
GRADLE_PATH = "/usr/bin/gradle"
ANT_PATH = "/usr/bin/ant"

# ===================== SIGNAL HANDLER =====================
stop_requested = False
def signal_handler(sig, frame):
    global stop_requested
    print("\nStop signal received. Finishing current tasks...")
    stop_requested = True
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ===================== HELPERS =====================
def ensure_file_exists(path): Path(path).touch(exist_ok=True)
def clean_message(msg, limit=300):
    if not msg: return ""
    cleaned = " ".join(msg.replace("\r"," ").replace("\n"," ").replace("\t"," ").split())
    return (cleaned[:limit]+"...") if len(cleaned)>limit else cleaned

def load_processed():
    ensure_file_exists(PROCESSED_FILE)
    with open(PROCESSED_FILE,"r") as f: return set(line.strip() for line in f if line.strip())
def save_processed(url):
    with open(PROCESSED_FILE,"a") as f: f.write(url+"\n")

def append_csv(path,row,header):
    write_header = not Path(path).exists()
    with open(path,"a",newline="") as f:
        w = csv.writer(f)
        if write_header: w.writerow(header)
        w.writerow(row)
CSV_HEADER = ["Repo Name","Repo URL","Description","Build Tool","Status","Compile Time (s)","Message","NOF","LOC","Repo Size"]
def append_all(row): append_csv(ALL_RESULTS_CSV,row,CSV_HEADER)
def append_success(row): append_csv(SUCCESS_RESULTS_CSV,row,CSV_HEADER)
def append_failed(row): append_csv(FAILED_RESULTS_CSV,row,CSV_HEADER)

# ===================== FETCH REPOS =====================
def fetch_repositories_from_api(api_url):
    try:
        headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
        resp = requests.get(api_url, headers=headers, timeout=60)
        if resp.status_code != 200:
            print(f"API error {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        if isinstance(data, dict) and "items" in data: return data["items"]
        elif isinstance(data, list):
            if all(isinstance(x,str) for x in data): return [{"html_url":u,"description":"NA"} for u in data]
            elif all(isinstance(x,dict) for x in data): return data
        return []
    except Exception as e:
        print(f"API fetch failed: {e}")
        return []

# ===================== BUILD TOOL DETECTION =====================
def detect_build_tool(repo_dir: Path):
    if (repo_dir/"pom.xml").exists(): return "Maven"
    elif (repo_dir/"build.gradle").exists() or (repo_dir/"build.gradle.kts").exists(): return "Gradle"
    elif (repo_dir/"build.xml").exists(): return "Ant"
    else: return "Javac"

def run_command(cmd,cwd=None,env=None):
    try:
        result = subprocess.run(cmd,cwd=cwd,env=env,capture_output=True,text=True,timeout=COMPILE_TIMEOUT)
        return result.returncode, (result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        return 1,"Timeout expired"

# ===================== COMPILE =====================
def compile_with_jdks(repo_dir: Path, build_tool: str):
    last_elapsed = 0
    for jdk,path in JDK_PATHS.items():
        env = os.environ.copy()
        env["JAVA_HOME"] = path
        env["PATH"] = os.path.join(path,"bin")+os.pathsep+env["PATH"]

        if build_tool=="Maven": cmd = [MAVEN_PATH,"clean","install","-DskipTests","-U"]
        elif build_tool=="Gradle":
            gradlew = repo_dir/"gradlew"
            if gradlew.exists(): cmd=[str(gradlew),"build","-x","test","--refresh-dependencies"]
            else: cmd=[GRADLE_PATH,"build","-x","test","--refresh-dependencies"]
        elif build_tool=="Ant": cmd=[ANT_PATH]
        else:
            src_roots=[repo_dir/"src"/"main"/"java",repo_dir/"src",repo_dir]
            java_files=[]
            for root in src_roots:
                if root.exists(): java_files.extend(root.rglob("*.java"))
            if not java_files: return False, clean_message("No Java files"),0
            out_dir=repo_dir/"out"; out_dir.mkdir(exist_ok=True)
            cmd=["javac","-encoding","UTF-8","-d",str(out_dir)]+[str(f) for f in java_files]

        start=time.time()
        code,out=run_command(cmd,cwd=repo_dir,env=env)
        last_elapsed=round(time.time()-start,2)
        if code==0: return True, clean_message(f"Compiled successfully ({build_tool},{jdk})"),last_elapsed
    return False, clean_message(f"{build_tool} failed on all JDKs"),max(0.01,last_elapsed)
