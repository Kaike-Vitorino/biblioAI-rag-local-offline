from __future__ import annotations

import json
import logging
import sqlite3
import re
from typing import Any

from app.config import Settings
from app.db.database import Database
from app.services.cache import TTLCache
from app.services.embedding import EmbeddingService
from app.services.query_planner import QueryPlanner
from app.services.text_utils import STOPWORDS_PT, build_fts_query, expand_query_terms, normalize_text
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        embedding_service: EmbeddingService,
        vector_store: VectorStore,
        query_planner: QueryPlanner | None = None,
    ):
        self.db = db
        self.settings = settings
        self.embedding_service = embedding_service
        self.vector_store = vector_store
        self.query_planner = query_planner
        self._result_cache: TTLCache[dict[str, Any]] = TTLCache(
            max_items=settings.cache_max_items,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        self._term_freq_cache: TTLCache[int] = TTLCache(
            max_items=settings.cache_max_items,
            ttl_seconds=settings.cache_ttl_seconds,
        )

    def retrieve(self, question: str, topk: int | None = None) -> dict[str, Any]:
        topk = topk or self.settings.topk
        cache_key = f"retr::{normalize_text(question)}::{topk}"
        cached = self._result_cache.get(cache_key)
        if cached is not None:
            return cached

        coverage_request = self._wants_broad_coverage(question)
        heuristic_terms = expand_query_terms(question)
        planned_terms: list[str] = []
        if self.query_planner is not None:
            planned_terms = self.query_planner.plan(question)
        terms = planned_terms + [term for term in heuristic_terms if term not in planned_terms]
        terms = self._dedupe_terms(terms)
        if not terms:
            terms = heuristic_terms
        focus_terms = self._determine_focus_terms(planned_terms, terms, question)
        fts_query = build_fts_query(terms, question)
        lexical_candidates = self._lexical_search(fts_query, self.settings.lexical_topn)
        core_terms = self._select_core_terms(terms)
        if core_terms:
            core_query = build_fts_query(core_terms, question)
            core_candidates = self._lexical_search(core_query, max(8, self.settings.lexical_topn // 2), lexical_boost=0.25)
            lexical_candidates.extend(core_candidates)
        vector_candidates = self._vector_search(question, self.settings.vector_topn)
        merged = self._merge_candidates(lexical_candidates, vector_candidates, focus_terms=focus_terms)
        final_chunks = self._select_final_chunks(merged, topk, coverage_request=coverage_request)
        all_references = self._build_all_references(
            merged=merged,
            selected_chunks=final_chunks,
            focus_terms=focus_terms,
            coverage_request=coverage_request,
        )

        evidences = []
        for item in final_chunks:
            evidences.append(
                {
                    "source_id": item["chunk_id"],
                    "chunk_id": item["chunk_id"],
                    "doc_id": item["doc_id"],
                    "file_name": item["file_name"],
                    "file_path": item["file_path"],
                    "page_start": item["page_start"],
                    "page_end": item["page_end"],
                    "text": item["text"],
                    "offsets": item.get("offsets"),
                    "score": item["score"],
                    "focus_match": bool(item.get("focus_match")),
                }
            )

        result = {
            "searched_terms": terms,
            "planned_terms": planned_terms,
            "core_terms": core_terms,
            "focus_terms": focus_terms,
            "fts_query": fts_query,
            "coverage_request": coverage_request,
            "evidence_docs_count": len({ev["doc_id"] for ev in evidences}),
            "evidences": evidences,
            "all_references": all_references,
            "candidates_count": len(merged),
        }
        self._result_cache.set(cache_key, result)
        return result

    def _lexical_search(self, fts_query: str, top_n: int, lexical_boost: float = 0.0) -> list[dict[str, Any]]:
        if not fts_query.strip() or fts_query.strip() == '""':
            return []
        try:
            rows = self.db.fetchall(
                """
                SELECT
                    c.chunk_id,
                    c.doc_id,
                    c.page_start,
                    c.page_end,
                    c.text,
                    c.offsets_json,
                    d.file_name,
                    d.file_path,
                    bm25(chunks_fts) AS bm25_score
                FROM chunks_fts
                JOIN chunks c ON c.rowid = chunks_fts.rowid
                JOIN docs d ON d.id = c.doc_id
                WHERE chunks_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                [fts_query, top_n],
            )
        except sqlite3.OperationalError:
            logger.warning("Invalid FTS query generated: %s", fts_query)
            return []
        results: list[dict[str, Any]] = []
        for rank, row in enumerate(rows):
            base_rank_score = 1.0 / (rank + 1)
            bm25_score = float(row["bm25_score"]) if row["bm25_score"] is not None else 0.0
            bm25_component = 1.0 / (1.0 + max(0.0, -bm25_score))
            lexical_score = max(base_rank_score, bm25_component) + lexical_boost
            results.append(
                {
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "page_start": row["page_start"],
                    "page_end": row["page_end"],
                    "text": row["text"],
                    "offsets": self._parse_offsets(row["offsets_json"]),
                    "file_name": row["file_name"],
                    "file_path": row["file_path"],
                    "lexical_score": lexical_score,
                    "vector_score": 0.0,
                }
            )
        return results

    def _vector_search(self, question: str, top_n: int) -> list[dict[str, Any]]:
        vector = self.embedding_service.embed_query(question)
        matches = self.vector_store.search(vector, top_n)
        if not matches:
            return []
        chunk_ids = [chunk_id for chunk_id, _ in matches]
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.db.fetchall(
            f"""
            SELECT
                c.chunk_id,
                c.doc_id,
                c.page_start,
                c.page_end,
                c.text,
                c.offsets_json,
                d.file_name,
                d.file_path
            FROM chunks c
            JOIN docs d ON d.id = c.doc_id
            WHERE c.chunk_id IN ({placeholders})
            """,
            chunk_ids,
        )
        by_chunk_id = {row["chunk_id"]: row for row in rows}
        results: list[dict[str, Any]] = []
        for chunk_id, raw_score in matches:
            row = by_chunk_id.get(chunk_id)
            if row is None:
                continue
            norm_score = max(0.0, min(1.0, (raw_score + 1.0) / 2.0))
            results.append(
                {
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "page_start": row["page_start"],
                    "page_end": row["page_end"],
                    "text": row["text"],
                    "offsets": self._parse_offsets(row["offsets_json"]),
                    "file_name": row["file_name"],
                    "file_path": row["file_path"],
                    "lexical_score": 0.0,
                    "vector_score": norm_score,
                }
            )
        return results

    def _merge_candidates(
        self,
        lexical_candidates: list[dict[str, Any]],
        vector_candidates: list[dict[str, Any]],
        focus_terms: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}

        for item in lexical_candidates:
            merged[item["chunk_id"]] = dict(item)

        for item in vector_candidates:
            existing = merged.get(item["chunk_id"])
            if existing is None:
                merged[item["chunk_id"]] = dict(item)
            else:
                existing["vector_score"] = max(existing["vector_score"], item["vector_score"])

        final = []
        focus_terms = focus_terms or []
        for candidate in merged.values():
            score = 0.55 * float(candidate.get("lexical_score", 0.0)) + 0.45 * float(candidate.get("vector_score", 0.0))
            if self._is_index_like(candidate.get("text", "")):
                score *= 0.3
            focus_signal = self._focus_signal(candidate.get("text", ""), focus_terms)
            focus_match = focus_signal["matched"]
            primary_match = bool(focus_signal.get("primary_matched", False))
            candidate["focus_match"] = focus_match
            if focus_terms:
                if focus_match:
                    score += 0.22
                    score += 0.18 * float(focus_signal["density"])
                    score += 0.1 * float(focus_signal["earlyness"])
                    if primary_match:
                        score += 0.12
                    else:
                        score -= 0.1
                else:
                    score -= 0.25
            if self._is_low_information(candidate.get("text", "")):
                score -= 0.15
            candidate["score"] = score
            final.append(candidate)
        final.sort(key=lambda x: x["score"], reverse=True)
        return final

    @staticmethod
    def _parse_offsets(offsets_json: str | None) -> dict[str, Any] | None:
        if not offsets_json:
            return None
        try:
            return json.loads(offsets_json)
        except json.JSONDecodeError:
            return None

    def _select_core_terms(self, terms: list[str]) -> list[str]:
        unigrams = [term for term in terms if " " not in term]
        scored: list[tuple[float, str]] = []
        for term in unigrams:
            if len(term) < 4:
                continue
            freq = self._term_doc_freq(term)
            if freq <= 0:
                continue
            specificity = (min(len(term), 18) / 18.0) * (1.0 / (freq + 1))
            scored.append((specificity, term))
        scored.sort(reverse=True)
        selected = [term for _, term in scored[:2]]
        return selected

    def _term_doc_freq(self, term: str) -> int:
        cache_key = f"df::{term}"
        cached = self._term_freq_cache.get(cache_key)
        if cached is not None:
            return cached
        query = f'"{term.replace(chr(34), " ")}"'
        try:
            row = self.db.fetchone("SELECT count(*) AS n FROM chunks_fts WHERE chunks_fts MATCH ?", [query])
            value = int(row["n"]) if row else 0
        except sqlite3.OperationalError:
            value = 0
        self._term_freq_cache.set(cache_key, value)
        return value

    @staticmethod
    def _is_index_like(text: str) -> bool:
        if not text:
            return False
        head = normalize_text(text[:220])
        if not head:
            return False
        return bool(
            re.search(r"\bindice\b", head)
            or "index geral" in head
            or "indice geral" in head
        )

    @staticmethod
    def _term_stems(terms: list[str]) -> list[str]:
        stems: list[str] = []
        for term in terms:
            normalized = normalize_text(term)
            if not normalized:
                continue
            token = normalized.split()[0]
            if len(token) >= 5:
                stems.append(token[:6])
            elif len(token) >= 3:
                stems.append(token)
        return list(dict.fromkeys(stems))

    def _matches_focus(self, text: str, focus_terms: list[str]) -> bool:
        return self._focus_signal(text, focus_terms)["matched"]

    def _determine_focus_terms(self, planned_terms: list[str], terms: list[str], question: str) -> list[str]:
        question_terms = [
            tok
            for tok in normalize_text(question).split()
            if tok and tok not in STOPWORDS_PT and len(tok) >= 4
        ]
        if planned_terms:
            planned_clean = [
                term
                for term in planned_terms
                if " " not in term and len(term) >= 4 and term not in STOPWORDS_PT
            ]
            planned_selected = self._rank_focus_terms(planned_clean + question_terms, question)
            planned_selected = self._prioritize_question_terms(planned_selected, question_terms)
            if planned_selected:
                return planned_selected[:2]
            if planned_clean:
                return planned_clean[:2]
        candidate_pool = self._select_core_terms(terms) + [
            term for term in terms if " " not in term and len(term) >= 4 and term not in STOPWORDS_PT
        ]
        selected = self._rank_focus_terms(candidate_pool + question_terms, question)
        selected = self._prioritize_question_terms(selected, question_terms)
        if selected:
            return selected[:2]
        # Fallback: prioritize informative unigrams from expanded terms.
        tokens = [term for term in terms if " " not in term and len(term) >= 5 and term not in STOPWORDS_PT]
        tokens = self._rank_focus_terms(tokens, question)
        if tokens:
            return tokens[:2]
        normalized_question = normalize_text(question)
        for token in normalized_question.split():
            if len(token) >= 4 and token not in STOPWORDS_PT:
                return [token]
        return []

    @staticmethod
    def _dedupe_terms(terms: list[str]) -> list[str]:
        unique: list[str] = []
        seen = set()
        for term in terms:
            normalized = normalize_text(term)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique

    def _focus_signal(self, text: str, focus_terms: list[str]) -> dict[str, float | bool]:
        normalized = normalize_text(text)
        if not normalized:
            return {"matched": False, "primary_matched": False, "density": 0.0, "earlyness": 0.0}
        stems = self._term_stems(focus_terms)
        if not stems:
            return {"matched": False, "primary_matched": False, "density": 0.0, "earlyness": 0.0}

        total_hits = 0
        primary_hits = 0
        first_position: int | None = None
        for idx, stem in enumerate(stems):
            for match in re.finditer(re.escape(stem), normalized):
                total_hits += 1
                if idx == 0:
                    primary_hits += 1
                if first_position is None or match.start() < first_position:
                    first_position = match.start()

        if total_hits <= 0:
            return {"matched": False, "primary_matched": False, "density": 0.0, "earlyness": 0.0}

        primary_matched = primary_hits > 0
        if len(stems) > 1 and not primary_matched:
            return {"matched": False, "primary_matched": False, "density": 0.0, "earlyness": 0.0}

        density = min(1.0, total_hits / max(2, len(stems) * 2))
        if first_position is None:
            earlyness = 0.0
        else:
            earlyness = max(0.0, 1.0 - (first_position / max(1, len(normalized))))
        return {
            "matched": True,
            "primary_matched": primary_matched,
            "density": density,
            "earlyness": earlyness,
        }

    @staticmethod
    def _is_low_information(text: str) -> bool:
        normalized = normalize_text(text)
        if not normalized:
            return True
        tokens = normalized.split()
        if len(tokens) < 12:
            return True
        letters = sum(ch.isalpha() for ch in normalized)
        digits = sum(ch.isdigit() for ch in normalized)
        if letters <= 0:
            return True
        return digits > letters * 0.35

    @staticmethod
    def _wants_broad_coverage(question: str) -> bool:
        normalized = normalize_text(question)
        if not normalized:
            return False
        broad_patterns = [
            "todos os livros",
            "de todos os livros",
            "em todos os livros",
            "de cada livro",
            "cada livro",
            "todas as referencias",
            "todas referencias",
            "todas as citacoes",
            "todas citacoes",
            "todas as obras",
            "todos os documentos",
        ]
        return any(pattern in normalized for pattern in broad_patterns)

    def _select_final_chunks(
        self,
        merged: list[dict[str, Any]],
        topk: int,
        coverage_request: bool,
    ) -> list[dict[str, Any]]:
        if not merged or topk <= 0:
            return []
        if not coverage_request:
            return merged[:topk]

        by_doc: dict[str, list[dict[str, Any]]] = {}
        for item in merged:
            by_doc.setdefault(item["doc_id"], []).append(item)
        if len(by_doc) <= 1:
            return merged[:topk]

        doc_order = sorted(
            by_doc.keys(),
            key=lambda doc_id: float(by_doc[doc_id][0].get("score", 0.0)),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        level = 0
        while len(selected) < topk:
            added = False
            for doc_id in doc_order:
                doc_items = by_doc[doc_id]
                if level >= len(doc_items):
                    continue
                candidate = doc_items[level]
                chunk_id = str(candidate.get("chunk_id"))
                if chunk_id in selected_ids:
                    continue
                selected.append(candidate)
                selected_ids.add(chunk_id)
                added = True
                if len(selected) >= topk:
                    break
            if not added:
                break
            level += 1

        if len(selected) < topk:
            for candidate in merged:
                chunk_id = str(candidate.get("chunk_id"))
                if chunk_id in selected_ids:
                    continue
                selected.append(candidate)
                selected_ids.add(chunk_id)
                if len(selected) >= topk:
                    break
        return selected[:topk]

    def _build_all_references(
        self,
        merged: list[dict[str, Any]],
        selected_chunks: list[dict[str, Any]],
        focus_terms: list[str],
        coverage_request: bool,
    ) -> list[dict[str, Any]]:
        if not merged:
            return []

        selected_ids = {str(item.get("chunk_id", "")) for item in selected_chunks}
        ordered = [item for item in merged if str(item.get("chunk_id", "")) in selected_ids] + merged
        all_refs: list[dict[str, Any]] = []
        seen: set[str] = set()

        max_items = 1200 if coverage_request else 600
        for item in ordered:
            chunk_id = str(item.get("chunk_id", "")).strip()
            if not chunk_id or chunk_id in seen:
                continue
            if chunk_id not in selected_ids and not self._is_related_candidate(item, focus_terms):
                continue
            seen.add(chunk_id)
            all_refs.append(
                {
                    "source_id": chunk_id,
                    "chunk_id": chunk_id,
                    "doc_id": item["doc_id"],
                    "file_name": item["file_name"],
                    "file_path": item["file_path"],
                    "page_start": item["page_start"],
                    "page_end": item["page_end"],
                    "score": float(item.get("score", 0.0)),
                    "focus_match": bool(item.get("focus_match")),
                }
            )
            if len(all_refs) >= max_items:
                break
        return all_refs

    def _is_related_candidate(self, candidate: dict[str, Any], focus_terms: list[str]) -> bool:
        text = str(candidate.get("text", ""))
        if self._is_low_information(text):
            return False

        if focus_terms:
            if bool(candidate.get("focus_match")):
                return True
            signal = self._focus_signal(text, focus_terms)
            if not bool(signal.get("matched")):
                return False
            return float(signal.get("density", 0.0)) >= 0.15

        lexical_score = float(candidate.get("lexical_score", 0.0))
        vector_score = float(candidate.get("vector_score", 0.0))
        combined_score = float(candidate.get("score", 0.0))
        return combined_score >= 0.0 or lexical_score >= 0.15 or vector_score >= 0.45

    def _rank_focus_terms(self, candidates: list[str], question: str) -> list[str]:
        if not candidates:
            return []
        normalized_question = normalize_text(question)
        question_tokens = [tok for tok in normalized_question.split() if tok and tok not in STOPWORDS_PT]
        position_map: dict[str, int] = {}
        for idx, tok in enumerate(question_tokens):
            if tok not in position_map:
                position_map[tok] = idx

        unique = []
        seen = set()
        for term in candidates:
            t = normalize_text(term).split()[0] if normalize_text(term) else ""
            if not t or t in seen:
                continue
            seen.add(t)
            unique.append(t)

        def _sort_key(term: str) -> tuple[int, int, float]:
            pos = position_map.get(term, 10_000)
            freq = self._term_doc_freq(term)
            specificity = (min(len(term), 18) / 18.0) * (1.0 / (freq + 1))
            return (pos, freq, -specificity)

        unique.sort(key=_sort_key)
        return unique

    @staticmethod
    def _prioritize_question_terms(ranked_terms: list[str], question_terms: list[str]) -> list[str]:
        if not ranked_terms:
            return []
        if not question_terms:
            return ranked_terms
        primary = next((tok for tok in question_terms if tok in ranked_terms), None)
        if primary is None or primary == ranked_terms[0]:
            return ranked_terms
        return [primary] + [term for term in ranked_terms if term != primary]
