from __future__ import annotations

import json
import logging
import re
from typing import Any, Generator

import requests
from fastapi.responses import StreamingResponse

from app.config import Settings

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
Voce e um assistente RAG estrito.
Regras absolutas:
1) Use SOMENTE as evidencias fornecidas.
2) Cada claim precisa de pelo menos uma citacao valida usando source_id.
3) Toda claim deve estar diretamente ligada ao foco da pergunta.
4) Se nao houver evidencia suficiente, retorne not_found=true.
5) Nao invente fatos nem fontes.
6) Seja conciso e objetivo.
7) Retorne JSON puro, sem markdown.
""".strip()


JSON_SCHEMA_HINT = {
    "not_found": "boolean",
    "synopsis": "string",
    "key_points": ["string"],
    "suggested_qa": [
        {
            "question": "string",
            "answer": "string",
            "citations": [{"source_id": "string", "quote": "string"}],
        }
    ],
    "claims": [
        {
            "claim_id": "string",
            "text": "string",
            "citations": [{"source_id": "string", "quote": "string"}],
        }
    ],
}

GENERATIVE_TASK_MARKERS = {
    "monte",
    "montar",
    "crie",
    "criar",
    "gere",
    "gerar",
    "faca",
    "fazer",
    "elabore",
    "elaborar",
    "escreva",
    "escrever",
    "produza",
    "produzir",
    "formule",
    "formular",
    "organize",
    "organizar",
    "questoes",
    "questionario",
    "perguntas",
    "respostas",
    "lista",
    "roteiro",
}


EVIDENCE_VALIDATION_SCHEMA_HINT = {
    "keep_source_ids": ["string"],
    "discarded": [{"source_id": "string", "reason": "string"}],
}


class LLMService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def generate_answer(
        self,
        question: str,
        evidences: list[dict[str, Any]],
        focus_terms: list[str] | None = None,
        coverage_request: bool = False,
        retry_mode: bool = False,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        prompt = self._build_user_prompt(
            question,
            evidences,
            focus_terms=focus_terms or [],
            coverage_request=coverage_request,
            retry_mode=retry_mode,
            conversation_history=conversation_history or [],
        )
        payload = {
            "model": self.settings.model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.settings.temperature,
                "num_ctx": min(self.settings.num_ctx, 4096),
            },
        }
        response = requests.post(
            f"{self.settings.ollama_base_url}/api/generate",
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        raw = response.json().get("response", "")
        parsed = self._parse_json(raw)
        if parsed is None:
            logger.warning(
                "LLM returned non-JSON response. Returning fallback not_found."
            )
            return {"not_found": True}
        return parsed

    def generate_stream(
        self,
        question: str,
        evidences: list[dict[str, Any]],
        focus_terms: list[str] | None = None,
        coverage_request: bool = False,
        retry_mode: bool = False,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> Generator[str, None, None]:
        prompt = self._build_user_prompt(
            question,
            evidences,
            focus_terms=focus_terms or [],
            coverage_request=coverage_request,
            retry_mode=retry_mode,
            conversation_history=conversation_history or [],
        )
        payload = {
            "model": self.settings.model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": True,
            "format": "json",
            "options": {
                "temperature": self.settings.temperature,
                "num_ctx": min(self.settings.num_ctx, 4096),
            },
        }
        try:
            with requests.post(
                f"{self.settings.ollama_base_url}/api/generate",
                json=payload,
                stream=True,
                timeout=120,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "response" in data:
                                yield data["response"]
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            raise

    def validate_evidence_relevance(
        self,
        question: str,
        evidences: list[dict[str, Any]],
        focus_terms: list[str] | None = None,
        max_keep: int = 8,
    ) -> list[str]:
        if not evidences:
            return []
        compact_evidences: list[dict[str, Any]] = []
        for ev in evidences:
            compact_evidences.append(
                {
                    "source_id": ev.get("source_id"),
                    "file_name": ev.get("file_name"),
                    "page_start": ev.get("page_start"),
                    "page_end": ev.get("page_end"),
                    "text": str(ev.get("text", ""))[:900],
                }
            )

        focus_text = (
            ", ".join(focus_terms or []) if focus_terms else "(sem termos focais)"
        )
        prompt = (
            "Voce e um verificador de evidencias para RAG.\n"
            "Retorne APENAS JSON valido no schema fornecido.\n"
            "Mantenha somente evidencias que respondem DIRETAMENTE a pergunta.\n"
            "Remova evidencias vagas, fora de contexto, ou com aderencia fraca.\n"
            "Se houver multiplos termos focais, prefira evidencias que cubram todos.\n"
            f"Pergunta: {question}\n"
            f"Termos focais: {focus_text}\n"
            f"Maximo de evidencias para manter: {max_keep}\n"
            f"Schema: {json.dumps(EVIDENCE_VALIDATION_SCHEMA_HINT, ensure_ascii=False)}\n"
            f"Evidencias candidatas: {json.dumps(compact_evidences, ensure_ascii=False)}"
        )

        payload = {
            "model": self.settings.model,
            "system": "Valide relevancia de evidencias com rigor e retorne apenas JSON.",
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.0,
                "num_ctx": self.settings.num_ctx,
            },
        }

        try:
            response = requests.post(
                f"{self.settings.ollama_base_url}/api/generate",
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
            parsed = self._parse_json(raw)
            if not parsed:
                return [
                    str(ev.get("source_id", ""))
                    for ev in evidences
                    if ev.get("source_id")
                ]

            keep_source_ids = parsed.get("keep_source_ids", [])
            if not isinstance(keep_source_ids, list):
                return [
                    str(ev.get("source_id", ""))
                    for ev in evidences
                    if ev.get("source_id")
                ]

            allowed = {
                str(ev.get("source_id", ""))
                for ev in evidences
                if str(ev.get("source_id", "")).strip()
            }
            filtered = [
                sid
                for sid in [str(s).strip() for s in keep_source_ids]
                if sid in allowed
            ]
            if not filtered:
                return [
                    str(ev.get("source_id", ""))
                    for ev in evidences
                    if ev.get("source_id")
                ]
            return filtered[:max_keep]
        except Exception as exc:
            logger.warning(
                "Evidence validation failed. Using original evidences. Error: %s", exc
            )
            return [
                str(ev.get("source_id", "")) for ev in evidences if ev.get("source_id")
            ]

    def _build_user_prompt(
        self,
        question: str,
        evidences: list[dict[str, Any]],
        focus_terms: list[str],
        coverage_request: bool,
        retry_mode: bool = False,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> str:
        evidence_lines = []
        available_docs: list[str] = []
        seen_docs: set[str] = set()
        qa_target = self._requested_qa_count(question)
        is_generative_task = self._is_generative_task(question)
        prompt_evidences = evidences[:6]
        for ev in prompt_evidences:
            evidence_lines.append(
                {
                    "source_id": ev["source_id"],
                    "chunk_id": ev["chunk_id"],
                    "doc_id": ev["doc_id"],
                    "file_name": ev["file_name"],
                    "file_path": ev["file_path"],
                    "page_start": ev["page_start"],
                    "page_end": ev["page_end"],
                    "text": self._compact_evidence_text(str(ev["text"])),
                }
            )
            doc_name = str(ev.get("file_name", ""))
            if doc_name and doc_name not in seen_docs:
                seen_docs.add(doc_name)
                available_docs.append(doc_name)
        retry_instruction = ""
        if retry_mode:
            retry_instruction = (
                "\nModo de correcao: existem evidencias relevantes para a pergunta."
                "\nNao marque not_found se houver ao menos 1 claim ancorada em citacao valida."
                "\nUse claims curtas e citacoes literais dos trechos."
            )
        coverage_instruction = ""
        if coverage_request and len(available_docs) > 1:
            min_docs = min(4, len(available_docs))
            coverage_instruction = (
                "\n5) A pergunta pede cobertura ampla. Distribua as citacoes entre documentos diferentes."
                f"\n6) Tente usar no minimo {min_docs} livros distintos quando houver evidencias."
            )
        focus_text = ", ".join(focus_terms) if focus_terms else "(nao informado)"
        history_lines: list[dict[str, str]] = []
        for turn in conversation_history or []:
            role = str(turn.get("role", "")).strip()
            content = str(turn.get("content", "")).strip()
            if not role or not content:
                continue
            history_lines.append({"role": role, "content": content[:700]})
        schema_hint = dict(JSON_SCHEMA_HINT)
        if qa_target:
            schema_hint["suggested_qa"] = [
                {
                    "question": f"string (gere exatamente {qa_target} itens quando houver evidencia suficiente)",
                    "answer": "string",
                    "citations": [{"source_id": "string", "quote": "string"}],
                }
            ]
        task_instruction = ""
        if is_generative_task:
            task_instruction = (
                "\n5) A pergunta pede uma saida composta pelo modelo, como lista, questionario ou roteiro."
                "\n6) Monte a resposta final a partir das evidencias, sem copiar a pergunta como unica sugestao."
                "\n7) Voce PODE reorganizar, resumir e redigir perguntas/respostas derivadas das evidencias, desde que todas permanecam ancoradas nas citacoes."
            )
            if qa_target:
                task_instruction += (
                    f"\n8) Gere exatamente {qa_target} itens em suggested_qa se houver material suficiente."
                )
        limits_instruction = (
            f"3) Mantenha no maximo 5 key_points, {qa_target or 3} suggested_qa e 6 claims.\n"
            "4) Se nao houver evidencia suficiente, retorne not_found=true e arrays vazios."
        )
        return (
            "Pergunta do usuario:\n"
            f"{question}\n\n"
            "Contexto da conversa anterior (se existir):\n"
            f"{json.dumps(history_lines, ensure_ascii=False)}\n\n"
            "Termos-foco obrigatorios para relevancia:\n"
            f"{focus_text}\n\n"
            "Documentos disponiveis:\n"
            f"{json.dumps(available_docs, ensure_ascii=False)}\n\n"
            "EVIDENCIAS (somente estas podem ser usadas):\n"
            f"{json.dumps(evidence_lines, ensure_ascii=False)}\n\n"
            "Schema JSON obrigatorio:\n"
            f"{json.dumps(schema_hint, ensure_ascii=False)}\n\n"
            "Regras adicionais de resposta:\n"
            "1) Cada claim deve citar explicitamente o tema da pergunta ou sinonimo direto presente nas evidencias.\n"
            "2) Evite claims genericas que nao sustentem o foco.\n"
            f"{limits_instruction}"
            f"{task_instruction}"
            f"{coverage_instruction}"
            f"{retry_instruction}"
        )

    @staticmethod
    def _requested_qa_count(question: str) -> int | None:
        normalized = re.sub(r"\s+", " ", question.lower())
        match = re.search(r"(\d{1,2})\s+(?:perguntas|questoes|questões)", normalized)
        if not match:
            return None
        try:
            value = int(match.group(1))
        except ValueError:
            return None
        if value <= 0:
            return None
        return min(value, 10)

    @staticmethod
    def _is_generative_task(question: str) -> bool:
        normalized = re.sub(r"\s+", " ", question.lower())
        return any(marker in normalized for marker in GENERATIVE_TASK_MARKERS)

    @staticmethod
    def _compact_evidence_text(text: str, max_chars: int = 700) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if len(compact) <= max_chars:
            return compact
        return compact[:max_chars].rstrip() + "..."

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        raw = raw.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return None
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
