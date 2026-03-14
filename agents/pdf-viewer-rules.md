# PDF Viewer Rules

Este documento define as regras obrigatórias para o PDF Viewer do sistema.

O viewer é uma das partes mais críticas do projeto porque ele conecta:

- busca semântica
- citações
- evidências
- leitura humana

Se o viewer estiver ruim, todo o sistema perde credibilidade.

---

# OBJETIVOS DO PDF VIEWER

O viewer deve permitir:

- leitura confortável
- localização clara de evidências
- navegação rápida
- confiança nas citações

O usuário precisa olhar para a página e entender rapidamente **onde está o trecho citado**.

---

# POSICIONAMENTO DA PÁGINA

A página do documento deve:

- ficar centralizada
- ocupar espaço adequado da tela
- não parecer pequena dentro de um container gigante
- escalar bem em telas grandes

Evitar:

- página minúscula
- excesso de espaço vazio
- layout que parece "quebrado"

---

# MODO LEITURA

O sistema deve possuir um **modo leitura expandido**.

Quando ativado:

- a página deve ocupar quase toda a tela
- distrações da interface devem ser minimizadas
- foco deve ser na leitura do documento

O modo leitura deve:

- manter navegação
- manter zoom
- manter highlights

---

# HIGHLIGHTS

Os highlights indicam a evidência usada pela resposta.

Regras obrigatórias:

- highlight deve ficar **dentro da página**
- highlight deve estar **alinhado ao texto**
- highlight não deve sair da área do PDF
- highlight deve ser claramente visível

---

# COR DO HIGHLIGHT

A cor deve:

- ter bom contraste
- não apagar o texto
- não cansar os olhos

Evitar:

- amarelo fraco
- laranja saturado
- cores transparentes demais

Boa referência:

- amarelo suave
- dourado claro
- highlight tipo "marcador de texto real"

---

# PRECISÃO DO HIGHLIGHT

O highlight deve apontar para:

- o bloco correto
- a linha correta
- idealmente o trecho correto

Nunca destacar:

- metade da página
- área aleatória
- posição desalinhada

---

# SELEÇÃO DE TEXTO

O usuário deve conseguir:

- selecionar texto
- copiar texto
- usar Ctrl+C / Cmd+C

Evitar renderização que destrua seleção.

Se necessário:

- usar text layer do PDF
- evitar canvas puro sem camada de texto.

---

# NAVEGAÇÃO

O viewer deve permitir:

- próxima página
- página anterior
- pular para página citada
- navegar entre matches

Controles devem ser claros.

---

# ZOOM

Zoom deve:

- funcionar suavemente
- manter alinhamento do highlight
- não quebrar layout

---

# TESTES OBRIGATÓRIOS

Antes de aceitar mudanças no viewer:

- abrir documento
- navegar páginas
- abrir página citada
- aplicar zoom
- verificar highlight
- selecionar texto
- copiar texto
- testar em tela pequena

---

# REGRA FINAL

Se o usuário não conseguir:

- ler confortavelmente
- identificar a evidência
- navegar facilmente

então o viewer ainda não está correto.