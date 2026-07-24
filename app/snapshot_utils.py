"""Transformações pequenas aplicadas aos snapshots do dashboard."""

import pandas as pd


def activity_month_labels(data: pd.DataFrame) -> pd.Series:
    """Retorna o mês calendário real da atividade no formato ``YYYY-MM``.

    Snapshots novos preservam ``mes_ref`` antes da agregação semanal. O fallback
    por ``semana_iso`` mantém compatibilidade de leitura com snapshots antigos.
    """
    source = data["mes_ref"] if "mes_ref" in data.columns else data["semana_iso"]
    return pd.to_datetime(source, errors="coerce").dt.to_period("M").astype(str)


def eligible_cohort_for_month(cohort: pd.DataFrame, month: str) -> pd.DataFrame:
    """Exclui matrículas que ainda não haviam iniciado no mês analisado."""
    if cohort.empty or "first_start_date" not in cohort.columns:
        return cohort.copy()
    starts = pd.to_datetime(cohort["first_start_date"], errors="coerce")
    next_month = pd.Period(month, freq="M").to_timestamp(how="end") + pd.Timedelta(
        nanoseconds=1
    )
    return cohort[starts.isna() | starts.lt(next_month)].copy()


def weekly_student_ratio(
    data: pd.DataFrame,
    numerator: str,
    denominator: str,
) -> pd.DataFrame:
    """Calcula uma razão por aluno-semana sem duplicar semanas entre meses."""
    weekly = (
        data.groupby(["account_id", "semana_iso"], as_index=False)[
            [numerator, denominator]
        ]
        .sum()
    )
    weekly = weekly[weekly[denominator] > 0].copy()
    weekly["pct"] = weekly[numerator] / weekly[denominator] * 100
    return weekly


def deduplicate_accounts(data: pd.DataFrame) -> pd.DataFrame:
    """Retorna no máximo uma linha por aluno.

    Cohorts e métricas podem ter uma linha por aluno e turma. Quando disponível,
    a matrícula mais antiga é preservada de forma determinística.
    """
    if data.empty or "account_id" not in data.columns:
        return data
    sort_cols = ["account_id"]
    if "first_start_date" in data.columns:
        sort_cols.append("first_start_date")
    return (
        data.sort_values(sort_cols, kind="stable", na_position="last")
        .drop_duplicates("account_id", keep="first")
        .reset_index(drop=True)
    )


def deduplicate_account_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    """Compatibilidade semântica para o carregamento de métricas."""
    return deduplicate_accounts(metrics)


def detect_atypical_collection(
    data: pd.DataFrame, reference_month: pd.Timestamp
) -> tuple[bool, int, float] | None:
    """Compara o volume do mês com a mediana dos três meses anteriores.

    Retorna ``(atipico, n_referencia, mediana_anterior)`` quando há pelo menos
    dois meses anteriores com coleta. Zero no mês de referência é atípico.
    """
    if data.empty:
        return None
    months = pd.to_datetime(data["mes"])
    counts = months.dt.to_period("M").value_counts()
    reference_period = reference_month.to_period("M")
    reference_count = int(counts.get(reference_period, 0))
    previous_counts = [counts.get(reference_period - offset, 0) for offset in (1, 2, 3)]
    previous_present = [count for count in previous_counts if count > 0]
    if len(previous_present) < 2:
        return None
    previous_median = float(pd.Series(previous_present).median())
    if previous_median <= 0:
        return None
    return (
        reference_count < previous_median * 0.4,
        reference_count,
        previous_median,
    )
