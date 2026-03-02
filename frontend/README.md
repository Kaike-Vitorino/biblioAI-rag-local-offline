# Frontend (React + Vite + PDF.js)

Este frontend e buildado em producao pelo `launcher.py` e servido pelo backend em `http://127.0.0.1:8000`.

## Uso recomendado

- Nao rode comandos manualmente.
- Use o launcher da raiz:
  - [launcher.py](/G:/ai/Spiritism_AK/launcher.py)

## Desenvolvimento local (opcional)

```bash
cd frontend
npm install
npm run dev
```

Se estiver rodando em modo dev com Vite (`5173`), configure:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

