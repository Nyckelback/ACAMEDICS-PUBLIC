"""Pruebas del parser de /caso: que la viñeta no se parta en opciones falsas (21-oct-2026).

Casos reales que salieron mal en el canal:
  #139 (11-may-2026): «38,9 °C.» y «grupo A.» se tomaron como opciones C y A; la pregunta
        quedó como opción A y la encuesta la daba por correcta.
  #1   (5-mar-2026):  «B.)» (escrito así para que Telegram no lo vuelva 😎) no se reconocía
        y la opción B quedó pegada a la A.
Se corre con:
    python -m tests.test_case_parser      (desde la raíz del repo)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case_parser import parse_case  # noqa: E402

CASO_139 = """Mujer de 26 años, docente, consulta a primer nivel por dolor faríngeo de 36 horas y fiebre de 38,9 °C. Niega tos, rinorrea y conjuntivitis. Varias compañeras de trabajo han presentado cuadros similares en las últimas semanas. Sin alergias conocidas. Al examen físico, faringe eritematosa con exudado amigdalino bilateral y adenopatías cervicales anteriores dolorosas. La sede no dispone de prueba rápida para estreptococo del grupo A. ¿Cuál es la conducta inicial más apropiada?

A. Penicilina V 500 mg VO por 10 días
B. Acetaminofén e ibuprofeno con reevaluación
C. Penicilina benzatínica 2 400 000 UI IM dosis única
D. Azitromicina 500 mg VO por 5 días

RESPUESTA CORRECTA: A. Penicilina V 500 mg VO por 10 días.

La escala de Centor estima la probabilidad de faringoamigdalitis estreptocócica en adultos.

TIP ACAMÉDICO: Con criterios de Centor altos y sin prueba rápida, suele preferirse penicilina V o amoxicilina.

BIBLIOGRAFÍA

1. National Institute for Health and Care Excellence. Sore throat (acute): antimicrobial prescribing. NICE guideline NG84. London; 2018."""

CASO_1 = """Lactante de 5 meses, alimentado con lactancia materna exclusiva, es llevado a consulta de crecimiento y desarrollo. P/L de -2.3 DE. Al examen físico se encuentra alerta, activo, bien hidratado, sin edemas y con adecuada succión al seno materno. ¿Cuál es la conducta inicial más adecuada?
A) Hospitalizar
B.) Control ambulatorio en 15 días
C) Manejo ambulatorio con FTLC
D) Manejo ambulatorio con fórmula láctea de inicio
CORRECTA: A
Todo niño menor de 6 meses con desnutrición aguda moderada o severa debe hospitalizarse.
TIP: La fase de estabilización hospitalaria inicia con F-75.
BIBLIOGRAFÍA
Ministerio de Salud y Protección Social de Colombia. Resolución 2350 de 2020."""


def _letras(p):
    return [o["letter"] for o in p.options]


def test_vineta_con_grados_y_grupo_a_no_se_parte():
    p = parse_case(CASO_139)
    assert p.parsed_ok, p.errors
    assert _letras(p) == ["A", "B", "C", "D"], _letras(p)
    assert "38,9 °C. Niega tos" in p.vignette
    assert p.vignette.endswith("¿Cuál es la conducta inicial más apropiada?")
    assert p.correct_letter == "A"
    assert p.correct_text == "Penicilina V 500 mg VO por 10 días"


def test_opcion_escrita_con_punto_y_parentesis():
    p = parse_case(CASO_1)
    assert p.parsed_ok, p.errors
    assert _letras(p) == ["A", "B", "C", "D"], _letras(p)
    assert p.options[0]["text"] == "Hospitalizar"
    assert p.options[1]["text"] == "Control ambulatorio en 15 días"
    assert p.correct_text == "Hospitalizar"


def test_opciones_en_una_linea_se_siguen_partiendo():
    base = "Niña de 20 meses con desnutrición aguda severa. ¿Cuál es la conducta más adecuada?\n{ops}\nCORRECTA: B\nLa B es correcta.\nTip: Un tip.\nBibliografía: Resolución 115 de 2026."
    for ops in ("A. Egreso  B. Continuar FTLC  C. Sulfato ferroso  D. Complementación",
                "A) Egreso  B) Continuar FTLC\nC) Sulfato ferroso\nD) Complementación"):
        p = parse_case(base.format(ops=ops))
        assert _letras(p) == ["A", "B", "C", "D"], (ops, _letras(p))
        assert p.correct_text == "Continuar FTLC"
    # la pregunta y las opciones en la misma línea
    p = parse_case("Niña de 20 meses con desnutrición aguda severa. ¿Cuál es la conducta más adecuada? "
                   "A. Egreso B. Continuar FTLC C. Sulfato ferroso D. Complementación\nCORRECTA: B\nLa B es correcta.")
    assert _letras(p) == ["A", "B", "C", "D"], _letras(p)
    assert p.vignette.endswith("¿Cuál es la conducta más adecuada?")


def test_prosa_con_letras_seguidas_no_se_parte():
    vineta = ("Hombre de 40 años con faringitis por estreptococo del grupo A. Vacunado contra hepatitis B. "
              "Recibe vitamina C. ¿Cuál es el siguiente paso?")
    p = parse_case(vineta + "\nA. Observar\nB. Penicilina\nC. Amigdalectomía\nD. Azitromicina\nCORRECTA: B\nLa B es correcta.")
    assert _letras(p) == ["A", "B", "C", "D"], _letras(p)
    assert p.vignette == vineta


def test_letra_fuera_de_secuencia_queda_en_la_opcion():
    p = parse_case("Lactante con raquitismo. ¿Cuál es la conducta?\nA. Suplementar vitamina D. Control en 1 mes\n"
                   "B. Observar\nC. Calcio IV\nD. Remitir\nCORRECTA: A\nLa A es correcta.")
    assert _letras(p) == ["A", "B", "C", "D"], _letras(p)
    assert p.options[0]["text"] == "Suplementar vitamina D. Control en 1 mes"


if __name__ == "__main__":
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in pruebas:
        f()
        print("  ✓", f.__name__)
    print(f"{len(pruebas)} pruebas OK")
