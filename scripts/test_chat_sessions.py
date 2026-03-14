from __future__ import annotations

import os
import uuid

import requests


BASE_URL = os.getenv("CHAT_TEST_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def run() -> None:
    suffix = uuid.uuid4().hex[:8]
    created = requests.post(f"{BASE_URL}/chats", json={"title": f"Teste {suffix}"}, timeout=30)
    created.raise_for_status()
    chat = created.json()
    chat_id = chat["id"]
    assert chat["title"].startswith("Teste"), chat

    listed = requests.get(f"{BASE_URL}/chats", timeout=30)
    listed.raise_for_status()
    chats = listed.json().get("chats", [])
    assert any(item.get("id") == chat_id for item in chats), "Chat criado nao apareceu em /chats"

    renamed = requests.patch(f"{BASE_URL}/chats/{chat_id}", json={"title": f"Renomeado {suffix}"}, timeout=30)
    renamed.raise_for_status()
    assert "Renomeado" in renamed.json().get("title", "")

    answer = requests.post(
        f"{BASE_URL}/chats/{chat_id}/messages",
        json={"question": "Quero uma citacao objetiva sobre um tema central do documento."},
        timeout=300,
    )
    answer.raise_for_status()
    payload = answer.json()
    assert payload.get("conversation_id") == chat_id, payload

    history = requests.get(f"{BASE_URL}/chats/{chat_id}/messages", timeout=30)
    history.raise_for_status()
    messages = history.json().get("messages", [])
    assert len(messages) >= 2, "Historico deveria conter pelo menos user+assistant."

    deleted = requests.delete(f"{BASE_URL}/chats/{chat_id}", timeout=30)
    deleted.raise_for_status()
    assert deleted.json().get("ok") is True

    missing = requests.get(f"{BASE_URL}/chats/{chat_id}/messages", timeout=30)
    assert missing.status_code == 404, missing.text

    print("CHAT_SESSION_TEST_OK")


if __name__ == "__main__":
    run()
