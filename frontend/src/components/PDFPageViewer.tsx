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
  highlightQuery: string;
  matchIndex: number;
  onPageCount?: (pageCount: number) => void;
  onMatchCount?: (matchCount: number) => void;
  zoom?: number;
  fitWidth?: boolean;
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

function extractQueryTerms(query: string): string[] {
  const normalized = normalizeForMatch(query);
  if (!normalized) return [];
  const stopwords = new Set(["que", "como", "para", "isso", "essa", "esse", "tem", "mais", "sobre", "dos", "das", "uma", "umas", "uns", "por", "com", "sem", "ser", "foi", "sao", "só", "so", "ter", "ha", "há"]);
  const unique: string[] = [];
  const seen = new Set<string>();
  for (const term of normalized.split(" ")) {
    if (!term || term.length < 3 || stopwords.has(term) || seen.has(term)) continue;
    seen.add(term);
    unique.push(term);
  }
  return unique;
}

function findTermMatches(spans: HTMLSpanElement[], query: string): number[][] {
  const terms = extractQueryTerms(query);
  if (!terms.length) return [];
  const matches: number[][] = [];
  for (let i = 0; i < spans.length; i += 1) {
    const text = normalizeForMatch(spans[i].textContent ?? "");
    if (!text) continue;
    for (const term of terms) {
      if (text.includes(term)) {
        matches.push([i]);
        break;
      }
    }
  }
  return matches;
}

export default function PDFPageViewer({
  pdfUrl,
  pageNumber,
  snippet,
  highlightQuery,
  matchIndex,
  onPageCount,
  onMatchCount,
  zoom = 1.35,
  fitWidth = false
}: PDFPageViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [currentPage, setCurrentPage] = useState(pageNumber);
  const [transitionClass, setTransitionClass] = useState("");
  const prevPageRef = useRef(pageNumber);

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
      const direction = boundedPage >= prevPageRef.current ? "next" : "prev";
      setTransitionClass(direction === "next" ? "page-transition-next" : "page-transition-prev");
      const baseViewport = page.getViewport({ scale: 1 });
      let renderScale = zoom;
      if (fitWidth) {
        const stage = stageRef.current;
        const availableWidth = Math.max(200, (stage?.clientWidth ?? 0) - 24);
        if (availableWidth > 0 && baseViewport.width > 0) {
          renderScale = Math.max(0.55, Math.min(3.0, availableWidth / baseViewport.width));
        }
      }
      const viewport = page.getViewport({ scale: renderScale });
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
      const matches = findTermMatches(spans, highlightQuery || snippet);
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
      prevPageRef.current = boundedPage;
      window.setTimeout(() => {
        if (!cancelled) setTransitionClass("");
      }, 220);
    }
    renderPage().catch((error) => {
      setLoadError(error instanceof Error ? error.message : "Falha na renderizacao da pagina.");
    });
    return () => {
      cancelled = true;
    };
  }, [pdfDoc, boundedPage, snippet, highlightQuery, matchIndex, onMatchCount, zoom, fitWidth]);

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
      <div ref={stageRef} className={`pdf-stage ${transitionClass}`}>
        <div className="pdf-page-surface">
          <canvas ref={canvasRef} />
          <div ref={textLayerRef} className="pdf-text-layer" />
        </div>
      </div>
    </div>
  );
}
