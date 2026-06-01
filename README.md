# Dashboard EMR — R1 2026 (público)

Dashboard de acompanhamento das métricas de uso da plataforma EMR pelos alunos
do Extensivo R1 2026 (B2C) e parceiros B2B (Inspirali e demais).

App público em Streamlit. **Não conecta a nenhum banco em runtime**: todos os
dados vêm de `snapshots/*.parquet`, agregados e anonimizados (sem nome/e-mail —
apenas `account_id` interno e métricas), atualizados 1x/dia pelo ETL.

## Rodar local
```
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

Sem credenciais, sem secrets. Se um parquet faltar, a aba correspondente avisa e
o resto do app segue funcionando.
