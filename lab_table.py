"""lab_table.py — laboratorios de la viñeta como tabla en el mensaje del canal.

Copia de la regla canónica ACAMEDICS-LAB/code/labs_tabla.py (JP, 7-oct-2026): mismas funciones
`partes` y `como_monoespaciado`, mismos casos de prueba (tests/labs_tabla_casos.json).
Telegram no tiene tablas: el bloque va en <pre> con columnas alineadas SOLO con espacios, así
el texto normalizado del mensaje sigue siendo el de la viñeta (canal_ops._reconstruir compara
el texto con los espacios colapsados para encontrar mensajes viejos).
"""
import json
import os
import re
import sys

MAX_LINEA = 80
MAX_PALABRAS = 9
MIN_FILAS = 3

# Resultados cualitativos que pueden ser el valor de un parámetro
CUALI = {
    "negativo", "negativa", "negativos", "negativas", "positivo", "positiva", "positivos", "positivas",
    "normal", "normales", "presente", "presentes", "ausente", "ausentes", "trazas", "indetectable",
    "escasos", "escasas", "numerosos", "numerosas", "abundantes", "raros", "raras", "reactivo", "reactiva",
    "nitritos", "claro", "clara", "turbio", "turbia", "amarillo", "ámbar", "detectable", "indetectables",
}
NUMERO = re.compile(r"^[<>≤≥~±]?[+\-−]?\d")
CRUCES = re.compile(r"^\++$")
VINETA = re.compile(r"^[-–—•·▪*✓✗✔✘⚠→>#|]")


def _es_valor(tok):
    t = tok.strip("(),;:").lower()
    return bool(NUMERO.match(tok) or CRUCES.match(tok) or t in CUALI)


def fila(linea):
    """«Hb 11,2 g/dL» → (etiqueta, valor) como índices dentro de la línea, o None."""
    s = linea.rstrip()
    if not s or len(s) > MAX_LINEA or s.endswith(".") or len(s.split()) > MAX_PALABRAS:
        return None
    if VINETA.match(s.lstrip()):          # «- …», «• …», «✓ …»: es una lista, no un laboratorio
        return None
    toks = [(m.start(), m.end()) for m in re.finditer(r"\S+", s)]
    for i in range(1, len(toks)):
        if _es_valor(s[toks[i][0]:toks[i][1]]):
            etiqueta = (toks[0][0], toks[i - 1][1])
            if not re.search(r"[^\W\d_]", s[etiqueta[0]:etiqueta[1]]):
                return None
            return etiqueta, (toks[i][0], len(s))
    return None


def titulo(linea):
    s = linea.strip()
    if not s or VINETA.match(s) or len(s) > 40 or re.search(r"\d", s) or s.endswith(".") or len(s.split()) > 5:
        return False
    return bool(re.search(r"[^\W\d_]", s))


def partes(texto):
    texto = texto or ""
    lineas, pos = [], 0
    for crudo in texto.split("\n"):
        lineas.append((pos, pos + len(crudo), crudo))
        pos += len(crudo) + 1

    # 1) clasificar cada línea
    clase = []
    for ini, fin, s in lineas:
        if not s.strip():
            clase.append("blanco")
        elif fila(s):
            clase.append("fila")
        elif titulo(s):
            clase.append("titulo")
        else:
            clase.append("prosa")

    # 2) tramos de filas/títulos; un título solo vale si le siguen filas dentro del tramo
    tramos, i, n = [], 0, len(lineas)
    while i < n:
        if clase[i] in ("fila", "titulo"):
            j = i
            while j < n and clase[j] in ("fila", "titulo"):
                j += 1
            # recortar títulos colgando al final
            k = j
            while k > i and clase[k - 1] == "titulo":
                k -= 1
            if any(clase[x] == "fila" for x in range(i, k)):
                tramos.append([i, k])
            i = max(j, i + 1)
        else:
            i += 1

    # 3) unir tramos separados solo por líneas en blanco; exigir MIN_FILAS por grupo
    grupos = []
    for t in tramos:
        if grupos and all(clase[x] == "blanco" for x in range(grupos[-1][-1][1], t[0])):
            grupos[-1].append(t)
        else:
            grupos.append([t])
    validos = [t for g in grupos if sum(clase[x] == "fila" for a, b in g for x in range(a, b)) >= MIN_FILAS for t in g]

    # 4) armar las partes cubriendo todo el texto
    out, cursor = [], 0
    for a, b in validos:
        ini, fin = lineas[a][0], lineas[b - 1][1]
        if ini > cursor:
            out.append({"tipo": "texto", "ini": cursor, "fin": ini})
        filas = []
        for x in range(a, b):
            li, lf, s = lineas[x]
            if clase[x] == "fila":
                (ea, eb), (va, vb) = fila(s)
                filas.append({"tipo": "fila", "etiqueta": [li + ea, li + eb], "valor": [li + va, li + vb], "ini": li, "fin": lf})
            else:
                ta = li + (len(s) - len(s.lstrip()))
                filas.append({"tipo": "titulo", "texto": [ta, li + len(s.rstrip())], "ini": li, "fin": lf})
        out.append({"tipo": "tabla", "ini": ini, "fin": fin, "filas": filas})
        cursor = fin
    if cursor < len(texto) or not out:
        out.append({"tipo": "texto", "ini": cursor, "fin": len(texto)})
    return out


