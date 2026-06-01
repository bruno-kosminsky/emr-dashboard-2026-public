# Dashboard EMR — R1 2026 (público)

Dashboard de acompanhamento das métricas de uso da plataforma EMR pelos alunos
do Extensivo R1 2026 (B2C) e parceiros B2B (Inspirali e demais).

App público em Streamlit. Os dados (`snapshots/*.parquet`) são **agregados e
anonimizados** — não contêm nome nem e-mail de alunos, apenas `account_id`
(identificador interno) e métricas. Credenciais de banco vivem nos Secrets do
Streamlit Cloud, nunca no repositório.

## Rodar local
```
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

A aba B2B (simulados ENAMED) consulta o Aurora em runtime; sem as credenciais
em `.streamlit/secrets.toml` (ou variáveis de ambiente), ela cai para um fallback.
