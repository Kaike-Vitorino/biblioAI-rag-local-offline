# Agents Knowledge Base

Esta pasta e a base operacional para qualquer agente ou desenvolvedor que altere o projeto.

## Ordem de leitura recomendada

1. `rag-architecture.md`
2. `rag-citation-validation.md`
3. `pdf-viewer-rules.md`
4. `ui-visual-review.md`
5. `ui-audit-agent.md`

## Quando consultar

- Antes de alterar `retrieval`, `chat`, `citacoes` ou `highlight`
- Antes de alterar `PDF Viewer` ou estilos globais de UI
- Antes de aprovar mudancas de UX sem revisao visual real

## Regras operacionais

- Nao aceitar alteracao visual sem validacao pratica
- Preservar rastreabilidade das evidencias
- Nao quebrar selecao/copia de texto do PDF
- Priorizar legibilidade, contraste e uso de viewport
- Em caso de duvida, escolher confiabilidade acima de efeito visual

## Resultado esperado

Qualquer novo agente deve conseguir entender rapidamente:

- como o RAG funciona neste sistema
- como validar referencias antes da resposta final
- como manter o viewer confiavel e legivel
