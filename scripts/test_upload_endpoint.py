from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import requests


BASE_URL = os.getenv("UPLOAD_TEST_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = int(os.getenv("UPLOAD_TEST_TIMEOUT", "900"))
POLL_SECONDS = float(os.getenv("UPLOAD_TEST_POLL_SECONDS", "1.5"))
CLEANUP = os.getenv("UPLOAD_TEST_CLEANUP", "1").strip().lower() in {"1", "true", "yes", "on"}
ROOT_DIR = Path(__file__).resolve().parents[1]


def wait_job(job_id: str) -> dict:
    started = time.time()
    while time.time() - started <= TIMEOUT:
        response = requests.get(f"{BASE_URL}/ingest/{job_id}", timeout=20)
        response.raise_for_status()
        state = response.json()
        status = str(state.get("status", "")).lower()
        if status in {"completed", "completed_with_errors", "failed"}:
            return state
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"Ingestao {job_id} nao finalizou no tempo esperado.")


def run() -> None:
    bad_files = {"file": ("arquivo.exe", b"nao suportado", "application/octet-stream")}
    bad_response = requests.post(f"{BASE_URL}/upload", files=bad_files, timeout=40)
    assert bad_response.status_code in {400, 415}, bad_response.text

    unique = uuid.uuid4().hex[:8]
    file_name = f"upload-smoke-{unique}.txt"
    content = (
        "Documento de teste de upload LAN.\n"
        "Este arquivo confirma envio via UI e ingestao automatica.\n"
    ).encode("utf-8")
    files = {"file": (file_name, content, "text/plain")}
    response = requests.post(f"{BASE_URL}/upload", files=files, timeout=120)
    response.raise_for_status()
    payload = response.json()
    assert payload.get("ok") is True, payload
    assert payload.get("saved_as"), payload
    assert "docs_dir" in payload, payload
    print("UPLOAD_OK", json.dumps(payload, ensure_ascii=False))

    job_id = payload.get("job_id")
    if job_id:
        final_state = wait_job(job_id)
        print("INGEST_UPLOAD_STATUS", json.dumps(final_state.get("status"), ensure_ascii=False))
        assert str(final_state.get("status", "")).lower() in {"completed", "completed_with_errors"}, final_state

    docs_response = requests.get(f"{BASE_URL}/docs", timeout=30)
    docs_response.raise_for_status()
    docs_payload = docs_response.json()
    names = {doc.get("file_name") for doc in docs_payload.get("docs", []) if isinstance(doc, dict)}
    saved_as = payload.get("saved_as")
    assert saved_as in names, f"Arquivo nao apareceu na lista /docs: {saved_as}"

    if not CLEANUP:
        print("TEST_DONE sem limpeza")
        return

    docs_dir = ROOT_DIR / "docs"
    uploaded_path = docs_dir / str(saved_as)
    if uploaded_path.exists():
        uploaded_path.unlink()
        print(f"CLEANUP_FILE {uploaded_path}")

    ingest_start = requests.post(f"{BASE_URL}/ingest", json={"docs_path": "docs"}, timeout=30)
    ingest_start.raise_for_status()
    cleanup_job = ingest_start.json().get("job_id")
    if cleanup_job:
        final_cleanup = wait_job(cleanup_job)
        assert str(final_cleanup.get("status", "")).lower() in {"completed", "completed_with_errors"}, final_cleanup

    docs_after = requests.get(f"{BASE_URL}/docs", timeout=30)
    docs_after.raise_for_status()
    after_names = {
        doc.get("file_name") for doc in docs_after.json().get("docs", []) if isinstance(doc, dict)
    }
    assert saved_as not in after_names, "Arquivo de teste ainda aparece em /docs apos limpeza."
    print("TEST_DONE com limpeza")


if __name__ == "__main__":
    run()
