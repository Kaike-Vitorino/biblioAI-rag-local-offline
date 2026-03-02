from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from app.config import Settings
from app.services.cache import TTLCache
from app.services.text_utils import STOPWORDS_PT, normalize_text

logger = logging.getLogger(__name__)


PLANNER_SYSTEM_PROMPT = """
Você extrai termos de busca para recuperação semântica/lexical.
Responda JSON puro com:
{
  "focus_terms": ["..."],
  "alternate_terms": ["..."]
}
Regras:
1) Incluir apenas conceitos de conteúdo (nunca intenções de pedido como "quero", "citações", "explicações", "resumo").
2) Máximo 6 termos totais.
3) Preferir substantivos/entidades da pergunta.
4) Termos em português normalizado sem pontuação.
""".strip()


class QueryPlanner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.ollama_base_url
        self.model = settings.query_planner_model
        self.timeout = settings.query_planner_timeout
        self.enabled = settings.query_planner_enabled
        self._cache: TTLCache[list[str]] = TTLCache(
            max_items=settings.cache_max_items,
            ttl_seconds=settings.cache_ttl_seconds,
        )

    def plan(self, question: str) -> list[str]:
        normalized_question = normalize_text(question)
        if not self.enabled or not normalized_question:
            return []
        allowed_tokens = {
            token
            for token in normalized_question.split()
            if token and token not in STOPWORDS_PT and len(token) >= 3
        }
        cache_key = f"planner::{normalized_question}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        terms = self._call_planner(question, allowed_tokens=allowed_tokens)
        if not terms:
            terms = self._fallback_terms(normalized_question)
        self._cache.set(cache_key, terms)
        return terms

    def _call_planner(self, question: str, allowed_tokens: set[str]) -> list[str]:
        payload = {
            "model": self.model,
            "system": PLANNER_SYSTEM_PROMPT,
            "prompt": f"Pergunta:\n{question}\n\nRetorne apenas JSON.",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_ctx": min(self.settings.num_ctx, 4096)},
        }
        try:
            response = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
            response.raise_for_status()
            raw = response.json().get("response", "")
            data = self._parse_json(raw)
            if not isinstance(data, dict):
                return []
            focus = data.get("focus_terms", [])
            alternates = data.get("alternate_terms", [])
            combined: list[str] = []
            if isinstance(focus, list):
                combined.extend([str(item) for item in focus])
            if isinstance(alternates, list):
                combined.extend([str(item) for item in alternates])
            return self._sanitize_terms(combined, allowed_tokens=allowed_tokens)
        except Exception as exc:
            logger.warning("Query planner LLM failed; falling back to heuristic extraction: %s", exc)
            return []

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None

    def _fallback_terms(self, normalized_question: str) -> list[str]:
        tokens = [tok for tok in normalized_question.split() if tok and tok not in STOPWORDS_PT and len(tok) >= 3]
        terms: list[str] = []
        seen = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= 6:
                break
        return terms

    def _sanitize_terms(self, terms: list[str], allowed_tokens: set[str]) -> list[str]:
        clean: list[str] = []
        seen = set()
        for term in terms:
            normalized = normalize_text(term)
            if not normalized:
                continue
            for token in normalized.split():
                if token in STOPWORDS_PT or len(token) < 3:
                    continue
                if allowed_tokens and token not in allowed_tokens:
                    continue
                if token not in seen:
                    seen.add(token)
                    clean.append(token)
                if len(clean) >= 6:
                    return clean
        return clean
