from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request


BACKEND = os.getenv("SMOKE_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
QUERY = os.getenv("SMOKE_TEST_QUERY", "Quero os topicos que explicam e falam sobre o perispirito")
TIMEOUT_SECONDS = int(os.getenv("SMOKE_TIMEOUT_SECONDS", "900"))
CHAT_TIMEOUT_SECONDS = int(os.getenv("SMOKE_CHAT_TIMEOUT_SECONDS", "240"))
POLL_INTERVAL = 2
REPEAT_OK_RUNS = int(os.getenv("SMOKE_REPEAT_OK", "3"))


def normalize_text(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    no_accents = "".join(ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn")
    no_punct = re.sub(r"[^\w\s]", " ", no_accents)
    return re.sub(r"\s+", " ", no_punct).strip()


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
    tokens = [tok for tok in normalize_text(query).split() if len(tok) >= 4 and tok not in stopwords]
    return list(dict.fromkeys(tokens))[:3]


def response_is_semantically_relevant(response: dict, query: str) -> bool:
    if response.get("not_found"):
        return False
    claims = response.get("claims", [])
    if not isinstance(claims, list) or not claims:
        return False
    all_references = response.get("all_references", [])
    if not isinstance(all_references, list) or not all_references:
        return False
    all_ref_ids = {
        str(item.get("source_id", "")).strip()
        for item in all_references
        if isinstance(item, dict)
    }
    all_ref_ids.discard("")
    focus = extract_focus_tokens(query)
    if not focus:
        return True
    stems = [tok[:6] if len(tok) >= 6 else tok for tok in focus]
    quote_bag: list[str] = []
    claim_bag: list[str] = []
    citation_source_ids: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_text = normalize_text(str(claim.get("text", "")))
        if claim_text:
            claim_bag.append(claim_text)
        for citation in claim.get("citations", []):
            if not isinstance(citation, dict):
                continue
            source_id = str(citation.get("source_id", "")).strip()
            if source_id:
                citation_source_ids.add(source_id)
            quote = normalize_text(str(citation.get("quote", "")))
            if quote:
                quote_bag.append(quote)

    if not quote_bag:
        return False
    if not citation_source_ids:
        return False
    if any(source_id not in all_ref_ids for source_id in citation_source_ids):
        return False
    quote_hits = sum(1 for quote in quote_bag if any(stem in quote for stem in stems))
    quote_ratio = quote_hits / max(1, len(quote_bag))
    claim_hits = sum(1 for claim_text in claim_bag if any(stem in claim_text for stem in stems))

    # Require majority of citations to talk about the focus term.
    return quote_hits >= 1 and quote_ratio >= 0.6 and (claim_hits >= 1 or len(claim_bag) <= 1)


def post_chat(question: str, timeout: int = 240) -> dict:
    body = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=f"{BACKEND}/chat",
        method="POST",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def main() -> int:
    started = time.time()
    last_error = ""
    ok_runs = 0
    while time.time() - started <= TIMEOUT_SECONDS:
        try:
            result = post_chat(QUERY, timeout=CHAT_TIMEOUT_SECONDS)
            relevant = response_is_semantically_relevant(result, QUERY)
            claims_count = len(result.get("claims", [])) if isinstance(result.get("claims"), list) else 0
            if relevant and claims_count > 0:
                ok_runs += 1
                print("SMOKE_OK_RUN", ok_runs, json.dumps({"claims": claims_count, "sources": len(result.get("sources", []))}))
                if ok_runs >= REPEAT_OK_RUNS:
                    print("SMOKE_OK")
                    return 0
            else:
                ok_runs = 0
                print(
                    "SMOKE_WAIT",
                    json.dumps({"not_found": bool(result.get("not_found")), "claims": claims_count, "terms": result.get("searched_terms", [])}),
                )
        except urllib.error.URLError as exc:
            ok_runs = 0
            last_error = str(exc)
            print("SMOKE_RETRY", last_error)
        except Exception as exc:
            ok_runs = 0
            last_error = str(exc)
            print("SMOKE_RETRY", last_error)
        time.sleep(POLL_INTERVAL)

    print("SMOKE_FAIL", last_error or "Consulta nao retornou citacoes relevantes no tempo esperado.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
