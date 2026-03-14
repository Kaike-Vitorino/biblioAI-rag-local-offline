from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
DOCS_DIR = ROOT_DIR / "docs"
FRONTEND_DIR = ROOT_DIR / "frontend"
LOG_DIR = ROOT_DIR / "logs"
LOG_FILE = LOG_DIR / "launcher.log"
INGEST_STATE_FILE = ROOT_DIR / ".ingest_state.json"
ENV_FILE = ROOT_DIR / ".env"
ENV_EXAMPLE_FILE = ROOT_DIR / ".env.example"
VENV_DIR = ROOT_DIR / ".venv"
BACKEND_DEPS_MARKER = ROOT_DIR / "data" / ".backend_requirements.hash"
FRONTEND_DEPS_MARKER = ROOT_DIR / "data" / ".frontend_package.hash"

BACKEND_BIND_HOST = "0.0.0.0"
BACKEND_HEALTH_HOST = "127.0.0.1"
BACKEND_PORT = 8000
BACKEND_BASE_URL = f"http://{BACKEND_HEALTH_HOST}:{BACKEND_PORT}"
OLLAMA_URL = "http://127.0.0.1:11434"

REQUIRED_MODELS = ["qwen3:8b", "nomic-embed-text"]
SUPPORTED_DOC_EXTENSIONS = {".pdf", ".txt", ".md"}
NODE_DOWNLOAD_PAGE = "https://nodejs.org/en/download"
OLLAMA_DOWNLOAD_PAGE = "https://ollama.com/download/windows"
OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
DEFAULT_SMOKE_QUERY = "Quais sao os principais topicos abordados nos documentos?"
BACKEND_RUNTIME_MARKER = ROOT_DIR / "data" / ".backend_runtime.json"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def status(message: str) -> None:
    print(message)
    logging.info(message)


def status_ok(message: str) -> None:
    status(f"[OK] {message}")


def status_warn(message: str) -> None:
    status(f"[AVISO] {message}")


def status_error(message: str) -> None:
    status(f"[ERRO] {message}")


def show_messagebox(title: str, message: str, kind: str = "info") -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if kind == "error":
            messagebox.showerror(title, message)
        elif kind == "warning":
            messagebox.showwarning(title, message)
        else:
            messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        # Message box is optional; console/log remains the main channel.
        pass


