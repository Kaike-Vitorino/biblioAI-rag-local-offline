# BibliIA RAG Local (Offline, One-Click)

Sistema local de RAG (Retrieval-Augmented Generation) para consulta de documentos com citacoes rastreaveis.
Foi pensado para rodar em casa/escritorio sem depender de servicos cloud na execucao diaria.

## O que este projeto faz

- Chat estilo ChatGPT em navegador.
- Busca hibrida (lexical + vetorial) em PDFs/TXT/MD locais.
- Respostas com citacoes clicaveis e viewer da fonte.
- Upload de arquivos pela UI.
- Ingestao automatica para novos documentos.
- Launcher Windows "one-click" (`launcher.py`) para usuarios leigos.

## Conceitos rapidos

- `RAG`: tecnica onde o LLM responde usando evidencias recuperadas dos seus arquivos.
- `Chunk`: bloco de texto (pedaco do documento) usado para indexacao e recuperacao.
- `Busca hibrida`: combina FTS5 (palavras-chave) + FAISS (similaridade semantica).
- `Citacao rastreavel`: cada afirmacao pode apontar para arquivo/pagina/trecho.

## Stack

- Backend: FastAPI + SQLite (FTS5) + FAISS + PyMuPDF.
- Frontend: React + Vite + TypeScript + PDF.js.
- LLM runtime local: Ollama.
- Modelo de chat padrao: `qwen3:8b`.
- Modelo de embeddings padrao: `nomic-embed-text`.

## Requisitos

- Windows 10/11.
- Python 3.11+.
- Node.js LTS (o launcher tenta instalar se faltar).
- Ollama (o launcher tenta instalar se faltar).
- Internet apenas para instalacao inicial e `ollama pull`.

## Como rodar (modo leigo: 1 clique)

1. Coloque seus arquivos em `docs/` (opcional; tambem pode enviar pela UI).
2. Execute `launcher.py` (duplo clique) ou `launcher.exe` se empacotado.
3. Aguarde: o launcher instala/verifica dependencias, sobe backend e abre o navegador.

URL principal:

- `http://127.0.0.1:8000`

Em rede local (LAN), o launcher tambem mostra o link `http://<IP_LOCAL>:8000`.

## Como enviar arquivos pela UI

1. Abra a sidebar.
2. Clique em `Adicionar arquivos`.
3. Selecione `.pdf`, `.txt` ou `.md`.
4. Aguarde o status: envio -> ingestao -> pronto.

Regras de upload (MVP):

- Extensoes aceitas: `.pdf`, `.txt`, `.md`.
- Limite por arquivo: `UPLOAD_MAX_MB` (padrao 50 MB).
- Nome sanitizado no servidor (sem path traversal).
- Se ja existir, cria sufixo: `arquivo (1).pdf`, etc.

## Fluxo interno resumido

1. Ingestao:
   - extrai texto por pagina (PDF)
   - cria chunks com overlap
   - grava metadados em SQLite
   - indexa em FTS5 e FAISS
2. Consulta:
   - expande termos de busca
   - recupera candidatos (lexical + vetorial)
   - reranqueia e seleciona evidencias
3. Geracao:
   - chama Ollama
   - valida citacoes
4. UI:
   - renderiza resposta e chips de fonte
   - abre viewer com pagina e highlight

## Variaveis de ambiente principais

Arquivo: `.env` (gerado automaticamente se nao existir)

- `MODEL=qwen3:8b`
- `EMBED_MODEL=nomic-embed-text`
- `DOCS_DIR=docs`
- `BACKEND_HOST=0.0.0.0`
- `BACKEND_PORT=8000`
- `TOPK=12`
- `FINAL_CHUNKS=10`
- `CHUNK_SIZE=900`
- `OVERLAP=120`
- `TEMPERATURE=0.1`
- `NUM_CTX=8192`

## API principal

- `GET /health`
- `POST /ingest`
- `GET /ingest/status`
- `GET /ingest/{job_id}`
- `POST /chat`
- `GET /chats`
- `POST /chats`
- `PATCH /chats/{id}`
- `DELETE /chats/{id}`
- `GET /chats/{id}/messages`
- `POST /chats/{id}/messages`
- `GET /docs`
- `GET /docs/{doc_id}/pdf`
- `GET /docs/{doc_id}/page/{page_number}/text`
- `GET /highlights/{source_id}`
- `POST /upload`

## Desenvolvimento rapido

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

## Empacotar launcher (opcional)

```powershell
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile launcher.py
```

Saida esperada: `dist/launcher.exe`.

