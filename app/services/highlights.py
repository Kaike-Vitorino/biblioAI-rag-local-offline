from __future__ import annotations

import json
from typing import Any

from app.db.database import Database


class HighlightService:
    def __init__(self, db: Database):
        self.db = db

    def get_highlight(self, source_id: str, preferred_snippet: str | None = None) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT c.chunk_id, c.doc_id, c.page_start, c.page_end, c.text, c.offsets_json
            FROM chunks c
            WHERE c.chunk_id = ? OR c.id = ?
            LIMIT 1
            """,
            [source_id, source_id],
        )
        if row is None:
            return None

        doc_id = row["doc_id"]
        page_start = int(row["page_start"])
        page_end = int(row["page_end"])
        offsets = self._safe_json(row["offsets_json"])

        chunk_text = str(row["text"] or "")
        snippet = (preferred_snippet or "").strip()
        if not snippet:
            snippet = str((offsets or {}).get("snippet") or "").strip()
        if not snippet:
            snippet = chunk_text[:320]

        page_row = self.db.fetchone(
            """
            SELECT text
            FROM pages
            WHERE doc_id = ? AND page_number = ?
            """,
            [doc_id, page_start],
        )
        page_text = str(page_row["text"] if page_row else "")

        start, end = self._find_snippet_bounds(page_text, snippet)

        highlights: list[dict[str, Any]] = []
        method = "snippet"

        if preferred_snippet:
            highlights.append(
                {
                    "page_number": page_start,
                    "start": start,
                    "end": end,
                    "snippet": snippet,
                    "approximate": start is None,
                }
            )
            method = "preferred_snippet"
        elif offsets and "char_start_in_page" in offsets and "char_end_in_page" in offsets:
            offset_start = int(offsets.get("char_start_in_page", 0))
            offset_end = int(offsets.get("char_end_in_page", 0))
            highlights.append(
                {
                    "page_number": int(offsets.get("page_number", page_start)),
                    "start": start if start is not None else offset_start,
                    "end": end if end is not None else offset_end,
                    "snippet": snippet,
                    "approximate": start is None,
                }
            )
            method = "offsets+snippet"
        else:
            highlights.append(
                {
                    "page_number": page_start,
                    "start": start,
                    "end": end,
                    "snippet": snippet,
                    "approximate": True,
                }
            )
            method = "snippet"

        return {
            "source_id": source_id,
            "doc_id": doc_id,
            "page_start": page_start,
            "page_end": page_end,
            "method": method,
            "highlights": highlights,
        }

    @staticmethod
    def _find_snippet_bounds(page_text: str, snippet: str) -> tuple[int | None, int | None]:
        if not page_text or not snippet:
            return None, None

        direct = page_text.find(snippet)
        if direct >= 0:
            return direct, direct + len(snippet)

        compact_snippet = " ".join(snippet.split())
        if not compact_snippet:
            return None, None
        compact_page = " ".join(page_text.split())
        compact_pos = compact_page.find(compact_snippet)
        if compact_pos < 0:
            return None, None

        # Approximate mapping back to original page string by matching the first token.
        first_token = compact_snippet.split()[0]
        token_pos = page_text.find(first_token)
        if token_pos < 0:
            return None, None
        return token_pos, token_pos + len(compact_snippet)

    @staticmethod
    def _safe_json(raw: str | None) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
