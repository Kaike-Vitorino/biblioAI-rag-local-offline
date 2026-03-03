from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND = os.getenv("EVAL_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
ITERATIONS = int(os.getenv("EVAL_ITERATIONS", "3"))
TARGET_PASS_RATE = float(os.getenv("EVAL_TARGET_PASS_RATE", "0.9"))
SLEEP_BETWEEN = float(os.getenv("EVAL_SLEEP_BETWEEN", "0.3"))
OUTPUT_FILE = Path(os.getenv("EVAL_OUTPUT_FILE", "logs/eval_citation_report.json"))


@dataclass
class QuerySpec:
    key: str
    question: str
    expected_stems: list[str]
    min_relevance_rate: float = 0.8
    min_doc_count: int = 1
    min_all_ref_doc_count: int = 1
    min_all_references: int = 1


QUERIES: list[QuerySpec] = [
    QuerySpec("introducao", "Quero citacoes objetivas dos temas introdutorios.", ["introdu", "tema"], 0.75, 1, 1, 3),
    QuerySpec("resumo", "Me mostre referencias com resumo dos pontos principais.", ["resum", "princip"], 0.7, 1, 1, 3),
    QuerySpec("conceitos", "Quais trechos explicam os conceitos centrais?", ["conceit", "centr"], 0.75, 1, 1, 3),
    QuerySpec("metodologia", "Quero referencias sobre metodologia e processo.", ["metodolog", "process"], 0.7, 1, 1, 3),
    QuerySpec("comparacao", "Referencias que comparem abordagens ou cenarios.", ["compar", "abord"], 0.7, 1, 1, 3),
    QuerySpec("conclusao", "Quais referencias tratam da conclusao?", ["conclus"], 0.7, 1, 1, 3),
    QuerySpec("recomendacoes", "Quero citacoes sobre recomendacoes praticas.", ["recomend", "pratic"], 0.7, 1, 1, 3),
    QuerySpec("definicoes", "Me aponte referencias com definicoes importantes.", ["definic", "import"], 0.7, 1, 1, 3),
    QuerySpec("aplicacoes", "Quais trechos tratam de aplicacoes reais?", ["aplic", "real"], 0.7, 1, 1, 3),
    QuerySpec("evidencias", "Citacoes sobre evidencias e justificativas.", ["evidenc", "justific"], 0.7, 1, 1, 3),
]


def normalize_text(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    no_accents = "".join(ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn")
    no_punct = re.sub(r"[^\w\s]", " ", no_accents)
    return re.sub(r"\s+", " ", no_punct).strip()


def get_json(url: str, timeout: int = 60) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def post_json(url: str, payload: dict[str, Any], timeout: int = 260) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def quote_in_page(quote: str, page_text: str) -> bool:
    nq = normalize_text(quote)
    np = normalize_text(page_text)
    if not nq or not np:
        return False
    if nq in np:
        return True
    # fuzzy-ish check by token overlap for OCR/hyphen edge cases
    q_tokens = [tok for tok in nq.split() if len(tok) >= 4]
    if not q_tokens:
        return False
    hits = sum(1 for tok in q_tokens if tok in np)
    return hits / max(1, len(q_tokens)) >= 0.7


def is_relevant_text(text: str, stems: list[str]) -> bool:
    norm = normalize_text(text)
    if not norm:
        return False
    return any(stem in norm for stem in stems)


def evaluate_query(spec: QuerySpec) -> dict[str, Any]:
    response = post_json(f"{BACKEND}/chat", {"question": spec.question})
    claims = response.get("claims", []) if isinstance(response.get("claims"), list) else []
    all_references = response.get("all_references", []) if isinstance(response.get("all_references"), list) else []

    citation_checks: list[dict[str, Any]] = []
    doc_ids: set[str] = set()
    citation_source_ids: set[str] = set()
    relevant_count = 0
    page_match_count = 0

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_text = str(claim.get("text", ""))
        citations = claim.get("citations", [])
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            doc_id = str(citation.get("doc_id", "")).strip()
            page = int(citation.get("page_start", 0) or 0)
            quote = str(citation.get("quote", ""))
            file_name = str(citation.get("file_name", ""))
            source_id = str(citation.get("source_id", "")).strip()

            if doc_id:
                doc_ids.add(doc_id)
            if source_id:
                citation_source_ids.add(source_id)

            page_text = ""
            page_ok = False
            try:
                page_resp = get_json(f"{BACKEND}/docs/{doc_id}/page/{page}/text", timeout=60)
                page_text = str(page_resp.get("text", ""))
                page_ok = quote_in_page(quote, page_text)
            except Exception:
                page_ok = False

            rel = is_relevant_text(quote, spec.expected_stems) or is_relevant_text(claim_text, spec.expected_stems)
            if rel:
                relevant_count += 1
            if page_ok:
                page_match_count += 1

            citation_checks.append(
                {
                    "file_name": file_name,
                    "doc_id": doc_id,
                    "page": page,
                    "relevant": rel,
                    "quote_found_in_page": page_ok,
                    "quote_preview": quote[:180],
                    "claim_preview": claim_text[:180],
                }
            )

    citations_total = len(citation_checks)
    relevance_rate = (relevant_count / citations_total) if citations_total else 0.0
    page_match_rate = (page_match_count / citations_total) if citations_total else 0.0
    distinct_docs = len(doc_ids)

    all_ref_source_ids = {
        str(item.get("source_id", "")).strip()
        for item in all_references
        if isinstance(item, dict)
    }
    all_ref_source_ids.discard("")
    all_ref_doc_ids = {
        str(item.get("doc_id", "")).strip()
        for item in all_references
        if isinstance(item, dict)
    }
    all_ref_doc_ids.discard("")
    citation_ref_coverage = (
        len([sid for sid in citation_source_ids if sid in all_ref_source_ids]) / max(1, len(citation_source_ids))
    )
    all_references_total = len(all_ref_source_ids)
    all_references_docs = len(all_ref_doc_ids)

    passed = (
        not bool(response.get("not_found"))
        and citations_total > 0
        and relevance_rate >= spec.min_relevance_rate
        and page_match_rate >= 0.85
        and distinct_docs >= spec.min_doc_count
        and all_references_total >= spec.min_all_references
        and all_references_docs >= spec.min_all_ref_doc_count
        and citation_ref_coverage >= 1.0
    )

    return {
        "key": spec.key,
        "question": spec.question,
        "passed": passed,
        "not_found": bool(response.get("not_found")),
        "searched_terms": response.get("searched_terms", []),
        "citations_total": citations_total,
        "relevance_rate": round(relevance_rate, 4),
        "page_match_rate": round(page_match_rate, 4),
        "distinct_docs": distinct_docs,
        "all_references_total": all_references_total,
        "all_references_docs": all_references_docs,
        "citation_ref_coverage": round(citation_ref_coverage, 4),
        "min_doc_count": spec.min_doc_count,
        "min_all_ref_doc_count": spec.min_all_ref_doc_count,
        "min_all_references": spec.min_all_references,
        "checks": citation_checks,
    }


def run_iteration(iteration: int) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for spec in QUERIES:
        result = evaluate_query(spec)
        results.append(result)
        print(
            f"ITER {iteration} | {spec.key:18} | pass={result['passed']} | "
            f"rel={result['relevance_rate']:.2f} | page={result['page_match_rate']:.2f} | "
            f"docs={result['distinct_docs']}/{result['all_references_docs']} | "
            f"refs={result['all_references_total']} | cov={result['citation_ref_coverage']:.2f}"
        )
        time.sleep(SLEEP_BETWEEN)

    pass_count = sum(1 for item in results if item["passed"])
    pass_rate = pass_count / max(1, len(results))
    summary = {
        "iteration": iteration,
        "pass_count": pass_count,
        "total": len(results),
        "pass_rate": round(pass_rate, 4),
        "failed_keys": [item["key"] for item in results if not item["passed"]],
        "results": results,
    }
    print(f"ITER {iteration} SUMMARY: {pass_count}/{len(results)} pass ({pass_rate:.2%})")
    return summary


def main() -> int:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    all_iterations: list[dict[str, Any]] = []
    for i in range(1, ITERATIONS + 1):
        summary = run_iteration(i)
        all_iterations.append(summary)
        if summary["pass_rate"] >= TARGET_PASS_RATE:
            report = {
                "target_pass_rate": TARGET_PASS_RATE,
                "iterations": all_iterations,
                "stopped_early": True,
            }
            OUTPUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print("EVAL_OK")
            return 0

    report = {
        "target_pass_rate": TARGET_PASS_RATE,
        "iterations": all_iterations,
        "stopped_early": False,
    }
    OUTPUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    final_rate = all_iterations[-1]["pass_rate"] if all_iterations else 0.0
    if final_rate >= TARGET_PASS_RATE:
        print("EVAL_OK")
        return 0
    print("EVAL_FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
