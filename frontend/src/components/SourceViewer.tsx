import { useEffect, useState } from "react";
import { getPageText, getPdfUrl } from "../api";
import type { SourceSelection } from "../types";
import PDFPageViewer from "./PDFPageViewer";

type SourceViewerProps = {
  source: SourceSelection | null;
  onClose?: () => void;
};

export default function SourceViewer({ source, onClose }: SourceViewerProps) {
  const [pageNumber, setPageNumber] = useState<number>(1);
  const [matchIndex, setMatchIndex] = useState<number>(0);
  const [matchCount, setMatchCount] = useState<number>(0);
  const [pageCount, setPageCount] = useState<number>(0);
  const [textPageContent, setTextPageContent] = useState<string>("");
  const [textLoading, setTextLoading] = useState(false);
  const [textError, setTextError] = useState<string | null>(null);

  useEffect(() => {
    if (!source) return;
    setPageNumber(Math.max(1, source.pageNumber));
    setMatchIndex(0);
    setMatchCount(0);
  }, [source]);

  useEffect(() => {
    let cancelled = false;
    async function loadTextPage() {
      if (!source || source.isPdf) {
        setTextPageContent("");
        setTextError(null);
        setTextLoading(false);
        return;
      }
      setTextLoading(true);
      setTextError(null);
      try {
        const data = await getPageText(source.docId, pageNumber);
        if (cancelled) return;
        setTextPageContent(data.text || "");
      } catch (error) {
        if (cancelled) return;
        setTextError(error instanceof Error ? error.message : "Falha ao carregar texto da fonte.");
      } finally {
        if (!cancelled) setTextLoading(false);
      }
    }
    loadTextPage().catch(() => {
      if (!cancelled) {
        setTextError("Falha ao carregar texto da fonte.");
        setTextLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [source, pageNumber]);

  if (!source) {
    return (
      <aside className="source-panel empty">
        <h2>Fonte</h2>
        <p>Selecione uma citação para abrir o viewer da fonte.</p>
      </aside>
    );
  }

  const canGoPrevPage = pageNumber > 1;
  const canGoNextPage = source.isPdf ? (pageCount ? pageNumber < pageCount : true) : false;
  const canCycleMatch = source.isPdf && matchCount > 1;

  return (
    <aside className="source-panel">
      <div className="source-panel-header">
        <h2>Fonte</h2>
        {onClose ? (
          <button className="close-button" type="button" onClick={onClose}>
            Fechar
          </button>
        ) : null}
      </div>
      <p className="source-meta">
        <strong>{source.fileName}</strong> · p. {pageNumber}
      </p>
      <p className="source-meta">source_id: {source.sourceId}</p>
      <p className="source-meta">highlight: {source.method}</p>
      <div className="source-controls">
        <button type="button" disabled={!canGoPrevPage} onClick={() => setPageNumber((p) => Math.max(1, p - 1))}>
          Pagina anterior
        </button>
        <button type="button" disabled={!canGoNextPage} onClick={() => setPageNumber((p) => p + 1)}>
          Proxima pagina
        </button>
        <button type="button" disabled={!canCycleMatch} onClick={() => setMatchIndex((i) => i + 1)}>
          Proximo match
        </button>
      </div>
      <p className="source-snippet">
        <strong>Snippet:</strong> {source.snippet || "Nao informado"}
      </p>
      {source.isPdf ? (
        <PDFPageViewer
          pdfUrl={getPdfUrl(source.docId)}
          pageNumber={pageNumber}
          snippet={source.snippet}
          matchIndex={matchIndex}
          onPageCount={setPageCount}
          onMatchCount={setMatchCount}
        />
      ) : (
        <div className="text-source-panel">
          {textLoading ? <p>Carregando texto da fonte...</p> : null}
          {textError ? <p className="error-text">{textError}</p> : null}
          {!textLoading && !textError ? (
            <>
              <p className="source-meta">Documento de texto (sem viewer PDF).</p>
              <pre className="text-source-content">{textPageContent || "Sem conteudo textual nesta pagina."}</pre>
            </>
          ) : null}
        </div>
      )}
    </aside>
  );
}
