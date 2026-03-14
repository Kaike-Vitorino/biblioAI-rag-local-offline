# RAG Architecture

Este documento define a arquitetura desejada para o sistema de RAG local/offline deste projeto.

O objetivo é garantir que o sistema seja:

- confiável
- rastreável
- preciso
- útil para pesquisa real
- robusto para documentos PDF

Este projeto não deve se comportar como um chatbot genérico solto.  
Ele deve funcionar como uma ferramenta séria de pesquisa assistida por IA.

---

# OBJETIVOS PRINCIPAIS

O sistema deve permitir:

- encontrar evidências relevantes
- responder com base em fontes reais
- mostrar de onde veio cada resposta
- abrir o documento na página correta
- destacar o trecho correto
- permitir exploração e estudo do material

---

# PILARES DA ARQUITETURA

A arquitetura deve se apoiar em 6 pilares:

1. ingestão estruturada
2. busca híbrida
3. reranking
4. validação de evidências
5. geração assistida com rastreabilidade
6. visualização confiável no PDF Viewer

---

# 1. INGESTÃO ESTRUTURADA

PDF não deve ser tratado apenas como arquivo visual.

O sistema deve separar:

- PDF original = fonte visual canônica
- estrutura textual = base para indexação e busca

Formato desejado por documento:

- original.pdf
- parsed.struct.json
- parsed.md (opcional)
- chunks.jsonl

---

# ESTRUTURA INTERNA MÍNIMA

Cada documento deve preservar, quando possível:

- doc_id
- páginas
- blocos
- linhas ou spans
- texto original
- texto normalizado
- bounding boxes
- source_type (native ou ocr)

---

# CHUNKS

Cada chunk deve conter metadados claros.

Campos desejados:

- chunk_id
- doc_id
- page_start
- page_end
- block_refs
- text
- normalized_text
- source_type

Chunks nunca devem perder o vínculo com sua origem.

---

# 2. BUSCA HÍBRIDA

A busca deve combinar:

- busca lexical
- busca exata/literal
- busca semântica

Não confiar apenas em embeddings.

---

# BUSCA LEXICAL

A busca lexical deve:

- respeitar todos os termos da consulta
- funcionar bem para uma ou mais palavras
- suportar frases curtas
- permitir aderência textual real

Exemplo:

Consulta:
"Marte Venus"

O sistema deve considerar ambos os termos.

Nunca reduzir a consulta só ao primeiro termo.

---

# BUSCA EXATA

Quando o usuário quer localizar expressão específica, o sistema deve priorizar aderência literal.

Exemplos:

- "energia cinética"
- "habitantes de Saturno"
- "família segundo o espiritismo"

---

# BUSCA SEMÂNTICA

A busca semântica deve complementar, não substituir, a consulta original.

Ela pode ajudar com:

- termos relacionados
- variações úteis
- ambiguidade leve

Ela não pode:

- apagar os termos do usuário
- reescrever a intenção de forma agressiva
- inventar contexto

---

# 3. RERANKING

Após recuperar candidatos, o sistema deve reranquear.

Critérios desejados:

- aderência à query original
- presença literal de termos importantes
- proximidade semântica
- qualidade do trecho
- utilidade para resposta

Reranking não deve priorizar apenas trechos vagamente parecidos.

---

# 4. VALIDAÇÃO DE EVIDÊNCIAS

Antes da resposta final, as evidências devem ser validadas.

A LLM deve revisar cada citação candidata e responder:

- esse trecho realmente fala sobre o que foi perguntado?
- esse trecho ajuda a responder?
- essa evidência é forte ou fraca?

Trechos fracos devem ser descartados.

---

# REGRA CRÍTICA

É melhor usar:

- 2 evidências boas

do que:

- 8 evidências ruins

---

# 5. GERAÇÃO ASSISTIDA COM RASTREABILIDADE

A LLM pode:

- sintetizar
- explicar
- resumir
- comparar
- criar perguntas
- gerar material de estudo

Mas sempre com base em evidências aprovadas.

A resposta final deve:

- manter aderência às fontes
- evitar alucinação
- citar evidências reais

---

# MODO PESQUISA ÚNICA

Neste modo:

- cada consulta = um chat
- sem contexto acumulado
- foco em resposta pontual e objetiva

---

# MODO CHAT COM MEMÓRIA

Neste modo:

- histórico influencia resposta
- usuário pode pedir refinamento
- usuário pode pedir transformação do conteúdo
- ainda deve haver vínculo com fontes reais

---

# 6. VISUALIZAÇÃO NO PDF VIEWER

O PDF Viewer deve permitir:

- abrir página correta
- destacar evidência correta
- navegar
- usar zoom
- selecionar texto
- copiar texto
- ler confortavelmente

Se o viewer falha, a confiança do RAG cai.

---

# HIGHLIGHTS

Highlights devem ser:

- precisos
- visíveis
- contidos na página
- coerentes com a evidência citada

Evitar:

- highlight vazando
- bloco errado
- área aleatória
- cor ruim

---

# OCR HÍBRIDO

OCR deve ser fallback, não padrão universal.

Fluxo desejado:

1. tentar extração nativa
2. se página vier vazia ou ruim, usar OCR naquela página
3. registrar páginas OCR

---

# PRINCÍPIOS GERAIS

O sistema deve sempre priorizar:

- precisão
- rastreabilidade
- aderência textual
- legibilidade
- previsibilidade
- confiança

O sistema não deve:

- inventar evidências
- ignorar parte da query
- mostrar citações irrelevantes
- tratar PDF só como imagem
- sacrificar UX de leitura