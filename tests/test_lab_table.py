import json
import os
import re
import unicodedata

from lab_table import partes, vineta_telegram

AQUI = os.path.dirname(os.path.abspath(__file__))


def _resumen(texto):
    return [[p["tipo"]] + ([[[texto[f["etiqueta"][0]:f["etiqueta"][1]], texto[f["valor"][0]:f["valor"][1]]] if f["tipo"] == "fila" else texto[f["texto"][0]:f["texto"][1]] for f in p["filas"]]] if p["tipo"] == "tabla" else []) for p in partes(texto)]


def _norm(t):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", t or "")).strip()


def test_casos_compartidos():
    for c in json.load(open(os.path.join(AQUI, "labs_tabla_casos.json"), encoding="utf-8")):
        assert _resumen(c["texto"]) == c["esperado"], c["nombre"]


def test_sin_tabla_sale_igual():
    t = "La hemoglobina es de 11 g/dL y la creatinina de 0,9 mg/dL."
    assert vineta_telegram(t) == (t, None)


def test_texto_normalizado_no_cambia():
    # el mensaje con <pre> debe leerse, sin etiquetas, igual que la viñeta (lo usa _reconstruir)
    t = "Estudios:\n\nHb 11,2 g/dL\nLeucocitos 8.500/mm³\nOrina\nProteínas 4+\nSangre negativa\n\n¿Cuál?"
    msg, modo = vineta_telegram(t)
    assert modo == "HTML" and "<pre>" in msg
    import html
    plano = html.unescape(re.sub(r"</?pre>", "", msg))
    assert _norm(plano) == _norm(t)