MIN_FILAS_DOS_COLUMNAS = 8


def columnas(filas):
    """Reparte las filas de una tabla larga en dos columnas lado a lado (JP, 7-oct-2026: «se ve
    muy largo… doble columna»). Con menos de 8 filas de datos, una sola columna. El corte cae en
    el título de grupo más cercano a la mitad (p. ej., sangre | orina); si no hay ninguno entre
    el 30 % y el 70 %, en la mitad, sin dejar un título huérfano al final de la primera columna.
    Devuelve una lista de 1 o 2 listas, en el mismo orden del texto."""
    n = len(filas)
    if sum(f["tipo"] == "fila" for f in filas) < MIN_FILAS_DOS_COLUMNAS:
        return [filas]
    titulos = [k for k in range(1, n) if filas[k]["tipo"] == "titulo" and 0.3 * n <= k <= 0.7 * n]
    if titulos:
        k = min(titulos, key=lambda x: (abs(x - n / 2), x))
    else:
        k = (n + 1) // 2
        while k > 1 and filas[k - 1]["tipo"] == "titulo":
            k -= 1
    return [filas[:k], filas[k:]]


def tiene_tabla(texto):
    return any(p["tipo"] == "tabla" for p in partes(texto))


def como_monoespaciado(texto, sep=2):
    """Para Telegram: el bloque en columnas alineadas con espacios (sin otros caracteres, así
    el texto normalizado sigue siendo el mismo que el de la viñeta). Devuelve lista de
    (tipo, contenido) con tipo «texto» o «pre»."""
    out = []
    for p in partes(texto):
        if p["tipo"] == "texto":
            out.append(("texto", texto[p["ini"]:p["fin"]]))
            continue
        # los parámetros que siguen a un título de grupo van sangrados (solo espacios)
        sangria, grupo, filas_txt = "  ", False, []
        for f in p["filas"]:
            if f["tipo"] == "titulo":
                grupo = True
                filas_txt.append((None, texto[f["texto"][0]:f["texto"][1]]))
            else:
                et = (sangria if grupo else "") + texto[f["etiqueta"][0]:f["etiqueta"][1]]
                filas_txt.append((et, texto[f["valor"][0]:f["valor"][1]]))
        ancho = max(len(et) for et, _ in filas_txt if et is not None)
        renglones = [v if et is None else et + " " * (ancho - len(et) + sep) + v for et, v in filas_txt]
        out.append(("pre", "\n".join(renglones)))
    return out


def vineta_telegram(texto):
    """(texto, parse_mode) para mandar la viñeta como mensaje. Sin bloque de laboratorios
    devuelve el texto tal cual y parse_mode None: los casos sin tabla salen exactamente igual
    que antes."""
    import html as _html
    texto = texto or ""
    if not tiene_tabla(texto):
        return texto, None
    trozos = como_monoespaciado(texto)
    out = []
    for i, (tipo, contenido) in enumerate(trozos):
        if tipo == "pre":
            out.append("<pre>" + _html.escape(contenido) + "</pre>")
            continue
        # el bloque <pre> ya se separa solo: un salto a cada lado basta (antes quedaban dos)
        if i + 1 < len(trozos) and contenido.endswith("\n\n"):
            contenido = contenido.rstrip("\n") + "\n"
        if i > 0 and contenido.startswith("\n\n"):
            contenido = "\n" + contenido.lstrip("\n")
        out.append(_html.escape(contenido))
    return "".join(out), "HTML"