def run_command(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    def _sanitize_log_line(text: str) -> str:
        ansi_stripped = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
        return ansi_stripped.encode("cp1252", errors="replace").decode(
            "cp1252", errors="replace"
        )

    logging.info("Executando comando: %s (cwd=%s)", cmd, cwd or ROOT_DIR)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if proc.stdout:
        for line in proc.stdout.splitlines():
            logging.info("CMD> %s", _sanitize_log_line(line))
    if check and proc.returncode != 0:
        raise RuntimeError(f"Comando falhou ({proc.returncode}): {' '.join(cmd)}")
    return proc


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_http_json(
    url: str, timeout_seconds: int = 60, expect_key: str | None = None
) -> dict[str, Any] | None:
    started = time.time()
    while time.time() - started <= timeout_seconds:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                payload = response.read().decode("utf-8", errors="replace")
                data = json.loads(payload)
                if expect_key is None or expect_key in data:
                    return data
        except Exception:
            time.sleep(1)
    return None


def http_post_json(
    url: str, payload: dict[str, Any], timeout: int = 30
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def normalize_text(text: str) -> str:
    import unicodedata

    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    no_accents = "".join(
        ch
        for ch in unicodedata.normalize("NFD", lowered)
        if unicodedata.category(ch) != "Mn"
    )
    no_punct = re.sub(r"[^\w\s]", " ", no_accents)
    return re.sub(r"\s+", " ", no_punct).strip()


def ensure_env_file() -> None:
    if ENV_FILE.exists():
        status_ok(".env encontrado")
        return
    if ENV_EXAMPLE_FILE.exists():
        shutil.copyfile(ENV_EXAMPLE_FILE, ENV_FILE)
        status_ok(".env criado a partir de .env.example")
        return
    ENV_FILE.write_text("", encoding="utf-8")
    status_warn(".env.example nao encontrado; .env vazio criado")


def read_env_lines(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env_with_defaults() -> None:
    ensure_env_file()
    current = read_env_lines(ENV_FILE)
    defaults = {
        "MODEL": "qwen3:8b",
        "EMBED_MODEL": "nomic-embed-text",
        "EMBEDDING_MODEL": "nomic-embed-text",
        "BACKEND_HOST": "0.0.0.0",
        "BACKEND_PORT": "8000",
        "DOCS_DIR": "docs",
        "TOPK": "12",
        "FINAL_CHUNKS": "10",
        "CHUNK_SIZE": "900",
        "OVERLAP": "120",
        "TEMPERATURE": "0.1",
        "NUM_CTX": "8192",
        "UPLOAD_MAX_MB": "50",
        "QUERY_PLANNER_ENABLED": "1",
        "QUERY_PLANNER_MODEL": "qwen3:8b",
        "QUERY_PLANNER_TIMEOUT": "20",
        "SMOKE_TEST_ENABLED": "1",
        "SMOKE_TEST_QUERY": DEFAULT_SMOKE_QUERY,
    }
    changed = False
    for key, value in defaults.items():
        if current.get(key) != value:
            current[key] = value
            changed = True
    if not changed:
        status_ok(".env ja esta configurado")
        return
    lines = [f"{k}={v}" for k, v in sorted(current.items())]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    status_ok(".env atualizado para modo one-click")


def resolve_node_paths() -> tuple[str | None, str | None]:
    node_path = shutil.which("node")
    npm_path = shutil.which("npm")
    if node_path and npm_path:
        return node_path, npm_path

    candidate_node = (
        Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
        / "nodejs"
        / "node.exe"
    )
    candidate_npm = candidate_node.with_name("npm.cmd")
    if candidate_node.exists():
        return str(candidate_node), str(
            candidate_npm
        ) if candidate_npm.exists() else npm_path
    return node_path, npm_path


def resolve_ollama_path() -> str | None:
    ollama_path = shutil.which("ollama")
    if ollama_path:
        return ollama_path
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
        / "Ollama"
        / "ollama.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    status(f"Baixando: {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            with destination.open("wb") as out_file:
                shutil.copyfileobj(response, out_file)
    except Exception as exc:
        raise RuntimeError(f"Falha no download: {url}") from exc


def get_latest_node_msi_url() -> tuple[str, str]:
    index_url = "https://nodejs.org/dist/index.json"
    with urllib.request.urlopen(index_url, timeout=30) as response:
        releases = json.loads(response.read().decode("utf-8", errors="replace"))
    for release in releases:
        is_lts = bool(release.get("lts"))
        files = release.get("files", [])
        version = release.get("version", "")
        if is_lts and "win-x64-msi" in files and version:
            filename = f"node-{version}-x64.msi"
            return f"https://nodejs.org/dist/{version}/{filename}", filename
    raise RuntimeError("Nao foi possivel identificar instalador LTS do Node.")


def install_node_assisted() -> None:
    status_warn("Node.js nao encontrado. Vamos abrir o instalador.")
    show_messagebox(
        "Instalacao do Node.js",
        "O Node.js nao foi encontrado.\n\nSera aberto o instalador. Clique em Next > Next > Install.",
        kind="warning",
    )
    with tempfile.TemporaryDirectory(prefix="local_rag_node_") as temp_dir:
        temp_path = Path(temp_dir)
        try:
            url, filename = get_latest_node_msi_url()
            installer = temp_path / filename
            download_file(url, installer)
            subprocess.run(["msiexec", "/i", str(installer)], check=False)
        except Exception as exc:
            status_warn(
                f"Nao foi possivel baixar/rodar automaticamente o Node ({exc})."
            )
            webbrowser.open(NODE_DOWNLOAD_PAGE)
            raise RuntimeError("Instale o Node.js e clique novamente no launcher.")


def install_ollama_assisted() -> None:
    status_warn("Ollama nao encontrado. Vamos abrir o instalador.")
    show_messagebox(
        "Instalacao do Ollama",
        "O Ollama nao foi encontrado.\n\nSera aberto o instalador. Clique em Next > Install.",
        kind="warning",
    )
    with tempfile.TemporaryDirectory(prefix="local_rag_ollama_") as temp_dir:
        temp_path = Path(temp_dir)
        try:
            installer = temp_path / "OllamaSetup.exe"
            download_file(OLLAMA_INSTALLER_URL, installer)
            subprocess.run([str(installer)], check=False)
        except Exception as exc:
            status_warn(
                f"Nao foi possivel baixar/rodar automaticamente o Ollama ({exc})."
            )
            webbrowser.open(OLLAMA_DOWNLOAD_PAGE)
            raise RuntimeError("Instale o Ollama e clique novamente no launcher.")


def restart_launcher() -> None:
    status("Reiniciando o launcher para aplicar alteracoes...")
    os.execv(sys.executable, [sys.executable, *sys.argv])


def ensure_node_installed() -> tuple[str, str]:
    status("Verificando Node.js...")
    node_path, npm_path = resolve_node_paths()
    if node_path and npm_path and Path(node_path).exists():
        status_ok("Node.js encontrado")
        return node_path, npm_path

    install_node_assisted()
    node_path, npm_path = resolve_node_paths()
    if node_path and npm_path:
        status_ok("Node.js instalado")
        restart_launcher()
    raise RuntimeError(
        "Node.js ainda nao esta disponivel. Feche e clique novamente no launcher."
    )


def ensure_ollama_installed() -> str:
    status("Verificando Ollama...")
    ollama_path = resolve_ollama_path()
    if ollama_path and Path(ollama_path).exists():
        status_ok("Ollama encontrado")
        return ollama_path

    install_ollama_assisted()
    ollama_path = resolve_ollama_path()
    if ollama_path:
        status_ok("Ollama instalado")
        restart_launcher()
    raise RuntimeError(
        "Ollama ainda nao esta disponivel. Feche e clique novamente no launcher."
    )


def ensure_ollama_running(ollama_path: str) -> None:
    status("Verificando servico do Ollama...")
    version = wait_for_http_json(
        f"{OLLAMA_URL}/api/version", timeout_seconds=3, expect_key="version"
    )
    if version:
        status_ok("Ollama ja esta rodando")
        return

    status("Iniciando Ollama...")
    creationflags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    with LOG_FILE.open("a", encoding="utf-8") as log_handle:
        subprocess.Popen(
            [ollama_path, "serve"],
            cwd=str(ROOT_DIR),
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creationflags,
        )

    version = wait_for_http_json(
        f"{OLLAMA_URL}/api/version", timeout_seconds=45, expect_key="version"
    )
    if not version:
        raise RuntimeError("Nao foi possivel iniciar o Ollama automaticamente.")
    status_ok("Ollama iniciado")


def list_ollama_models(ollama_path: str) -> set[str]:
    result = run_command([ollama_path, "list"], check=False)
    models: set[str] = set()
    if result.returncode != 0:
        return models
    for line in result.stdout.splitlines():
        raw = line.strip()
        if not raw or raw.lower().startswith("name"):
            continue
        model = raw.split()[0].strip()
        if model:
            models.add(model)
    return models


def model_installed(model: str, available: set[str]) -> bool:
    if model in available:
        return True
    if ":" in model:
        base = model.split(":", 1)[0]
        if f"{base}:latest" in available:
            return True
    else:
        if f"{model}:latest" in available:
            return True
    return False


def ensure_models(ollama_path: str, models: list[str]) -> None:
    status("Verificando modelos do Ollama...")
    available = list_ollama_models(ollama_path)
    for model in models:
        if model_installed(model, available):
            status_ok(f"Modelo {model} ja esta instalado")
            continue
        status(f"Baixando modelo {model} (pode demorar)...")
        try:
            run_command([ollama_path, "pull", model], check=True)
            status_ok(f"Modelo {model} instalado")
        except Exception as exc:
            status_error(f"Falha ao baixar modelo {model}: {exc}")
            show_messagebox(
                "Falha ao baixar modelo",
                f"Nao foi possivel baixar o modelo {model}.\nVerifique internet e tente novamente.",
                kind="error",
            )
            raise


def ensure_venv() -> Path:
    status("Verificando ambiente Python do backend...")
    python_exe = VENV_DIR / "Scripts" / "python.exe"
    if not python_exe.exists():
        status("Criando ambiente virtual...")
        run_command([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    status_ok("Ambiente virtual pronto")
    return python_exe


def install_backend_dependencies(python_exe: Path) -> None:
    requirements_file = ROOT_DIR / "requirements.txt"
    if not requirements_file.exists():
        raise RuntimeError("Arquivo requirements.txt nao encontrado.")
    current_hash = sha256_file(requirements_file)
    BACKEND_DEPS_MARKER.parent.mkdir(parents=True, exist_ok=True)
    previous_hash = (
        BACKEND_DEPS_MARKER.read_text(encoding="utf-8").strip()
        if BACKEND_DEPS_MARKER.exists()
        else ""
    )
    if previous_hash == current_hash:
        probe = run_command(
            [str(python_exe), "-c", "import fastapi, uvicorn"],
            check=False,
        )
        if probe.returncode == 0:
            status_ok("Dependencias do backend ja estao atualizadas")
            return

    status("Instalando dependencias do backend...")
    run_command(
        [str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], check=True
    )
    try:
        run_command(
            [str(python_exe), "-m", "pip", "install", "-r", "requirements.txt"],
            check=True,
        )
    except Exception as exc:
        raise RuntimeError(
            "Nao foi possivel instalar as dependencias do backend.\n"
            "Isso geralmente acontece por compatibilidade de versao do Python.\n"
            "Feche o launcher e tente novamente. Se persistir, use Python 3.12, 3.13 ou 3.14."
        ) from exc
    BACKEND_DEPS_MARKER.write_text(current_hash, encoding="utf-8")
    status_ok("Dependencias do backend instaladas")


def build_is_stale() -> bool:
    dist_index = FRONTEND_DIR / "dist" / "index.html"
    if not dist_index.exists():
        return True
    dist_mtime = dist_index.stat().st_mtime
    watched = [
        FRONTEND_DIR / "src",
        FRONTEND_DIR / "package.json",
        FRONTEND_DIR / "vite.config.ts",
        FRONTEND_DIR / "index.html",
    ]
    for path in watched:
        if not path.exists():
            continue
        if path.is_file() and path.stat().st_mtime > dist_mtime:
            return True
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.stat().st_mtime > dist_mtime:
                    return True
    return False


def ensure_frontend_built(npm_path: str, env: dict[str, str]) -> None:
    status("Verificando frontend...")
    package_lock = FRONTEND_DIR / "package-lock.json"
    package_json = FRONTEND_DIR / "package.json"
    fingerprint_file = package_lock if package_lock.exists() else package_json
    if not fingerprint_file.exists():
        raise RuntimeError("package.json do frontend nao encontrado.")
    current_hash = sha256_file(fingerprint_file)
    FRONTEND_DEPS_MARKER.parent.mkdir(parents=True, exist_ok=True)
    previous_hash = (
        FRONTEND_DEPS_MARKER.read_text(encoding="utf-8").strip()
        if FRONTEND_DEPS_MARKER.exists()
        else ""
    )

    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.exists() or previous_hash != current_hash:
        status("Instalando dependencias do frontend (npm install)...")
        run_command([npm_path, "install"], cwd=FRONTEND_DIR, env=env, check=True)
        FRONTEND_DEPS_MARKER.write_text(current_hash, encoding="utf-8")
        status_ok("Dependencias do frontend instaladas")
    if build_is_stale():
        status("Gerando build de producao do frontend...")
        run_command([npm_path, "run", "build"], cwd=FRONTEND_DIR, env=env, check=True)
        status_ok("Frontend buildado")
    else:
        status_ok("Build do frontend ja esta atualizado")


def compute_backend_build_hash() -> str:
    import hashlib

    digest = hashlib.sha256()
    candidates: list[Path] = []
    app_dir = ROOT_DIR / "app"
    if app_dir.exists():
        candidates.extend(sorted(app_dir.rglob("*.py")))
    for extra in [ROOT_DIR / "requirements.txt", ENV_FILE, ROOT_DIR / ".env.example"]:
        if extra.exists():
            candidates.append(extra)
    for path in candidates:
        digest.update(str(path.relative_to(ROOT_DIR)).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def get_backend_listener_pid() -> int | None:
    try:
        result = run_command(["netstat", "-ano", "-p", "tcp"], check=False)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    pattern = re.compile(
        rf"^\s*TCP\s+\S+:{BACKEND_PORT}\s+\S+\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE
    )
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def stop_process(pid: int) -> None:
    if pid <= 0:
        return
    status(f"Encerrando processo antigo do backend (PID {pid})...")
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_backend_down(timeout_seconds: int = 20) -> None:
    started = time.time()
    while time.time() - started <= timeout_seconds:
        health = wait_for_http_json(
            f"{BACKEND_BASE_URL}/health", timeout_seconds=1, expect_key="status"
        )
        if not health:
            return
        time.sleep(1)


def ensure_backend_running(python_exe: Path, env: dict[str, str]) -> None:
    status("Verificando backend...")
    desired_build_hash = compute_backend_build_hash()
    health = wait_for_http_json(
        f"{BACKEND_BASE_URL}/health", timeout_seconds=2, expect_key="status"
    )
    if health and health.get("status") == "ok":
        running_hash = str(health.get("build_hash", "") or "")
        running_pid = int(health.get("pid", 0) or 0)
        if running_hash == desired_build_hash:
            status_ok("Backend ja esta rodando e atualizado")
            return
        status_warn(
            "Backend em execucao desatualizado. Reiniciando para aplicar correcoes..."
        )
        if running_pid > 0:
            stop_process(running_pid)
        else:
            pid = get_backend_listener_pid()
            if pid:
                stop_process(pid)
        wait_backend_down()

    status("Iniciando backend...")
    creationflags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP

    backend_env = dict(env)
    backend_env["APP_BUILD_HASH"] = desired_build_hash
    with LOG_FILE.open("a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            [
                str(python_exe),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                BACKEND_BIND_HOST,
                "--port",
                str(BACKEND_PORT),
            ],
            cwd=str(ROOT_DIR),
            env=backend_env,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creationflags,
        )

    health = wait_for_http_json(
        f"{BACKEND_BASE_URL}/health", timeout_seconds=60, expect_key="status"
    )
    if not health or health.get("status") != "ok":
        raise RuntimeError("Backend nao ficou saudavel no tempo esperado.")
    running_hash = str(health.get("build_hash", "") or "")
    if running_hash != desired_build_hash:
        raise RuntimeError("Backend iniciou, mas sem a versao esperada do codigo.")
    BACKEND_RUNTIME_MARKER.parent.mkdir(parents=True, exist_ok=True)
    BACKEND_RUNTIME_MARKER.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "build_hash": desired_build_hash,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    status_ok("Backend iniciado")


def load_ingest_state() -> dict[str, Any]:
    if not INGEST_STATE_FILE.exists():
        return {"files": {}}
    try:
        return json.loads(INGEST_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"files": {}}


def collect_docs_state() -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    if not DOCS_DIR.exists():
        return files
    for doc_file in sorted(DOCS_DIR.rglob("*")):
        if not doc_file.is_file():
            continue
        if doc_file.suffix.lower() not in SUPPORTED_DOC_EXTENSIONS:
            continue
        stat = doc_file.stat()
        rel = str(doc_file.relative_to(ROOT_DIR)).replace("\\", "/")
        files[rel] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "sha256": sha256_file(doc_file),
        }
    return files


def should_run_ingest() -> bool:
    current = collect_docs_state()
    if not current:
        status_warn(
            "Nenhum arquivo suportado em /docs (.pdf, .txt, .md). A UI sera aberta mesmo assim."
        )
        return False
    old = load_ingest_state().get("files", {})
    if old != current:
        return True
    status_ok("Nenhum arquivo novo detectado em /docs")
    return False


def save_ingest_state(job_id: str | None = None) -> None:
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_job_id": job_id,
        "files": collect_docs_state(),
    }
    INGEST_STATE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_auto_ingest_if_needed() -> None:
    if not should_run_ingest():
        return
    status(
        "Arquivos novos/alterados detectados em /docs. Iniciando ingestao automatica..."
    )
    payload = {"docs_path": "docs"}
    ingest_start = http_post_json(f"{BACKEND_BASE_URL}/ingest", payload, timeout=20)
    job_id = ingest_start.get("job_id")
    if not job_id:
        raise RuntimeError("Backend nao retornou job_id para ingestao.")
    status(f"Ingestao iniciada (job_id={job_id})")

    started = time.time()
    last_line = ""
    while time.time() - started <= 60 * 60:
        query = urllib.parse.urlencode({"job_id": job_id})
        with urllib.request.urlopen(
            f"{BACKEND_BASE_URL}/ingest/status?{query}", timeout=20
        ) as response:
            state = json.loads(response.read().decode("utf-8", errors="replace"))
        progress = state.get("progress", {})
        status_line = (
            f"Status ingestao: {state.get('status')} | "
            f"arquivos {progress.get('files_done', 0)}/{progress.get('files_total', 0)} | "
            f"chunks {progress.get('chunks_done', 0)} | "
            f"skipped {progress.get('skipped', 0)} | "
            f"updated {progress.get('updated', 0)}"
        )
        if status_line != last_line:
            status(status_line)
            last_line = status_line

        status_value = str(state.get("status", "")).lower()
        if status_value in {"completed", "completed_with_errors"}:
            save_ingest_state(job_id)
            if (
                int(progress.get("chunks_done", 0) or 0) == 0
                and int(progress.get("updated", 0) or 0) == 0
            ):
                status_ok(
                    "Ingestao finalizada sem novos chunks (arquivos ja indexados)"
                )
            else:
                status_ok("Ingestao finalizada")
            return
        if status_value in {"failed"}:
            errors = [str(item) for item in state.get("errors", [])]
            if any(
                "another ingestion job is already running" in item.lower()
                for item in errors
            ):
                status_warn(
                    "Ja existe uma ingestao em andamento. Aguardando terminar..."
                )
                _wait_existing_ingest_completion()
                save_ingest_state(job_id)
                status_ok("Ingestao existente finalizada e estado atualizado")
                return
            raise RuntimeError(f"Ingestao falhou: {errors}")
        time.sleep(2)

    raise RuntimeError("Ingestao nao terminou no tempo esperado.")


def _wait_existing_ingest_completion(timeout_seconds: int = 60 * 60) -> None:
    started = time.time()
    last_line = ""
    while time.time() - started <= timeout_seconds:
        with urllib.request.urlopen(
            f"{BACKEND_BASE_URL}/ingest/status", timeout=20
        ) as response:
            state = json.loads(response.read().decode("utf-8", errors="replace"))
        progress = state.get("progress", {})
        status_line = (
            f"Ingestao existente: {state.get('status')} | "
            f"arquivos {progress.get('files_done', 0)}/{progress.get('files_total', 0)} | "
            f"chunks {progress.get('chunks_done', 0)}"
        )
        if status_line != last_line:
            status(status_line)
            last_line = status_line
        status_value = str(state.get("status", "")).lower()
        if status_value in {"completed", "completed_with_errors"}:
            status_ok("Ingestao finalizada")
            return
        if status_value in {"failed"}:
            raise RuntimeError(f"Ingestao falhou: {state.get('errors', [])}")
        time.sleep(2)
    raise RuntimeError("Ingestao existente nao terminou no tempo esperado.")


def run_ingest_force() -> None:
    status("Executando ingestao completa de manutencao...")
    payload = {"docs_path": "docs"}
    ingest_start = http_post_json(f"{BACKEND_BASE_URL}/ingest", payload, timeout=20)
    job_id = ingest_start.get("job_id")
    if not job_id:
        raise RuntimeError("Backend nao retornou job_id para ingestao.")
    started = time.time()
    while time.time() - started <= 60 * 60:
        query = urllib.parse.urlencode({"job_id": job_id})
        with urllib.request.urlopen(
            f"{BACKEND_BASE_URL}/ingest/status?{query}", timeout=20
        ) as response:
            state = json.loads(response.read().decode("utf-8", errors="replace"))
        status_value = str(state.get("status", "")).lower()
        if status_value in {"completed", "completed_with_errors"}:
            save_ingest_state(job_id)
            return
        if status_value in {"failed"}:
            raise RuntimeError(f"Ingestao falhou: {state.get('errors', [])}")
        time.sleep(2)
    raise RuntimeError("Ingestao forÃ§ada nao terminou no tempo esperado.")


def run_chat_smoke_test(query: str, timeout: int = 240) -> dict[str, Any]:
    body = json.dumps({"question": query}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=f"{BACKEND_BASE_URL}/chat",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def extract_focus_tokens(query: str) -> list[str]:
    stopwords = {
        "quero",
        "topicos",
        "topico",
        "explicam",
        "explicar",
        "falam",
        "falar",
        "sobre",
        "os",
        "as",
        "o",
        "a",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "que",
        "e",
    }
    normalized = normalize_text(query)
    tokens = [
        tok for tok in normalized.split() if len(tok) >= 4 and tok not in stopwords
    ]
    unique: list[str] = []
    seen = set()
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            unique.append(tok)
    return unique[:3]


def response_is_semantically_relevant(response: dict[str, Any], query: str) -> bool:
    if response.get("not_found"):
        return False
    claims = response.get("claims", [])
    if not isinstance(claims, list) or not claims:
        return False
    focus_tokens = extract_focus_tokens(query)
    if not focus_tokens:
        return True
    stems = [tok[:6] if len(tok) >= 6 else tok for tok in focus_tokens]

    citation_texts: list[str] = []
    claim_texts: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        citation_list = claim.get("citations", [])
        if not isinstance(citation_list, list) or not citation_list:
            continue
        claim_text = normalize_text(str(claim.get("text", "")))
        if claim_text:
            claim_texts.append(claim_text)
        for citation in citation_list:
            if not isinstance(citation, dict):
                continue
            quote = normalize_text(str(citation.get("quote", "")))
            if quote:
                citation_texts.append(quote)

    if not citation_texts:
        return False
    quote_hits = sum(
        1 for quote in citation_texts if any(stem in quote for stem in stems)
    )
    quote_ratio = quote_hits / max(1, len(citation_texts))
    claim_hits = sum(
        1 for claim_text in claim_texts if any(stem in claim_text for stem in stems)
    )
    return (
        quote_hits >= 1
        and quote_ratio >= 0.6
        and (claim_hits >= 1 or len(claim_texts) <= 1)
    )


def ensure_smoke_query_returns_result() -> None:
    env_values = read_env_lines(ENV_FILE)
    enabled = env_values.get("SMOKE_TEST_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        status_ok("Smoke test desativado por configuracao")
        return
    query = (
        env_values.get("SMOKE_TEST_QUERY", DEFAULT_SMOKE_QUERY).strip()
        or DEFAULT_SMOKE_QUERY
    )
    status(f"Executando smoke test de chat: {query}")
    try:
        response = run_chat_smoke_test(query)
        if response_is_semantically_relevant(response, query):
            status_ok(
                "Smoke test passou: chat retornou resposta relevante com citacoes"
            )
            return
        status_warn(
            "Smoke test retornou resposta fraca ou pouco relevante. Tentando correcao com ingestao forçada..."
        )
    except Exception as exc:
        status_warn(
            f"Smoke test inicial falhou ({exc}). Tentando correcao com ingestao forçada..."
        )

    run_ingest_force()
    response = run_chat_smoke_test(query)
    if not response_is_semantically_relevant(response, query):
        raise RuntimeError(
            "Smoke test falhou: a consulta principal ainda nao retornou citacoes semanticamente conectadas."
        )
    status_ok("Smoke test passou apos ingestao de manutencao")


def build_runtime_env(
    node_path: str, npm_path: str, ollama_path: str
) -> dict[str, str]:
    env = os.environ.copy()
    # Ensure common install folders are visible even if shell PATH is stale.
    extra_dirs = {
        str(Path(node_path).resolve().parent),
        str(Path(npm_path).resolve().parent),
        str(Path(ollama_path).resolve().parent),
    }
    current_path = env.get("PATH", "")
    for entry in extra_dirs:
        if entry and entry not in current_path:
            current_path = f"{entry};{current_path}"
    env["PATH"] = current_path
    env["PYTHONUTF8"] = "1"
    return env


def _detect_lan_ip() -> str | None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        return None
    return None


def open_ui() -> None:
    local_url = f"{BACKEND_BASE_URL}/"
    lan_ip = _detect_lan_ip()
    lan_url = f"http://{lan_ip}:{BACKEND_PORT}/" if lan_ip else None
    status(f"Link local: {local_url}")
    if lan_url:
        status(f"Link LAN (mesmo Wi-Fi): {lan_url}")
    status(f"Abrindo a interface em {local_url}")
    webbrowser.open(local_url)


def run() -> None:
    setup_logging()
    status("=== Local RAG - Launcher One-Click ===")

    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11+ e necessario para executar este projeto.")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    write_env_with_defaults()

    node_path, npm_path = ensure_node_installed()
    ollama_path = ensure_ollama_installed()
    runtime_env = build_runtime_env(node_path, npm_path, ollama_path)

    ensure_ollama_running(ollama_path)
    ensure_models(ollama_path, REQUIRED_MODELS)

    python_exe = ensure_venv()
    install_backend_dependencies(python_exe)
    ensure_frontend_built(npm_path, runtime_env)
    ensure_backend_running(python_exe, runtime_env)
    run_auto_ingest_if_needed()
    # Smoke test desativado no fluxo one-click para nao poluir a lista de chats do usuario.
    # Se necessario, rode ensure_smoke_query_returns_result() manualmente em manutencao.
    # ensure_smoke_query_returns_result()
    open_ui()

    status_ok("Tudo pronto. Voce pode usar a aplicacao no navegador.")
    show_messagebox(
        "Aplicacao pronta",
        "Tudo certo! A aplicacao foi iniciada e o navegador sera aberto automaticamente.",
        kind="info",
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        status_error(str(exc))
        logging.error("Falha no launcher: %s", traceback.format_exc())
        show_messagebox(
            "Falha na inicializacao",
            "Nao foi possivel iniciar automaticamente.\n\n"
            f"Erro: {exc}\n\n"
            f"Veja o arquivo de log em:\n{LOG_FILE}",
            kind="error",
        )
        # Keep window open when launched with double click.
        try:
            input("Pressione Enter para fechar...")
        except Exception:
            pass
        sys.exit(1)
