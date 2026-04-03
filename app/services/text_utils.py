from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


STOPWORDS_PT = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "como",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "que",
    "se",
    "sem",
    "sobre",
    "um",
    "uma",
    "ele",
    "ela",
    "eles",
    "elas",
    "dele",
    "dela",
    "deles",
    "delas",
    "nele",
    "nela",
    "neles",
    "nelas",
    "me",
    "quais",
    "qual",
    "trecho",
    "trechos",
    "passagem",
    "passagens",
    "tratam",
    "trata",
    "tratar",
    "objetiva",
    "objetivas",
    "objetivo",
    "objetivos",
    "quero",
    "aponte",
    "liste",
    "listar",
    "preciso",
    "gostaria",
    "pode",
    "poderia",
    "principais",
    "principal",
    "relacionada",
    "relacionadas",
    "relacionado",
    "relacionados",
    "citacao",
    "citacoes",
    "citar",
    "referencia",
    "referencias",
    "explicacao",
    "explicacoes",
    "explicar",
    "resumo",
    "sinopse",
    "sintese",
    "pontos",
    "chave",
    "sugestao",
    "sugestoes",
    "qa",
    "q&a",
    "todos",
    "todas",
    "topico",
    "topicos",
    "tema",
    "temas",
    "assunto",
    "assuntos",
    "fala",
    "falar",
    "falam",
    "falem",
    "falando",
    "fale",
    "explica",
    "explicam",
    "expliquem",
    "explicando",
    "especificamente",
    "especifico",
    "especifica",
    "especificos",
    "especificas",
    "detalhe",
    "detalhes",
    "detalhado",
    "detalhada",
    "detalhados",
    "detalhadas",
    "mostrar",
    "mostre",
    "mostram",
    "trazer",
    "traga",
    "traga me",
    "trazer me",
    "apenas",
    "somente",
    "diz",
    "dizem",
    "acerca",
    "relativo",
    "relativa",
    "relativos",
    "relativas",
    "monte",
    "montar",
    "monta",
    "crie",
    "criar",
    "cria",
    "gere",
    "gerar",
    "gera",
    "faca",
    "fazer",
    "facam",
    "elabore",
    "elaborar",
    "produza",
    "produzir",
    "escreva",
    "escrever",
    "formule",
    "formular",
    "organize",
    "organizar",
    "questionario",
    "pergunta",
    "perguntas",
    "resposta",
    "respostas",
    "lista",
    "listas",
    "roteiro",
    "roteiros",
    "contexto",
    "conversa",
    "anterior",
    "evidencia",
    "evidencias",
    "fornecida",
    "fornecidas",
    "fornecido",
    "fornecidos",
    "disponivel",
    "disponiveis",
}


@dataclass
class ChunkSlice:
    text: str
    page_number: int
    token_count: int
    char_start_in_page: int
    char_end_in_page: int


def normalize_text(text: str) -> str:
    fixed = _repair_mojibake(text)
    lowered = fixed.lower().strip()
    no_accents = "".join(
        ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn"
    )
    no_punct = re.sub(r"[^\w\s]", " ", no_accents)
    return re.sub(r"\s+", " ", no_punct).strip()


def _repair_mojibake(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    # Mojibake hints commonly seen as UTF-8 interpreted as Latin-1.
    if chr(195) not in raw and chr(194) not in raw and "\ufffd" not in raw:
        return raw
    try:
        candidate = raw.encode("latin1").decode("utf-8")
    except Exception:
        return raw
    return candidate if candidate else raw


def expand_query_terms(question: str, max_terms: int = 16) -> list[str]:
    normalized = normalize_text(question)
    if not normalized:
        return []
    tokens = [tok for tok in normalized.split() if len(tok) >= 3 and tok not in STOPWORDS_PT]
    terms: list[str] = []
    seen = set()
    for token in tokens:
        if token not in seen:
            terms.append(token)
            seen.add(token)
        if len(terms) >= max_terms:
            return terms
    for i in range(len(tokens) - 1):
        bigram = f"{tokens[i]} {tokens[i + 1]}"
        if bigram not in seen:
            terms.append(bigram)
            seen.add(bigram)
        if len(terms) >= max_terms:
            return terms
    return terms


def build_fts_query(terms: list[str], fallback_question: str) -> str:
    clean_terms = [t.strip() for t in terms if t.strip()]
    if not clean_terms:
        fallback = normalize_text(fallback_question)
        return f'"{fallback}"' if fallback else '""'
    wrapped = [f'"{term.replace(chr(34), " ")}"' for term in clean_terms]
    return " OR ".join(wrapped)


def chunk_page_text(page_text: str, page_number: int, chunk_size: int, overlap: int) -> list[ChunkSlice]:
    text = page_text or ""
    if not text.strip():
        return []
    token_spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    if not token_spans:
        return []
    chunks: list[ChunkSlice] = []
    i = 0
    local_index = 0
    while i < len(token_spans):
        end_idx = min(i + chunk_size, len(token_spans))
        char_start = token_spans[i][0]
        char_end = token_spans[end_idx - 1][1]
        chunk_text = text[char_start:char_end].strip()
        if chunk_text:
            chunks.append(
                ChunkSlice(
                    text=chunk_text,
                    page_number=page_number,
                    token_count=end_idx - i,
                    char_start_in_page=char_start,
                    char_end_in_page=char_end,
                )
            )
            local_index += 1
        if end_idx >= len(token_spans):
            break
        i = max(end_idx - overlap, i + 1)
    return chunks
