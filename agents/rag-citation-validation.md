# RAG Citation Validation

Este documento define como o sistema deve validar citações antes de exibi-las.

O objetivo é evitar:

- citações irrelevantes
- páginas erradas
- evidências fracas
- perda de confiança no sistema

---

# PRINCÍPIO CENTRAL

A resposta da IA **não pode citar qualquer trecho recuperado**.

Cada citação deve passar por uma **validação final pela LLM**.

---

# PIPELINE DE CITAÇÕES

Fluxo ideal:

1. usuário envia pergunta
2. retrieval encontra chunks candidatos
3. reranking seleciona melhores
4. evidências são extraídas
5. LLM valida as evidências
6. apenas citações aprovadas são usadas

---

# VALIDAÇÃO DAS CITAÇÕES

Para cada citação candidata, a LLM deve verificar:

1. o trecho fala realmente sobre a pergunta?
2. o trecho contém informação relevante?
3. o trecho responde ou ajuda a responder?

---

# CITAÇÕES QUE DEVEM SER DESCARTADAS

A LLM deve rejeitar:

- trechos vagamente relacionados
- trechos fora de contexto
- citações irrelevantes
- texto que menciona o termo mas não responde a pergunta

---

# PRIORIDADE

É melhor retornar:

- poucas citações boas

do que:

- muitas citações ruins.

---

# EXEMPLO

Pergunta:

"Como o espiritismo define família?"

Trecho aceito:

trecho que explica conceito de família segundo espiritismo.

Trecho rejeitado:

texto que menciona família em outro contexto.

---

# REGRAS PARA O AGENTE

Antes de gerar a resposta final:

1. avaliar cada citação
2. remover citações fracas
3. manter apenas as evidências fortes
4. gerar resposta baseada nessas evidências

---

# REGRA CRÍTICA

Nunca inventar evidência.

Se não houver evidência suficiente:

- dizer que não encontrou informação clara.

---

# OBJETIVO FINAL

Garantir que o sistema funcione como:

uma ferramenta de pesquisa confiável.