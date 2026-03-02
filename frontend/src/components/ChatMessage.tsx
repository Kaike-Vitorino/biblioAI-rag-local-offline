import SourceChip, { type SourceLike } from "./SourceChip";
import type { ChatResponse, Citation, SourceUsed } from "../types";

type ChatMessageProps = {
  response: ChatResponse;
  onOpenSource: (source: SourceLike) => void;
};

function dedupeQuotes(citations: Citation[]): Citation[] {
  const seen = new Set<string>();
  const result: Citation[] = [];
  for (const citation of citations) {
    const key = `${citation.source_id}::${citation.quote}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(citation);
  }
  return result;
}

function dedupeSources(sources: SourceUsed[]): SourceUsed[] {
  const seen = new Set<string>();
  const result: SourceUsed[] = [];
  for (const source of sources) {
    const key = source.source_id;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(source);
  }
  return result;
}

function buildResponseCopyText(response: ChatResponse): string {
  if (response.not_found) {
    const terms = response.searched_terms?.join(", ") || "-";
    return `Nao encontrado nos documentos.\nTermos pesquisados: ${terms}`;
  }

  const sections: string[] = [];
  sections.push(`Synopsis:\n${response.synopsis || "-"}`);
  if (response.key_points.length) {
    sections.push(`Key Points:\n${response.key_points.map((item) => `- ${item}`).join("\n")}`);
  }
  if (response.suggested_qa.length) {
    sections.push(
      `Q&A Suggestions:\n${response.suggested_qa
        .map((item) => `Q: ${item.question}\nA: ${item.answer}`)
        .join("\n\n")}`
    );
  }
  if (response.claims.length) {
    sections.push(`Claims:\n${response.claims.map((claim) => `- ${claim.text}`).join("\n")}`);
  }
  return sections.join("\n\n");
}

function buildCitationsCopyText(response: ChatResponse): string {
  const citations = dedupeQuotes(response.claims.flatMap((claim) => claim.citations));
  if (!citations.length) return "Sem citacoes disponiveis.";
  return citations
    .map((citation) => `${citation.file_name} p.${citation.page_start}: ${citation.quote}`)
    .join("\n\n");
}

async function copyToClipboard(text: string) {
  if (!navigator.clipboard?.writeText) {
    throw new Error("Clipboard nao suportado neste navegador.");
  }
  await navigator.clipboard.writeText(text);
}

export default function ChatMessage({ response, onOpenSource }: ChatMessageProps) {
  const allCitations = response.claims.flatMap((claim) => claim.citations);
  const directQuotes = dedupeQuotes(allCitations).filter((citation) => citation.quote.trim().length > 0);
  const allReferences = dedupeSources((response.all_references ?? response.sources) as SourceUsed[]);
  const allReferenceDocs = new Set(allReferences.map((item) => item.doc_id)).size;

  return (
    <div className={`assistant-card ${response.not_found ? "assistant-card-not-found" : ""}`}>
      <div className="assistant-actions">
        <button
          type="button"
          className="small-action"
          onClick={() => {
            void copyToClipboard(buildResponseCopyText(response));
          }}
        >
          Copiar resposta
        </button>
        <button
          type="button"
          className="small-action"
          onClick={() => {
            void copyToClipboard(buildCitationsCopyText(response));
          }}
        >
          Copiar citacoes
        </button>
      </div>

      {response.not_found ? (
        <section className="not-found-box">
          <h3>Nao encontrado nos documentos</h3>
          <p>{response.message || "Nao encontramos evidencia suficiente para responder com citacoes validas."}</p>
          {!!response.searched_terms?.length && (
            <p className="searched-terms">
              <strong>Termos pesquisados:</strong> {response.searched_terms.join(", ")}
            </p>
          )}
        </section>
      ) : (
        <>
          <section>
            <h3>Synopsis</h3>
            <p>{response.synopsis || "Sem sinopse retornada."}</p>
          </section>

          <section>
            <h3>Key Points</h3>
            {response.key_points.length ? (
              <ul className="flat-list">
                {response.key_points.map((point, index) => (
                  <li key={`${point}-${index}`}>{point}</li>
                ))}
              </ul>
            ) : (
              <p>Nenhum ponto-chave.</p>
            )}
          </section>

          <section>
            <h3>Q&A Suggestions</h3>
            {response.suggested_qa.length ? (
              <div className="qa-list">
                {response.suggested_qa.map((item, index) => (
                  <article key={`${item.question}-${index}`} className="qa-item">
                    <p className="qa-question">Q: {item.question}</p>
                    <p className="qa-answer">A: {item.answer}</p>
                    <div className="chips-row">
                      {item.citations.map((citation) => (
                        <SourceChip key={`${citation.source_id}-${citation.quote}`} source={citation} onClick={onOpenSource} />
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p>Sem sugestoes de Q&A.</p>
            )}
          </section>

          <section>
            <h3>Direct Quotes</h3>
            {directQuotes.length ? (
              <div className="quotes-list">
                {directQuotes.map((citation, index) => (
                  <article key={`${citation.source_id}-${index}`} className="quote-item">
                    <blockquote>{citation.quote}</blockquote>
                    <div className="chips-row">
                      <SourceChip source={citation} onClick={onOpenSource} />
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p>Sem citacoes diretas disponiveis.</p>
            )}
          </section>

          <section>
            <h3>Claims</h3>
            {response.claims.length ? (
              <div className="claims-list">
                {response.claims.map((claim) => (
                  <article key={claim.claim_id || claim.text} className="claim-item">
                    <p>{claim.text}</p>
                    <div className="chips-row">
                      {claim.citations.map((citation) => (
                        <SourceChip key={`${claim.claim_id}-${citation.source_id}`} source={citation} onClick={onOpenSource} />
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p>Sem claims estruturadas.</p>
            )}
          </section>

          <section>
            <h3>Fontes usadas</h3>
            {response.sources.length ? (
              <div className="chips-row">
                {response.sources.map((source) => (
                  <SourceChip key={source.source_id} source={source} onClick={onOpenSource} />
                ))}
              </div>
            ) : (
              <p>Nenhuma fonte utilizada.</p>
            )}
          </section>

          <section>
            <h3>Todas as referencias encontradas</h3>
            {allReferences.length ? (
              <>
                <p className="meta-line">
                  {allReferences.length} referencias em {allReferenceDocs} material(is).
                </p>
                <div className="chips-row">
                  {allReferences.map((source) => (
                    <SourceChip key={`all-${source.source_id}`} source={source} onClick={onOpenSource} />
                  ))}
                </div>
              </>
            ) : (
              <p>Nenhuma referencia adicional encontrada.</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
