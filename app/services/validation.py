from __future__ import annotations

from typing import Any


class ResponseValidator:
    NOT_FOUND_MESSAGE = "não encontrado nos documentos"

    def minimal_not_found(self) -> dict[str, Any]:
        return {
            "not_found": True,
            "message": self.NOT_FOUND_MESSAGE,
            "synopsis": "",
            "key_points": [],
            "suggested_qa": [],
            "claims": [],
            "sources": [],
        }

    def validate(
        self,
        llm_response: dict[str, Any],
        evidences: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence_map = {ev["source_id"]: ev for ev in evidences}
        if not isinstance(llm_response, dict):
            return self.minimal_not_found()
        if llm_response.get("not_found", False):
            return self.minimal_not_found()

        raw_claims = llm_response.get("claims")
        if not isinstance(raw_claims, list) or not raw_claims:
            return self.minimal_not_found()

        claims: list[dict[str, Any]] = []
        used_sources: dict[str, dict[str, Any]] = {}

        for claim in raw_claims:
            if not isinstance(claim, dict):
                return self.minimal_not_found()
            citations = claim.get("citations")
            if not isinstance(citations, list) or not citations:
                return self.minimal_not_found()
            normalized_citations = []
            for citation in citations:
                if not isinstance(citation, dict):
                    return self.minimal_not_found()
                source_id = citation.get("source_id")
                if not source_id or source_id not in evidence_map:
                    return self.minimal_not_found()
                ev = evidence_map[source_id]
                normalized_citations.append(
                    {
                        "source_id": source_id,
                        "chunk_id": ev["chunk_id"],
                        "doc_id": ev["doc_id"],
                        "file_name": ev["file_name"],
                        "file_path": ev["file_path"],
                        "page_start": ev["page_start"],
                        "page_end": ev["page_end"],
                        "quote": str(citation.get("quote", "")).strip() or ev["text"][:240],
                    }
                )
                used_sources[source_id] = ev

            claims.append(
                {
                    "claim_id": str(claim.get("claim_id") or ""),
                    "text": str(claim.get("text") or "").strip(),
                    "citations": normalized_citations,
                }
            )

        if not claims:
            return self.minimal_not_found()

        suggested_qa: list[dict[str, Any]] = []
        raw_qa = llm_response.get("suggested_qa", [])
        if isinstance(raw_qa, list):
            for item in raw_qa:
                if not isinstance(item, dict):
                    continue
                citations = []
                for citation in item.get("citations", []):
                    if not isinstance(citation, dict):
                        continue
                    source_id = citation.get("source_id")
                    if not source_id or source_id not in evidence_map:
                        continue
                    ev = evidence_map[source_id]
                    citations.append(
                        {
                            "source_id": source_id,
                            "chunk_id": ev["chunk_id"],
                            "doc_id": ev["doc_id"],
                            "file_name": ev["file_name"],
                            "file_path": ev["file_path"],
                            "page_start": ev["page_start"],
                            "page_end": ev["page_end"],
                            "quote": str(citation.get("quote", "")).strip() or ev["text"][:240],
                        }
                    )
                    used_sources[source_id] = ev
                suggested_qa.append(
                    {
                        "question": str(item.get("question", "")).strip(),
                        "answer": str(item.get("answer", "")).strip(),
                        "citations": citations,
                    }
                )

        sources = [
            {
                "source_id": source_id,
                "chunk_id": ev["chunk_id"],
                "doc_id": ev["doc_id"],
                "file_name": ev["file_name"],
                "file_path": ev["file_path"],
                "page_start": ev["page_start"],
                "page_end": ev["page_end"],
            }
            for source_id, ev in used_sources.items()
        ]
        sources.sort(key=lambda s: (s["file_name"], s["page_start"], s["source_id"]))

        return {
            "not_found": False,
            "synopsis": str(llm_response.get("synopsis", "")).strip(),
            "key_points": [str(item).strip() for item in llm_response.get("key_points", []) if str(item).strip()],
            "suggested_qa": suggested_qa,
            "claims": claims,
            "sources": sources,
        }

