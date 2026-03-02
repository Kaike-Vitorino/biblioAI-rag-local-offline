import type { Citation, SourceUsed } from "../types";

type SourceLike = Citation | SourceUsed;

type SourceChipProps = {
  source: SourceLike;
  onClick: (source: SourceLike) => void;
};

export default function SourceChip({ source, onClick }: SourceChipProps) {
  return (
    <button
      type="button"
      className="source-chip"
      onClick={() => onClick(source)}
      title={`Abrir fonte ${source.file_name} página ${source.page_start}`}
    >
      {source.file_name} · p. {source.page_start}
    </button>
  );
}

export type { SourceLike };

