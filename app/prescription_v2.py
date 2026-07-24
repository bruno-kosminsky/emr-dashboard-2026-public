"""Régua mensal oficial do dashboard, baseada nos documentos v2.

Fontes:
- contextos_v2/prescricao_excelencia_v2.md
- contextos_v2/prescricao_proficiencia_v2.md
- contextos_v2/decomposicao_semanal_v2.md

Os volumes de Excelência representam o Perfil A/base alta. O Perfil B não tem
uma tabela mensal coerente nos documentos atuais e, por isso, não é inferido.
"""

PRESCRIPTION_VERSION = "v2"
EXCEL_VOLUME_PROFILE = "Perfil A/base alta"

PRESCRIPTION_MONTHLY = {
    "2026-01": {"label": "jan/26", "prof_min": 51, "prof_max": 55, "excel_min": 55, "excel_max": 62},
    "2026-02": {"label": "fev/26", "prof_min": 52, "prof_max": 57, "excel_min": 57, "excel_max": 64},
    "2026-03": {"label": "mar/26", "prof_min": 53, "prof_max": 59, "excel_min": 59, "excel_max": 66},
    "2026-04": {"label": "abr/26", "prof_min": 53, "prof_max": 61, "excel_min": 61, "excel_max": 68},
    "2026-05": {"label": "mai/26", "prof_min": 55, "prof_max": 63, "excel_min": 63, "excel_max": 70},
    "2026-06": {"label": "jun/26", "prof_min": 57.5, "prof_max": 65.5, "excel_min": 65.5, "excel_max": 72.5},
    "2026-07": {"label": "jul/26", "prof_min": 60, "prof_max": 68, "excel_min": 68, "excel_max": 75},
    "2026-08": {"label": "ago/26", "prof_min": 62.5, "prof_max": 70.5, "excel_min": 70.5, "excel_max": 77.5},
    "2026-09": {"label": "set/26", "prof_min": 65, "prof_max": 73, "excel_min": 73, "excel_max": 80},
}
PRESCRIPTION_ORDER = list(PRESCRIPTION_MONTHLY)

# Tuplas: (Proficiência, Excelência Perfil A/base alta).
# A v2 não define metas mensais de dias ativos. O agregado short_fmt também não
# é meta oficial v2; os documentos o substituem pelos subtipos de simulado.
PRESCRIPTION_TARGETS = {
    "2026-01": {"questoes": (155, 265), "flashcards": (30, 50), "blocos": (30, 30)},
    "2026-02": {"questoes": (280, 500), "flashcards": (60, 100), "blocos": (60, 60)},
    "2026-03": {"questoes": (280, 500), "flashcards": (60, 100), "blocos": (60, 60)},
    "2026-04": {"questoes": (345, 570), "flashcards": (60, 100), "blocos": (60, 60)},
    "2026-05": {"questoes": (425, 650), "flashcards": (60, 100), "blocos": (60, 60)},
    "2026-06": {"questoes": (440, 680), "flashcards": (60, 100), "blocos": (60, 60)},
    "2026-07": {"questoes": (490, 780), "flashcards": (60, 100), "blocos": (60, 60)},
    "2026-08": {"questoes": (565, 930), "flashcards": (60, 100), "blocos": (60, 60)},
    "2026-09": {"questoes": (295, 500), "flashcards": (30, 50), "blocos": (30, 30)},
}
