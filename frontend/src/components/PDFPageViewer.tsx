import { useEffect, useMemo, useRef, useState } from "react";
import {
  GlobalWorkerOptions,
  Util,
  getDocument,
  type PDFDocumentProxy
} from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

type PDFPageViewerProps = {
  pdfUrl: string;
  pageNumber: number;
  snippet: string;
  matchIndex: number;
  onPageCount?: (pageCount: number) => void;
  onMatchCount?: (matchCount: number) => void;
};

function normalizeForMatch(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function findSnippetMatches(spans: HTMLSpanElement[], snippet: string): number[][] {
  const normalizedSnippet = normalizeForMatch(snippet);
  if (!normalizedSnippet) return [];
  const matches: number[][] = [];
  const seen = new Set<string>();

  for (let i = 0; i < spans.length; i += 1) {
    let combined = "";
    const indices: number[] = [];
    for (let j = i; j < spans.length && j < i + 32; j += 1) {
      const text = normalizeForMatch(spans[j].textContent ?? "");
      if (!text) continue;
      combined = combined ? `${combined} ${text}` : text;
      indices.push(j);
      if (combined.includes(normalizedSnippet)) {
        const key = `${indices[0]}-${indices[indices.length - 1]}`;
        if (!seen.has(key)) {
          seen.add(key);
          matches.push([...indices]);
        }
        break;
      }
      if (combined.length > Math.max(120, normalizedSnippet.length * 2.3)) break;
    }
  }
  return matches;
}

export default function PDFPageViewer({
  pdfUrl,
  pageNumber,
  snippet,
  matchIndex,
  onPageCount,
  onMatchCount
}: PDFPageViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [currentPage, setCurrentPage] = useState(pageNumber);

  const boundedPage = useMemo(() => {
    if (!pdfDoc) return Math.max(1, pageNumber);
    return Math.min(Math.max(1, pageNumber), pdfDoc.numPages);
  }, [pageNumber, pdfDoc]);

  useEffect(() => {
    setCurrentPage(pageNumber);
  }, [pageNumber]);

  useEffect(() => {
    let cancelled = false;
    async function loadPdf() {
      setLoading(true);
      setLoadError(null);
      try {
        const loadingTask = getDocument(pdfUrl);
        const doc = await loadingTask.promise;
        if (cancelled) return;
        setPdfDoc(doc);
        onPageCount?.(doc.numPages);
      } catch (error) {
        if (cancelled) return;
        setPdfDoc(null);
        setLoadError(error instanceof Error ? error.message : "Falha ao abrir PDF.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadPdf();
    return () => {
      cancelled = true;
    };
  }, [pdfUrl, onPageCount]);

  useEffect(() => {
    if (!pdfDoc) return;
    const doc = pdfDoc;
    let cancelled = false;
    async function renderPage() {
      const canvas = canvasRef.current;
      const textLayer = textLayerRef.current;
      if (!canvas || !textLayer) return;
      const page = await doc.getPage(boundedPage);
      if (cancelled) return;
      const viewport = page.getViewport({ scale: 1.35 });
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      textLayer.style.width = `${viewport.width}px`;
      textLayer.style.height = `${viewport.height}px`;
      textLayer.innerHTML = "";

      await page.render({ canvasContext: ctx, viewport }).promise;
      if (cancelled) return;

      const textContent = await page.getTextContent();
      if (cancelled) return;
      for (const item of textContent.items as Array<Record<string, unknown>>) {
        const text = String(item.str ?? "");
        if (!text.trim()) continue;
        const transform = item.transform as number[];
        const tx = Util.transform(viewport.transform, transform);
        const angle = Math.atan2(tx[1], tx[0]);
        const fontHeight = Math.sqrt(tx[2] * tx[2] + tx[3] * tx[3]) || 10;

        const span = document.createElement("span");
        span.className = "text-fragment";
        span.textContent = text;
        span.style.left = `${tx[4]}px`;
        span.style.top = `${tx[5] - fontHeight}px`;
        span.style.fontSize = `${fontHeight}px`;
        span.style.transformOrigin = "left bottom";
        if (Math.abs(angle) > 0.01) {
          span.style.transform = `rotate(${angle}rad)`;
        }
        textLayer.appendChild(span);
      }

      const spans = Array.from(textLayer.querySelectorAll<HTMLSpanElement>("span.text-fragment"));
      const matches = findSnippetMatches(spans, snippet);
      onMatchCount?.(matches.length);

      for (const span of spans) {
        span.classList.remove("active-highlight", "matched-highlight");
      }
      if (!matches.length) return;

      for (const match of matches) {
        for (const idx of match) {
          spans[idx]?.classList.add("matched-highlight");
        }
      }
      const active = matches[((matchIndex % matches.length) + matches.length) % matches.length];
      for (const idx of active) {
        spans[idx]?.classList.add("active-highlight");
      }
      const anchor = spans[active[0]];
      anchor?.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    }
    renderPage().catch((error) => {
      setLoadError(error instanceof Error ? error.message : "Falha na renderizacao da pagina.");
    });
    return () => {
      cancelled = true;
    };
  }, [pdfDoc, boundedPage, snippet, matchIndex, onMatchCount]);

  useEffect(() => {
    setCurrentPage(boundedPage);
  }, [boundedPage]);

  return (
    <div className="pdf-viewer">
      <div className="pdf-status">
        {loading && <span>Carregando PDF...</span>}
        {!loading && pdfDoc && <span>Pagina {currentPage} de {pdfDoc.numPages}</span>}
        {loadError && <span className="error-text">{loadError}</span>}
      </div>
      <div className="pdf-stage">
        <canvas ref={canvasRef} />
        <div ref={textLayerRef} className="pdf-text-layer" />
      </div>
    </div>
  );
}
