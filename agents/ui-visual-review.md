# UI Visual Review Protocol

Este documento define como agentes devem **avaliar visualmente a interface do sistema** antes de considerar uma implementação de UI como aceitável.

Este projeto não aceita mudanças de UI baseadas apenas em código ou suposições.  
Toda alteração visual deve passar por **observação real da interface renderizada**.

O objetivo é garantir:

- boa legibilidade
- layout equilibrado
- uso eficiente do espaço
- contraste adequado
- experiência de leitura confortável
- comportamento consistente em diferentes telas

---

# PRINCÍPIO FUNDAMENTAL

Uma interface **não é considerada correta até ser observada visualmente**.

Sempre que um agente modificar:

- layout
- viewer
- highlights
- spacing
- cores
- tipografia
- painéis
- controles

ele deve **rodar a aplicação e inspecionar a interface renderizada**.

---

# FLUXO OBRIGATÓRIO DE AVALIAÇÃO DE UI

Sempre seguir este fluxo:

1. Rodar a aplicação localmente
2. Abrir a interface no navegador
3. Navegar pelos fluxos principais
4. Observar a UI como um usuário real
5. Tirar screenshots mentais ou reais
6. Avaliar com os critérios deste documento
7. Corrigir problemas detectados
8. Validar novamente

Nunca assumir que a UI está boa apenas porque:

- o CSS compila
- os componentes renderizam
- o layout parece correto no código

---

# ÁREAS QUE DEVEM SER INSPECIONADAS

Sempre avaliar:

## 1. Hierarquia visual

Perguntas obrigatórias:

- O que chama atenção primeiro?
- O conteúdo principal está claro?
- Existe excesso de elementos competindo pela atenção?
- O usuário entende rapidamente o que é importante?

Problemas comuns:

- excesso de botões
- texto pequeno
- elementos com mesma prioridade visual
- ausência de foco no conteúdo

---

## 2. Uso do espaço

A interface deve usar o espaço de forma eficiente.

Verificar:

- grandes áreas vazias
- conteúdo espremido
- elementos desalinhados
- layout quebrado em telas menores

Problemas comuns:

- página de documento pequena dentro de área enorme
- painéis laterais gigantes
- conteúdo central muito estreito
- margem exagerada

---

## 3. Legibilidade

Texto deve ser confortável de ler.

Avaliar:

- tamanho da fonte
- contraste
- espaçamento entre linhas
- largura da coluna de leitura

Evitar:

- texto pequeno
- cores fracas
- linhas muito longas
- texto espremido

---

## 4. Contraste

Elementos importantes precisam ser visíveis.

Verificar:

- botões visíveis
- highlights visíveis
- texto com contraste suficiente
- elementos interativos distinguíveis

Evitar:

- cinza claro em fundo claro
- highlights apagados
- texto difícil de ler

---

## 5. Alinhamento

A interface deve parecer organizada.

Verificar:

- alinhamento de botões
- consistência de margens
- consistência de padding
- alinhamento vertical e horizontal

Problemas comuns:

- botões desalinhados
- elementos flutuando
- padding inconsistente

---

# REGRAS ESPECÍFICAS PARA O PDF VIEWER

O PDF Viewer é uma das partes mais importantes do sistema.

Sempre verificar:

## Página do documento

A página deve:

- ficar bem centralizada
- ocupar espaço adequado
- não parecer perdida na tela

Evitar:

- página pequena no meio de área enorme
- zoom inadequado
- layout que desperdiça espaço

---

## Destaques (highlights)

Os highlights devem:

- ficar dentro da página
- ser visíveis
- não esconder o texto
- indicar claramente o trecho citado

Evitar:

- highlight vazando da página
- cor fraca demais
- highlight mal alinhado
- overlay desalinhado

---

## Experiência de leitura

O viewer deve permitir leitura confortável.

Verificar:

- zoom funcionando
- navegação de páginas clara
- modo expandido funcionando
- seleção de texto funcionando

---

# TESTES OBRIGATÓRIOS DO VIEWER

Sempre testar:

1. abrir documento
2. abrir página citada
3. navegar entre páginas
4. aplicar zoom
5. selecionar texto
6. copiar texto
7. visualizar highlight
8. mudar tamanho da janela
9. testar em tela menor

---

# CHECKLIST DE QUALIDADE VISUAL

Antes de considerar a UI aceitável, verificar:

- [ ] layout equilibrado
- [ ] página do documento bem posicionada
- [ ] highlights visíveis e alinhados
- [ ] contraste adequado
- [ ] tipografia legível
- [ ] controles claros
- [ ] uso eficiente do espaço
- [ ] experiência de leitura confortável

Se qualquer item falhar, a UI deve ser ajustada.

---

# FILOSOFIA DE UI DO PROJETO

Este projeto prioriza:

- clareza
- legibilidade
- simplicidade
- foco no conteúdo
- experiência de leitura

Não priorizamos:

- efeitos visuais desnecessários
- animações exageradas
- layouts complexos
- UI decorativa

---

# REGRA FINAL

Se a interface:

- parece improvisada
- dificulta leitura
- desperdiça espaço
- confunde o usuário

então **ela não está pronta**.

A UI deve ser tratada como parte essencial do sistema, não como detalhe superficial.