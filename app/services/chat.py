from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from app.db.database import Database
from app.services.llm import LLMService
from app.services.retrieval import RetrievalService
from app.services.text_utils import normalize_text
from app.services.validation import ResponseValidator

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        db: Database,
        retrieval_service: RetrievalService,
        llm_service: LLMService,
        validator: ResponseValidator,
    ):
        self.db = db
        self.retrieval_service = retrieval_service
        self.llm_service = llm_service
        self.validator = validator

    def answer(self, question: str, conversation_id: str | None = None) -> dict[str, Any]:
        conv_id = conversation_id or f"conv_{uuid.uuid4().hex}"
        self._ensure_conversation(conv_id, initial_question=question)
        self._add_message(conv_id, "user", {"question": question})

        retrieval = self.retrieval_service.retrieve(question)
        all_references = retrieval.get("all_references", [])
        focus_terms = retrieval.get("focus_terms", [])
        coverage_request = bool(retrieval.get("coverage_request"))
        evidences = retrieval["evidences"]
        focus_evidences = [ev for ev in evidences if ev.get("focus_match")]
        if focus_evidences:
            if coverage_request:
                evidences = self._diversify_by_doc(focus_evidences, min(10, max(6, len(focus_evidences))))
            else:
                evidences = focus_evidences[: min(8, max(4, len(focus_evidences)))]

        if not evidences:
            not_found = self.validator.minimal_not_found()
            response_payload = {
                "conversation_id": conv_id,
                "question": question,
                "searched_terms": retrieval["searched_terms"],
                "all_references": all_references,
                **not_found,
            }
            self._add_message(conv_id, "assistant", response_payload)
            return response_payload

        validated: dict[str, Any]
        llm_error: str | None = None
        try:
            llm_response = self.llm_service.generate_answer(
                question,
                evidences,
                focus_terms=focus_terms,
                coverage_request=coverage_request,
            )
            validated = self.validator.validate(llm_response, evidences)
            if validated.get("not_found") and self._should_retry_not_found(retrieval):
                llm_retry = self.llm_service.generate_answer(
                    question,
                    evidences,
                    focus_terms=focus_terms,
                    coverage_request=coverage_request,
                    retry_mode=True,
                )
                validated_retry = self.validator.validate(llm_retry, evidences)
                if not validated_retry.get("not_found"):
                    validated = validated_retry
        except Exception as exc:
            llm_error = str(exc)
            logger.warning("LLM generation failed, switching to extractive fallback: %s", llm_error)
            validated = self.validator.minimal_not_found()

        validated = self._enforce_focus_alignment(
            validated=validated,
            evidences=evidences,
            focus_terms=focus_terms,
            question=question,
            llm_error=llm_error,
        )

        if validated.get("not_found") and evidences:
            extractive = self._build_extractive_fallback(
                question,
                evidences,
                focus_terms=focus_terms,
                coverage_request=coverage_request,
                llm_error=llm_error,
            )
            if not extractive.get("not_found"):
                validated = extractive

        if coverage_request and not validated.get("not_found"):
            validated = self._enforce_coverage_distribution(
                validated=validated,
                evidences=evidences,
                focus_terms=focus_terms,
                question=question,
            )
        merged_all_references = self._merge_all_references(
            retrieval_references=all_references,
            used_sources=validated.get("sources", []),
        )

        response_payload = {
            "conversation_id": conv_id,
            "question": question,
            "searched_terms": retrieval["searched_terms"],
            "all_references": merged_all_references,
            "not_found": validated["not_found"],
            "message": validated.get("message"),
            "synopsis": validated["synopsis"],
            "key_points": validated["key_points"],
            "suggested_qa": validated["suggested_qa"],
            "claims": validated["claims"],
            "sources": validated["sources"],
        }
        self._add_message(conv_id, "assistant", response_payload)
        self._log_retrieval(conv_id, question, retrieval)
        return response_payload

    @staticmethod
    def _merge_all_references(
        retrieval_references: list[dict[str, Any]],
        used_sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        ordered_ids: list[str] = []

        def _append(item: dict[str, Any]) -> None:
            source_id = str(item.get("source_id", "")).strip()
            if not source_id:
                return
            normalized = {
                "source_id": source_id,
                "chunk_id": str(item.get("chunk_id", source_id)),
                "doc_id": str(item.get("doc_id", "")),
                "file_name": str(item.get("file_name", "")),
                "file_path": str(item.get("file_path", "")),
                "page_start": int(item.get("page_start", 0) or 0),
                "page_end": int(item.get("page_end", 0) or 0),
            }
            if "score" in item:
                try:
                    normalized["score"] = float(item.get("score", 0.0))
                except Exception:
                    normalized["score"] = 0.0
            if "focus_match" in item:
                normalized["focus_match"] = bool(item.get("focus_match"))
            if source_id not in merged:
                ordered_ids.append(source_id)
            merged[source_id] = normalized

        for item in retrieval_references:
            if isinstance(item, dict):
                _append(item)
        for item in used_sources:
            if isinstance(item, dict):
                _append(item)

        return [merged[source_id] for source_id in ordered_ids if source_id in merged]

    def _ensure_conversation(self, conversation_id: str, initial_question: str | None = None) -> None:
        row = self.db.fetchone("SELECT id, title FROM conversations WHERE id = ?", [conversation_id])
        if row is None:
            title = self._title_from_question(initial_question)
            self.db.execute(
                """
                INSERT INTO conversations(id, title, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                [conversation_id, title],
            )
            return
        if initial_question:
            existing_title = str(row["title"] or "").strip()
            if not existing_title or existing_title.lower() in {"novo chat", "new chat", "chat"}:
                self.db.execute(
                    """
                    UPDATE conversations
                    SET title = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    [self._title_from_question(initial_question), conversation_id],
                )

    def _add_message(self, conversation_id: str, role: str, content: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO messages(id, conversation_id, role, content_json)
            VALUES (?, ?, ?, ?)
            """,
            [f"msg_{uuid.uuid4().hex}", conversation_id, role, json.dumps(content, ensure_ascii=False)],
        )
        self.db.execute(
            """
            UPDATE conversations
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            [conversation_id],
        )

    @staticmethod
    def _title_from_question(question: str | None) -> str:
        if not question:
            return "Novo chat"
        compact = re.sub(r"\s+", " ", question).strip()
        if not compact:
            return "Novo chat"
        max_len = 60
        if len(compact) <= max_len:
            return compact
        return compact[:max_len].rstrip() + "..."

    def _log_retrieval(self, conversation_id: str, question: str, retrieval: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO retrieval_logs(id, conversation_id, question, searched_terms_json, selected_evidence_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                f"ret_{uuid.uuid4().hex}",
                conversation_id,
                question,
                json.dumps(retrieval.get("searched_terms", []), ensure_ascii=False),
                json.dumps(retrieval.get("evidences", []), ensure_ascii=False),
            ],
        )

    def _should_retry_not_found(self, retrieval: dict[str, Any]) -> bool:
        evidences = retrieval.get("evidences", [])
        terms = retrieval.get("searched_terms", [])
        if not evidences or not terms:
            return False
        unigram_terms = [term for term in terms if " " not in term and len(term) >= 5]
        if not unigram_terms:
            return False
        normalized_texts = [normalize_text(ev.get("text", "")) for ev in evidences]
        matches = 0
        for term in unigram_terms:
            if any(term in text for text in normalized_texts):
                matches += 1
        return matches >= 1

    def _build_extractive_fallback(
        self,
        question: str,
        evidences: list[dict[str, Any]],
        focus_terms: list[str],
        coverage_request: bool,
        llm_error: str | None = None,
    ) -> dict[str, Any]:
        if not evidences:
            return self.validator.minimal_not_found()

        focus_stems = self._focus_stems(focus_terms, question)
        focus_first = [ev for ev in evidences if self._text_has_focus(str(ev.get("text", "")), focus_stems)]
        selected_pool = focus_first + [ev for ev in evidences if ev not in focus_first]
        if coverage_request:
            selected = self._diversify_by_doc(selected_pool, min(8, len(selected_pool)))
        else:
            selected = selected_pool[: min(5, len(selected_pool))]

        claims: list[dict[str, Any]] = []
        key_points: list[str] = []
        sources: list[dict[str, Any]] = []
        seen_quotes: set[str] = set()

        for idx, ev in enumerate(selected, start=1):
            text = str(ev.get("text", "")).strip()
            if not text:
                continue
            quote = self._best_quote_for_evidence(text, focus_stems)
            if not quote:
                continue
            quote_key = normalize_text(quote)
            if quote_key in seen_quotes:
                continue
            seen_quotes.add(quote_key)

            claim_text = self._claim_from_quote(quote, focus_stems)
            citation = {
                "source_id": ev["source_id"],
                "chunk_id": ev["chunk_id"],
                "doc_id": ev["doc_id"],
                "file_name": ev["file_name"],
                "file_path": ev["file_path"],
                "page_start": ev["page_start"],
                "page_end": ev["page_end"],
                "quote": quote,
            }
            claims.append(
                {
                    "claim_id": f"fallback_{idx}",
                    "text": claim_text,
                    "citations": [citation],
                }
            )
            if claim_text and claim_text not in key_points:
                key_points.append(claim_text)
            sources.append(
                {
                    "source_id": ev["source_id"],
                    "chunk_id": ev["chunk_id"],
                    "doc_id": ev["doc_id"],
                    "file_name": ev["file_name"],
                    "file_path": ev["file_path"],
                    "page_start": ev["page_start"],
                    "page_end": ev["page_end"],
                }
            )

        if not claims:
            return self.validator.minimal_not_found()

        topic = focus_terms[0] if focus_terms else (normalize_text(question).split()[0] if normalize_text(question) else "tema")
        synopsis = f"Sintese extraida diretamente das fontes recuperadas sobre '{topic}'."
        if llm_error:
            synopsis += " O modelo de geracao nao respondeu a tempo; resposta gerada no modo extrativo."

        qa = [
            {
                "question": question,
                "answer": "Resposta extraida diretamente das fontes listadas.",
                "citations": [claims[0]["citations"][0]],
            }
        ]

        return {
            "not_found": False,
            "synopsis": synopsis,
            "key_points": key_points[:6],
            "suggested_qa": qa,
            "claims": claims,
            "sources": sources,
        }

    def _enforce_coverage_distribution(
        self,
        validated: dict[str, Any],
        evidences: list[dict[str, Any]],
        focus_terms: list[str],
        question: str,
    ) -> dict[str, Any]:
        if validated.get("not_found"):
            return validated
        if not evidences:
            return validated

        by_doc: dict[str, list[dict[str, Any]]] = {}
        for ev in evidences:
            by_doc.setdefault(str(ev["doc_id"]), []).append(ev)
        if len(by_doc) <= 1:
            return validated

        evidence_by_source = {str(ev["source_id"]): ev for ev in evidences}
        self._select_single_citation_per_claim_diverse(validated.get("claims", []))
        used_source_ids: set[str] = set()
        for claim in validated.get("claims", []):
            for citation in claim.get("citations", []):
                source_id = str(citation.get("source_id", "")).strip()
                if source_id:
                    used_source_ids.add(source_id)

        used_docs = {str(evidence_by_source[sid]["doc_id"]) for sid in used_source_ids if sid in evidence_by_source}
        focus_stems = self._focus_stems(focus_terms, question)

        target_doc_count = min(len(by_doc), 5)
        missing_docs = [doc_id for doc_id in by_doc.keys() if doc_id not in used_docs]
        if len(used_docs) >= target_doc_count:
            return validated

        next_index = len(validated.get("claims", [])) + 1
        for doc_id in missing_docs:
            candidates = by_doc.get(doc_id, [])
            if not candidates:
                continue
            ev = candidates[0]
            quote = self._best_quote_for_evidence(str(ev.get("text", "")), focus_stems)
            if not quote:
                continue
            claim_text = self._claim_from_quote(quote, focus_stems)
            citation = {
                "source_id": ev["source_id"],
                "chunk_id": ev["chunk_id"],
                "doc_id": ev["doc_id"],
                "file_name": ev["file_name"],
                "file_path": ev["file_path"],
                "page_start": ev["page_start"],
                "page_end": ev["page_end"],
                "quote": quote,
            }
            validated.setdefault("claims", []).append(
                {
                    "claim_id": f"coverage_{next_index}",
                    "text": claim_text,
                    "citations": [citation],
                }
            )
            validated.setdefault("sources", []).append(
                {
                    "source_id": ev["source_id"],
                    "chunk_id": ev["chunk_id"],
                    "doc_id": ev["doc_id"],
                    "file_name": ev["file_name"],
                    "file_path": ev["file_path"],
                    "page_start": ev["page_start"],
                    "page_end": ev["page_end"],
                }
            )
            next_index += 1
            used_docs.add(doc_id)
            if len(used_docs) >= target_doc_count:
                break

        # Dedupe sources after coverage extension.
        balanced_claims = self._balance_claims_by_doc(validated.get("claims", []), max_per_doc=2, limit_total=8)
        if balanced_claims:
            validated["claims"] = balanced_claims

        # Keep sources referenced by final claims only.
        final_source_ids: set[str] = set()
        for claim in validated.get("claims", []):
            for citation in claim.get("citations", []):
                source_id = str(citation.get("source_id", "")).strip()
                if source_id:
                    final_source_ids.add(source_id)

        unique_sources: dict[str, dict[str, Any]] = {}
        for source in validated.get("sources", []):
            source_id = str(source.get("source_id", "")).strip()
            if not source_id or source_id not in final_source_ids:
                continue
            unique_sources[source_id] = source
        validated["sources"] = sorted(
            unique_sources.values(),
            key=lambda s: (str(s.get("file_name", "")), int(s.get("page_start", 0)), str(s.get("source_id", ""))),
        )

        # Rebuild suggested_qa if it is concentrated in a single document.
        qa_docs: set[str] = set()
        for item in validated.get("suggested_qa", []):
            for citation in item.get("citations", []):
                doc_id = str(citation.get("doc_id", "")).strip()
                if doc_id:
                    qa_docs.add(doc_id)
        if len(qa_docs) <= 1:
            validated["suggested_qa"] = self._build_diverse_qa_from_claims(
                claims=validated.get("claims", []),
                focus_terms=focus_terms,
                limit=3,
            )
        return validated

    def _enforce_focus_alignment(
        self,
        validated: dict[str, Any],
        evidences: list[dict[str, Any]],
        focus_terms: list[str],
        question: str,
        llm_error: str | None,
    ) -> dict[str, Any]:
        if validated.get("not_found"):
            return validated
        if not focus_terms:
            return validated

        stems = self._focus_stems(focus_terms, question)
        if not stems:
            return validated
        primary_stem = stems[0] if stems else ""

        evidence_map = {str(ev.get("source_id")): ev for ev in evidences if ev.get("source_id")}
        kept_claims: list[dict[str, Any]] = []
        used_source_ids: set[str] = set()

        for claim in validated.get("claims", []):
            claim_text = normalize_text(str(claim.get("text", "")))
            claim_has_focus = self._text_has_focus(claim_text, stems, require_primary=bool(primary_stem))
            citations = claim.get("citations", [])
            if not isinstance(citations, list):
                continue

            citation_has_focus = False
            normalized_citations: list[dict[str, Any]] = []
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                source_id = str(citation.get("source_id", "")).strip()
                ev = evidence_map.get(source_id)
                if not source_id or ev is None:
                    continue

                quote_raw = str(citation.get("quote", "")).strip()
                ev_text = str(ev.get("text", ""))
                quote_norm = normalize_text(quote_raw)
                ev_norm = normalize_text(ev_text)
                quote_is_literal = bool(quote_norm and quote_norm in ev_norm)
                if (
                    not quote_raw
                    or len(quote_norm) < 28
                    or quote_raw.endswith("-")
                    or not quote_is_literal
                    or not self._text_has_focus(quote_raw, stems, require_primary=bool(primary_stem))
                ):
                    quote_raw = self._best_quote_for_evidence(ev_text, stems)
                if not quote_raw:
                    continue

                if self._text_has_focus(quote_raw, stems, require_primary=bool(primary_stem)):
                    citation_has_focus = True

                normalized_citation = dict(citation)
                normalized_citation["quote"] = quote_raw
                normalized_citations.append(normalized_citation)

            if claim_has_focus or citation_has_focus:
                if not claim_has_focus and normalized_citations:
                    claim["text"] = self._claim_from_quote(str(normalized_citations[0].get("quote", "")), stems)
                claim["citations"] = normalized_citations
                kept_claims.append(claim)
                for citation in normalized_citations:
                    source_id = str(citation.get("source_id", "")).strip()
                    if source_id:
                        used_source_ids.add(source_id)

        if not kept_claims:
            return self._build_extractive_fallback(
                question,
                evidences,
                focus_terms=focus_terms,
                coverage_request=False,
                llm_error=llm_error,
            )

        validated["claims"] = self._dedupe_claims(kept_claims)[:8]
        sources = [source for source in validated.get("sources", []) if source.get("source_id") in used_source_ids]
        if not sources and used_source_ids:
            sources = []
            for source_id in sorted(used_source_ids):
                ev = evidence_map.get(source_id)
                if ev is None:
                    continue
                sources.append(
                    {
                        "source_id": ev["source_id"],
                        "chunk_id": ev["chunk_id"],
                        "doc_id": ev["doc_id"],
                        "file_name": ev["file_name"],
                        "file_path": ev["file_path"],
                        "page_start": ev["page_start"],
                        "page_end": ev["page_end"],
                    }
                )
        validated["sources"] = sources

        key_points = [str(item).strip() for item in validated.get("key_points", []) if str(item).strip()]
        filtered_points = []
        for point in key_points:
            if self._text_has_focus(point, stems, require_primary=bool(primary_stem)):
                filtered_points.append(point)
        if filtered_points:
            validated["key_points"] = filtered_points[:6]
        else:
            validated["key_points"] = [
                str(claim.get("text", "")).strip()
                for claim in kept_claims[:6]
                if str(claim.get("text", "")).strip()
            ]

        synopsis = str(validated.get("synopsis", "")).strip()
        if synopsis and not self._text_has_focus(synopsis, stems, require_primary=bool(primary_stem)):
            validated["synopsis"] = (
                f"Sintese focada em {focus_terms[0]} com base apenas nas citacoes recuperadas."
            )
        return validated

    @staticmethod
    def _focus_stems(focus_terms: list[str], question: str) -> list[str]:
        stems: list[str] = []
        if focus_terms:
            terms = focus_terms
        else:
            terms = [token for token in normalize_text(question).split() if len(token) >= 4]
        for term in terms:
            token = normalize_text(str(term))
            if not token:
                continue
            head = token.split()[0]
            stem = head[:7] if len(head) >= 7 else head
            if stem and stem not in stems:
                stems.append(stem)
        return stems[:4]

    @staticmethod
    def _text_has_focus(text: str, stems: list[str], require_primary: bool = False) -> bool:
        if not stems:
            return False
        normalized = normalize_text(text)
        if not normalized:
            return False
        if require_primary:
            primary = stems[0]
            if primary not in normalized:
                return False
        return any(stem in normalized for stem in stems)

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        if not text:
            return []
        chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
        return [re.sub(r"\s+", " ", chunk).strip() for chunk in chunks if chunk and chunk.strip()]

    @staticmethod
    def _looks_like_header(sentence: str) -> bool:
        clean = sentence.strip()
        if not clean:
            return True
        normalized = normalize_text(clean)
        if normalized.startswith("capitulo "):
            return True
        if normalized.startswith("parte "):
            return True
        if len(clean) <= 30 and clean.upper() == clean:
            return True
        if "•" in clean and clean.count("•") >= 2:
            return True
        return bool(re.fullmatch(r"[IVXLC0-9\-\s]+", clean))

    def _best_quote_for_evidence(self, text: str, focus_stems: list[str], max_len: int = 320) -> str:
        cleaned = self._clean_evidence_text(text)
        sentences = self._split_sentences(cleaned)
        if focus_stems:
            primary = focus_stems[0]
            for sentence in sentences:
                if primary in normalize_text(sentence) and len(sentence) >= 28:
                    return sentence[:max_len].strip()
        for sentence in sentences:
            if self._text_has_focus(sentence, focus_stems) and len(sentence) >= 28:
                return sentence[:max_len].strip()

        for sentence in sentences:
            if self._text_has_focus(sentence, focus_stems):
                return sentence[:max_len].strip()

        for sentence in sentences:
            if self._looks_like_header(sentence):
                continue
            if len(sentence) >= 40:
                return sentence[:max_len].strip()

        compact_text = cleaned
        return compact_text[:max_len] if compact_text else ""

    @staticmethod
    def _claim_from_quote(quote: str, focus_stems: list[str], max_len: int = 220) -> str:
        compact = re.sub(r"\s+", " ", quote).strip()
        if not compact:
            return ""
        sentence = re.split(r"(?<=[.!?])\s+", compact)[0].strip()
        if sentence:
            return sentence[:max_len]
        if focus_stems:
            return compact[:max_len]
        return compact[: max_len // 2]

    @staticmethod
    def _clean_evidence_text(text: str) -> str:
        if not text:
            return ""
        cleaned = text.replace("\u00ad", "")
        cleaned = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1 \2", cleaned)
        cleaned = re.sub(r"\s*\n+\s*", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not claims:
            return []
        deduped: list[dict[str, Any]] = []
        seen = set()
        for claim in claims:
            text_key = normalize_text(str(claim.get("text", "")))
            citations = claim.get("citations", [])
            source_id = ""
            quote_key = ""
            if isinstance(citations, list) and citations:
                source_id = str(citations[0].get("source_id", ""))
                quote_key = normalize_text(str(citations[0].get("quote", "")))
            key = (text_key, source_id, quote_key)
            weak_key = (source_id, quote_key)
            if not text_key:
                continue
            if key in seen or weak_key in seen:
                continue
            seen.add(key)
            seen.add(weak_key)
            deduped.append(claim)
        return deduped

    @staticmethod
    def _diversify_by_doc(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        if not candidates or limit <= 0:
            return []
        by_doc: dict[str, list[dict[str, Any]]] = {}
        for item in candidates:
            by_doc.setdefault(str(item.get("doc_id", "")), []).append(item)
        if len(by_doc) <= 1:
            return candidates[:limit]

        doc_order = sorted(
            by_doc.keys(),
            key=lambda doc_id: float(by_doc[doc_id][0].get("score", 0.0)),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        level = 0
        while len(selected) < limit:
            added = False
            for doc_id in doc_order:
                doc_items = by_doc[doc_id]
                if level >= len(doc_items):
                    continue
                item = doc_items[level]
                source_id = str(item.get("source_id") or item.get("chunk_id") or "")
                if source_id in selected_ids:
                    continue
                selected.append(item)
                if source_id:
                    selected_ids.add(source_id)
                added = True
                if len(selected) >= limit:
                    break
            if not added:
                break
            level += 1

        if len(selected) < limit:
            for item in candidates:
                source_id = str(item.get("source_id") or item.get("chunk_id") or "")
                if source_id and source_id in selected_ids:
                    continue
                selected.append(item)
                if source_id:
                    selected_ids.add(source_id)
                if len(selected) >= limit:
                    break
        return selected[:limit]

    @staticmethod
    def _balance_claims_by_doc(
        claims: list[dict[str, Any]],
        max_per_doc: int = 2,
        limit_total: int = 8,
    ) -> list[dict[str, Any]]:
        if not claims:
            return []
        by_doc: dict[str, list[dict[str, Any]]] = {}
        doc_order: list[str] = []
        for claim in claims:
            citations = claim.get("citations", [])
            if not isinstance(citations, list) or not citations:
                continue
            doc_id = str(citations[0].get("doc_id", "")).strip()
            if not doc_id:
                continue
            if doc_id not in by_doc:
                by_doc[doc_id] = []
                doc_order.append(doc_id)
            by_doc[doc_id].append(claim)
        if not by_doc:
            return claims[:limit_total]

        selected: list[dict[str, Any]] = []
        # First pass: guarantee at least one claim per doc.
        for doc_id in doc_order:
            doc_claims = by_doc.get(doc_id, [])
            if not doc_claims:
                continue
            selected.append(doc_claims[0])
            if len(selected) >= limit_total:
                return selected[:limit_total]

        # Second pass: add extras up to max_per_doc.
        for doc_id in doc_order:
            doc_claims = by_doc.get(doc_id, [])
            for claim in doc_claims[1:max_per_doc]:
                selected.append(claim)
                if len(selected) >= limit_total:
                    return selected[:limit_total]
        return selected[:limit_total]

    def _build_diverse_qa_from_claims(
        self,
        claims: list[dict[str, Any]],
        focus_terms: list[str],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        if not claims:
            return []
        topic = focus_terms[0] if focus_terms else "o tema"
        qa: list[dict[str, Any]] = []
        used_docs: set[str] = set()
        for claim in claims:
            citations = claim.get("citations", [])
            if not isinstance(citations, list) or not citations:
                continue
            citation = citations[0]
            doc_id = str(citation.get("doc_id", "")).strip()
            if not doc_id or doc_id in used_docs:
                continue
            used_docs.add(doc_id)
            file_name = str(citation.get("file_name", "")).strip() or "documento"
            qa.append(
                {
                    "question": f"Qual referencia de {topic} aparece em {file_name}?",
                    "answer": str(claim.get("text", "")).strip(),
                    "citations": [citation],
                }
            )
            if len(qa) >= limit:
                break
        return qa

    @staticmethod
    def _select_single_citation_per_claim_diverse(claims: list[dict[str, Any]]) -> None:
        if not claims:
            return
        used_docs: set[str] = set()
        for claim in claims:
            citations = claim.get("citations", [])
            if not isinstance(citations, list) or not citations:
                continue
            chosen = None
            for citation in citations:
                doc_id = str(citation.get("doc_id", "")).strip()
                if doc_id and doc_id not in used_docs:
                    chosen = citation
                    break
            if chosen is None:
                chosen = citations[0]
            doc_id = str(chosen.get("doc_id", "")).strip()
            if doc_id:
                used_docs.add(doc_id)
            claim["citations"] = [chosen]
