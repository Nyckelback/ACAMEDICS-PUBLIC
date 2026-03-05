"""
Medical clinical case parser module.
Parses structured medical case questions with vignettes, options, answers, justifications, tips, and bibliographies.
Handles many format variations for each section header.
"""

import re
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional


@dataclass
class ParsedCase:
    """Dataclass representing a parsed medical case."""
    vignette: str
    options: List[Dict[str, str]]  # [{"letter": "A", "text": "..."}, ...]
    correct_letter: str
    correct_text: str
    justification: str
    tip: str
    bibliography: List[str]
    raw_text: str
    parsed_ok: bool
    errors: List[str]

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)


# ──────────────────────────────────────────────────
# SECTION HEADER PATTERNS (all the variations)
# ──────────────────────────────────────────────────

# CORRECT ANSWER variations
# Matches: CORRECTA, CORRECT, RPTA, RESPUESTA, RESP, RESPUESTA CORRECTA, RPTA CORRECTA,
# OPCIÓN CORRECTA, OPCION CORRECTA, ANSWER, CLAVE, ALTERNATIVA CORRECTA
_ANSWER_KEYWORDS = (
    r"(?:"
    r"RESPUESTA\s+CORRECTA"
    r"|RPTA\s+CORRECTA"
    r"|OPCI[OÓ]N\s+CORRECTA"
    r"|ALTERNATIVA\s+CORRECTA"
    r"|CORRECTA"
    r"|CORRECT"
    r"|RPTA"
    r"|RESPUESTA"
    r"|RESP"
    r"|ANSWER"
    r"|CLAVE"
    r")"
)

_ANSWER_PATTERN = (
    r"(?:^|\n)\s*"
    + _ANSWER_KEYWORDS
    + r"\s*[:=\-.]?\s*([A-F])(?:[.\s,;:\-)}\]]|$)"
)

# JUSTIFICATION / explanation section header variations
# Matches: JUSTIFICACIÓN, JUSTIFICACION, FUNDAMENTACIÓN, FUNDAMENTACION,
# ANÁLISIS, ANALISIS, EXPLICACIÓN, EXPLICACION, SUSTENTO, ARGUMENTO,
# ANÁLISIS Y FUNDAMENTACIÓN DEL CASO CLÍNICO, FUNDAMENTO, DESARROLLO
_JUSTIFICATION_KEYWORDS = (
    r"(?:"
    r"AN[AÁ]LISIS\s+Y\s+FUNDAMENTACI[OÓ]N(?:\s+DEL\s+CASO\s+CL[IÍ]NICO)?"
    r"|JUSTIFICACI[OÓ]N(?:\s+DE\s+LA\s+RESPUESTA)?"
    r"|FUNDAMENTACI[OÓ]N(?:\s+DE\s+LA\s+RESPUESTA)?"
    r"|EXPLICACI[OÓ]N(?:\s+DE\s+LA\s+RESPUESTA)?"
    r"|AN[AÁ]LISIS(?:\s+DEL\s+CASO(?:\s+CL[IÍ]NICO)?)?"
    r"|SUSTENTO(?:\s+CL[IÍ]NICO)?"
    r"|ARGUMENTO(?:\s+CL[IÍ]NICO)?"
    r"|FUNDAMENTO(?:\s+CL[IÍ]NICO)?"
    r"|DESARROLLO"
    r"|JUSTIFICACION"
    r"|FUNDAMENTACION"
    r"|EXPLICACION"
    r")"
)

_JUSTIFICATION_PATTERN = r"(?:^|\n)\s*" + _JUSTIFICATION_KEYWORDS + r"\s*[:.]?\s*(?:\n|$)"

# TIP section header variations
# Matches: TIP ACAMÉDICO, TIP ACADEMICO, TIP, DATO CLAVE, DATO IMPORTANTE,
# PERLA CLÍNICA, PERLA CLINICA, NOTA CLÍNICA, NOTA CLINICA, RECUERDA, RECUERDE,
# DATO CLÍNICO, PEARL, CONSEJO, PUNTO CLAVE, KEY POINT
_TIP_KEYWORDS = (
    r"(?:"
    r"TIP\s+ACAM[EÉ]DICO"
    r"|TIP\s+ACADEMICO"
    r"|TIP\s+CL[IÍ]NICO"
    r"|DATO\s+CLAVE"
    r"|DATO\s+IMPORTANTE"
    r"|DATO\s+CL[IÍ]NICO"
    r"|PERLA\s+CL[IÍ]NICA"
    r"|PERLA\s+CLINICA"
    r"|NOTA\s+CL[IÍ]NICA"
    r"|NOTA\s+CLINICA"
    r"|PUNTO\s+CLAVE"
    r"|KEY\s+POINT"
    r"|PEARL"
    r"|CONSEJO"
    r"|RECUERD[AE]"
    r"|TIP"
    r")"
)

_TIP_PATTERN = r"(?:^|\n)\s*" + _TIP_KEYWORDS + r"\s*[:.]?\s*(?:\n|$)"

# BIBLIOGRAPHY section header variations
# Matches: BIBLIOGRAFÍA, BIBLIOGRAFIA, REFERENCIAS BIBLIOGRÁFICAS, REFERENCIAS,
# BIBLIOGRAPHY, FUENTES, CITAS, REFS, SOURCES, LECTURAS RECOMENDADAS,
# REFERENCIAS BIBLIOGRAFICAS, MATERIAL DE CONSULTA, BIBLIOGRAPHIC REFERENCES
_BIB_KEYWORDS = (
    r"(?:"
    r"REFERENCIAS?\s+BIBLIOGR[AÁ]FICAS?"
    r"|BIBLIOGRAF[IÍ]A"
    r"|BIBLIOGRAFIA"
    r"|BIBLIOGRAPHY"
    r"|BIBLIOGRAPHIC\s+REFERENCES?"
    r"|REFERENCIAS?"
    r"|FUENTES?"
    r"|CITAS?\s*BIBLIOGR[AÁ]FICAS?"
    r"|CITAS?"
    r"|LECTURAS?\s+RECOMENDADAS?"
    r"|MATERIAL\s+DE\s+CONSULTA"
    r"|SOURCES?"
    r"|REFS?"
    r")"
)

_BIB_PATTERN = r"(?:^|\n)\s*" + _BIB_KEYWORDS + r"\s*[:.]?\s*(?:\n|$)"

# OPTIONS - detect option lines (A. or A) format)
_OPTION_START = r"^[A-F][.)]\s+"
# End-of-options keywords: any section header
_END_OPTIONS_KEYWORDS = (
    r"^(?:"
    + _ANSWER_KEYWORDS
    + r"|" + _JUSTIFICATION_KEYWORDS
    + r"|" + _TIP_KEYWORDS
    + r"|" + _BIB_KEYWORDS
    + r")"
)


def _fix_telegram_emojis(text: str) -> str:
    """
    Fix Telegram auto-emoji conversions that break option parsing.
    Telegram converts certain text sequences to emojis:
      B) → 😎 (sunglasses)  D) → 😄 or 😃  :) → various smileys

    Two-pass fix:
    1. Line-start: emoji at start of line → option letter (for option lines)
    2. Inline: emoji anywhere in text → letter with parenthesis (for justification text)
       e.g. "opción 😎" → "opción B)"
    """
    # Map of emojis that Telegram creates from option-like text
    emoji_to_option = {
        "😎": "B) ",   # B) → sunglasses
        "😄": "D) ",   # D) → grinning face
        "😃": "D) ",   # D) → smiley
        "😀": "D) ",   # D) → grinning
        "😁": "D) ",   # D) variant
    }

    # Inline replacements (for mid-text occurrences like "opción 😎")
    emoji_to_inline = {
        "😎": "B)",
        "😄": "D)",
        "😃": "D)",
        "😀": "D)",
        "😁": "D)",
    }

    # Pass 1: fix line-start emojis (option lines)
    lines = text.split("\n")
    fixed = []
    for line in lines:
        stripped = line.strip()
        replaced = False
        for emoji, replacement in emoji_to_option.items():
            if stripped.startswith(emoji):
                rest = stripped[len(emoji):].strip()
                if rest:
                    fixed.append(replacement + rest)
                    replaced = True
                    break
        if not replaced:
            fixed.append(line)
    result = "\n".join(fixed)

    # Pass 2: fix inline emojis (inside justification, tip, etc.)
    for emoji, replacement in emoji_to_inline.items():
        result = result.replace(emoji, replacement)

    return result


def _split_inline_options(text: str) -> str:
    """
    Split options that are on a single line into separate lines.
    e.g. "A. Option one  B. Option two  C. Option three"
    becomes:
    "A. Option one
    B. Option two
    C. Option three"

    This handles cases where the user pastes all options in one paragraph.
    Only splits when 2+ options are found on the same line.
    """
    import re as _re
    # Pattern: letter followed by . or ) and space, at word boundary
    option_pat = _re.compile(r'(?<!\w)([A-F][.)]\s)')

    new_lines = []
    for line in text.split("\n"):
        matches = list(option_pat.finditer(line))
        if len(matches) >= 2:
            # Multiple options on one line - split them
            parts = []
            for idx, m in enumerate(matches):
                start = m.start()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
                part = line[start:end].strip()
                if part:
                    parts.append(part)
            # If first option doesn't start at position 0, keep the prefix as vignette
            if matches[0].start() > 0:
                prefix = line[:matches[0].start()].strip()
                if prefix:
                    new_lines.append(prefix)
            new_lines.extend(parts)
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


def parse_case(text: str) -> ParsedCase:
    """
    Parse a medical clinical case from raw text.
    Handles many format variations for each section header.
    """
    # Pre-process: fix Telegram emoji conversions
    text = _fix_telegram_emojis(text)
    # Pre-process: split inline options onto separate lines
    text = _split_inline_options(text)

    errors = []
    vignette = ""
    options = []
    correct_letter = ""
    correct_text = ""
    justification = ""
    tip = ""
    bibliography = []
    parsed_ok = True

    if not text or not text.strip():
        errors.append("Input text is empty")
        return ParsedCase(
            vignette=vignette, options=options, correct_letter=correct_letter,
            correct_text=correct_text, justification=justification, tip=tip,
            bibliography=bibliography, raw_text=text, parsed_ok=False, errors=errors,
        )

    # Step 1: Extract vignette and options
    vignette, options, options_end_idx = _extract_vignette_and_options(text)
    if not vignette.strip():
        errors.append("Could not extract vignette")
        parsed_ok = False
    if not options:
        errors.append("Could not extract options (need at least one option A-F)")
        parsed_ok = False

    # Step 2: Extract correct answer
    correct_letter, correct_answer_end_idx = _extract_correct_answer(text, options_end_idx)
    if not correct_letter:
        errors.append(
            "Could not detect correct answer. Expected formats: 'CORRECTA: D', 'RPTA: D', "
            "'RESPUESTA CORRECTA: D', 'OPCIÓN CORRECTA: D', 'CLAVE: D', etc."
        )
        parsed_ok = False
    else:
        for opt in options:
            if opt["letter"].upper() == correct_letter.upper():
                correct_text = opt["text"]
                break
        if not correct_text:
            errors.append(f"Correct letter '{correct_letter}' does not match any available option")

    # Step 3: Find section boundaries
    justification_header_idx, justification_header_end = _find_section(text, _JUSTIFICATION_PATTERN)
    tip_start_idx, tip_header_end = _find_section(text, _TIP_PATTERN)
    bib_start_idx, bib_header_end = _find_section(text, _BIB_PATTERN)

    # Step 4: Extract justification
    # Justification can start from:
    #   a) After a JUSTIFICACIÓN header, OR
    #   b) Right after the CORRECTA line (if no explicit justification header)
    just_start = -1
    if justification_header_end >= 0:
        just_start = justification_header_end
    elif correct_answer_end_idx >= 0:
        just_start = correct_answer_end_idx

    if just_start >= 0:
        # End at whichever comes first: TIP or BIBLIOGRAPHY
        just_end = len(text)
        if tip_start_idx >= 0 and tip_start_idx > just_start:
            just_end = tip_start_idx
        if bib_start_idx >= 0 and bib_start_idx > just_start and bib_start_idx < just_end:
            just_end = bib_start_idx
        justification = text[just_start:just_end].strip()
        # If justification starts with a justification header keyword, strip it
        justification = re.sub(
            r"^" + _JUSTIFICATION_KEYWORDS + r"\s*[:.]?\s*\n?",
            "", justification, flags=re.IGNORECASE
        ).strip()

    if not justification:
        errors.append("Could not extract justification")

    # Step 5: Extract TIP section
    if tip_header_end >= 0:
        tip_end = len(text)
        if bib_start_idx >= 0 and bib_start_idx > tip_start_idx:
            tip_end = bib_start_idx
        tip = text[tip_header_end:tip_end].strip()
        # Remove header if it leaked through
        tip = re.sub(
            r"^" + _TIP_KEYWORDS + r"\s*[:.]?\s*\n?",
            "", tip, flags=re.IGNORECASE
        ).strip()
    elif tip_start_idx >= 0:
        tip_end = len(text)
        if bib_start_idx >= 0 and bib_start_idx > tip_start_idx:
            tip_end = bib_start_idx
        tip_raw = text[tip_start_idx:tip_end].strip()
        tip = re.sub(
            r"^" + _TIP_KEYWORDS + r"\s*[:.]?\s*\n?",
            "", tip_raw, flags=re.IGNORECASE
        ).strip()

    # Step 6: Extract bibliography
    if bib_header_end >= 0:
        bib_text = text[bib_header_end:].strip()
        # Also strip header if it leaked
        bib_text = re.sub(
            r"^" + _BIB_KEYWORDS + r"\s*[:.]?\s*\n?",
            "", bib_text, flags=re.IGNORECASE
        ).strip()
        bibliography = _parse_bibliography(bib_text)
    elif bib_start_idx >= 0:
        bib_text = text[bib_start_idx:].strip()
        bib_text = re.sub(
            r"^" + _BIB_KEYWORDS + r"\s*[:.]?\s*\n?",
            "", bib_text, flags=re.IGNORECASE
        ).strip()
        bibliography = _parse_bibliography(bib_text)

    result = ParsedCase(
        vignette=vignette, options=options, correct_letter=correct_letter,
        correct_text=correct_text, justification=justification, tip=tip,
        bibliography=bibliography, raw_text=text,
        parsed_ok=parsed_ok and len(errors) == 0, errors=errors,
    )
    return result


def _find_section(text: str, pattern: str) -> Tuple[int, int]:
    """
    Find a section header in text.
    Returns (start_of_line, end_of_header) where end_of_header is position after the header line.
    Returns (-1, -1) if not found.
    """
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if match:
        pos = match.start()
        if pos < len(text) and text[pos] == "\n":
            pos += 1
        end = match.end()
        return pos, end
    return -1, -1


def _extract_vignette_and_options(text: str) -> Tuple[str, List[Dict[str, str]], int]:
    """Extract vignette and options from text."""
    vignette = ""
    options = []

    lines = text.split("\n")
    first_option_line_idx = -1

    for i, line in enumerate(lines):
        if re.match(_OPTION_START, line.strip()):
            first_option_line_idx = i
            break

    if first_option_line_idx < 0:
        return "", [], len(text)

    vignette_lines = lines[:first_option_line_idx]
    vignette = "\n".join(vignette_lines).strip()

    current_option_letter = None
    current_option_text = ""
    last_option_line_idx = first_option_line_idx

    for i in range(first_option_line_idx, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # Stop at any section header
        if re.match(_END_OPTIONS_KEYWORDS, stripped, re.IGNORECASE):
            break

        match = re.match(r"^([A-F])[.)]\s+(.*)", stripped)
        if match:
            if current_option_letter:
                options.append({
                    "letter": current_option_letter,
                    "text": current_option_text.strip(),
                })
            current_option_letter = match.group(1)
            current_option_text = match.group(2)
            last_option_line_idx = i
        elif current_option_letter:
            current_option_text += " " + stripped
            last_option_line_idx = i

    if current_option_letter:
        options.append({
            "letter": current_option_letter,
            "text": current_option_text.strip(),
        })

    options_end_idx = 0
    for i in range(last_option_line_idx + 1):
        options_end_idx += len(lines[i]) + 1
    options_end_idx = min(options_end_idx, len(text))

    return vignette, options, options_end_idx


def _extract_correct_answer(text: str, start_from: int = 0) -> Tuple[str, int]:
    """Extract the correct answer letter from text."""
    match = re.search(_ANSWER_PATTERN, text[start_from:], re.IGNORECASE | re.MULTILINE)

    if match:
        correct_letter = match.group(1).upper()
        match_pos = start_from + match.start()
        line_end = text.find("\n", match_pos)
        if line_end == -1:
            line_end = len(text)
        else:
            line_end += 1
        return correct_letter, line_end

    return "", -1


def _parse_bibliography(bib_text: str) -> List[str]:
    """
    Parse bibliography text into individual references.
    Handles many formats:
    - Bulleted: - ref, • ref, * ref
    - Numbered: 1. ref, 1) ref, 1- ref, [1] ref
    - Plain lines separated by newlines (each line is a ref if long enough)
    - Multi-line refs (continuation lines without prefix)
    """
    if not bib_text.strip():
        return []

    references = []
    lines = bib_text.split("\n")
    current_ref = ""

    # Pattern for lines that START a new reference
    new_ref_pattern = re.compile(
        r"^\s*(?:"
        r"\d+[\.\)\-\]]\s+"      # 1. or 1) or 1- or 1]
        r"|\[\d+\]\s*"           # [1]
        r"|[-•*►–—]\s+"          # bullet chars
        r"|[a-zA-Z]\)\s+"        # a) b) c)
        r")"
    )

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            # Empty line: save current ref
            if current_ref:
                references.append(current_ref.strip())
                current_ref = ""
            continue

        # Check if this line starts a new reference (has a prefix)
        prefix_match = new_ref_pattern.match(stripped)
        if prefix_match:
            if current_ref:
                references.append(current_ref.strip())
            # Remove the prefix
            current_ref = stripped[prefix_match.end():].strip()
        else:
            # No prefix - could be continuation OR a standalone line
            # If current_ref exists and line starts with lowercase or continues naturally, append
            if current_ref and (stripped[0].islower() or stripped.startswith("(")):
                current_ref += " " + stripped.strip()
            else:
                # Treat as a new standalone reference
                if current_ref:
                    references.append(current_ref.strip())
                current_ref = stripped.strip()

    if current_ref:
        references.append(current_ref.strip())

    # Filter out very short entries (likely not real references)
    references = [r for r in references if len(r) > 15]

    return references


def validate_case(parsed: ParsedCase) -> Tuple[bool, List[str]]:
    """Validate a parsed case to ensure it has all required components."""
    validation_errors = []

    if not parsed.vignette or not parsed.vignette.strip():
        validation_errors.append("Vignette is empty")

    if len(parsed.options) < 2:
        validation_errors.append(f"Need at least 2 options, found {len(parsed.options)}")

    if not parsed.correct_letter:
        validation_errors.append("Correct answer letter not detected")
    else:
        option_letters = [opt["letter"].upper() for opt in parsed.options]
        if parsed.correct_letter.upper() not in option_letters:
            validation_errors.append(
                f"Correct letter '{parsed.correct_letter}' does not match any option: {option_letters}"
            )

    if not parsed.justification or not parsed.justification.strip():
        validation_errors.append("Justification is empty")

    is_valid = len(validation_errors) == 0
    return is_valid, validation_errors


if __name__ == "__main__":
    print("Running comprehensive parser tests...\n")

    # ── Test 1: Standard format ──
    test1 = """Paciente de 28 anos consulta por lesion en vulva.
A. Azitromicina 1 gr VO semanal por 3 semanas
B. Azitromicina 1 gr VO dosis unica
C. Penicilina benzatinica semanal por 3 semanas
D. Penicilina benzatinica IM + azitromicina VO dosis unica
CORRECTA: D
Esta paciente presenta chancro sifilitico. El tratamiento consiste en penicilina mas azitromicina.
TIP ACAMEDICO
La sifilis primaria puede presentarse con variantes atipicas.
BIBLIOGRAFIA
- Tuddenham S, Ghanem KG. Approach to genital ulcers. UpToDate 2026
- Workowski KA. Treatment guidelines for STIs. MMWR 2021."""
    p = parse_case(test1)
    assert p.parsed_ok, f"Test 1 FAIL: {p.errors}"
    assert p.correct_letter == "D"
    assert len(p.options) == 4
    assert p.tip != ""
    assert len(p.bibliography) == 2
    print("Test 1 PASS: Standard CORRECTA + TIP ACAMEDICO + BIBLIOGRAFIA")

    # ── Test 2: RESPUESTA CORRECTA format ──
    test2 = """Caso clinico de paciente.
A) Primera opcion
B) Segunda opcion
C) Tercera opcion
RESPUESTA CORRECTA: B
La segunda opcion es correcta porque razon medica."""
    p = parse_case(test2)
    assert p.parsed_ok, f"Test 2 FAIL: {p.errors}"
    assert p.correct_letter == "B"
    print("Test 2 PASS: RESPUESTA CORRECTA format")

    # ── Test 3: RPTA:C (no space) ──
    test3 = """Caso clinico.
A. Opcion A
B. Opcion B
C. Opcion C
RPTA:C
Justificacion de por que C es correcta."""
    p = parse_case(test3)
    assert p.parsed_ok, f"Test 3 FAIL: {p.errors}"
    assert p.correct_letter == "C"
    print("Test 3 PASS: RPTA:C (no space)")

    # ── Test 4: OPCION CORRECTA ──
    test4 = """Paciente presenta sintomas.
A. Tratamiento 1
B. Tratamiento 2
C. Tratamiento 3
OPCION CORRECTA: A
El tratamiento 1 es el adecuado."""
    p = parse_case(test4)
    assert p.parsed_ok, f"Test 4 FAIL: {p.errors}"
    assert p.correct_letter == "A"
    print("Test 4 PASS: OPCION CORRECTA")

    # ── Test 5: CLAVE: ──
    test5 = """Caso de emergencia.
A. Opcion A
B. Opcion B
CLAVE: B
Porque B es la respuesta."""
    p = parse_case(test5)
    assert p.parsed_ok, f"Test 5 FAIL: {p.errors}"
    assert p.correct_letter == "B"
    print("Test 5 PASS: CLAVE format")

    # ── Test 6: ALTERNATIVA CORRECTA ──
    test6 = """Paciente con fiebre.
A. Paracetamol
B. Ibuprofeno
C. Aspirina
ALTERNATIVA CORRECTA: C
La aspirina es preferida por su efecto antiinflamatorio."""
    p = parse_case(test6)
    assert p.parsed_ok, f"Test 6 FAIL: {p.errors}"
    assert p.correct_letter == "C"
    print("Test 6 PASS: ALTERNATIVA CORRECTA")

    # ── Test 7: TIP variations ──
    test7a = """Caso.
A. X
B. Y
CORRECTA: A
Justificacion aqui es larga y suficiente para el parser.
DATO CLAVE
Este es un dato clave importante para recordar siempre.
BIBLIOGRAFIA
Referencia uno del articulo principal publicado en revista."""
    p = parse_case(test7a)
    assert p.tip != "", f"Test 7a FAIL: TIP empty, errors={p.errors}"
    print("Test 7a PASS: DATO CLAVE as TIP")

    test7b = """Caso.
A. X
B. Y
CORRECTA: B
Justificacion larga y detallada del caso clinico.
PERLA CLINICA
Esta es una perla clinica muy importante que debes saber.
REFERENCIAS
Articulo importante de revista medica publicado en 2024."""
    p = parse_case(test7b)
    assert p.tip != "", f"Test 7b FAIL: TIP empty"
    assert len(p.bibliography) >= 1, f"Test 7b FAIL: no bib"
    print("Test 7b PASS: PERLA CLINICA + REFERENCIAS")

    test7c = """Caso.
A. X
B. Y
CORRECTA: A
Justificacion del caso medico muy importante.
RECUERDA
Siempre considerar diagnostico diferencial en estos pacientes.
FUENTES
Libro de medicina interna Harrison capitulo 45 edicion 2024."""
    p = parse_case(test7c)
    assert p.tip != "", f"Test 7c FAIL: TIP empty"
    assert len(p.bibliography) >= 1, f"Test 7c FAIL: no bib"
    print("Test 7c PASS: RECUERDA + FUENTES")

    # ── Test 8: JUSTIFICACION header explicit ──
    test8 = """Pregunta clinica.
A. Opcion uno
B. Opcion dos
CORRECTA: B
JUSTIFICACION
La opcion B es correcta porque el mecanismo de accion del farmaco actua directamente.
TIP ACAMEDICO
Recordar que este farmaco tiene interacciones importantes.
REFERENCIAS BIBLIOGRAFICAS
1. Harrison Principios de Medicina Interna 21a edicion 2024
2. Goodman Gilman Las Bases Farmacologicas de la Terapeutica 2023"""
    p = parse_case(test8)
    assert p.parsed_ok, f"Test 8 FAIL: {p.errors}"
    assert "opcion B" in p.justification.lower() or "mecanismo" in p.justification.lower(), f"Test 8 FAIL: justification={p.justification[:50]}"
    assert p.tip != ""
    assert len(p.bibliography) >= 2
    print("Test 8 PASS: Explicit JUSTIFICACION header + numbered bib")

    # ── Test 9: Bibliography with various formats ──
    test9 = """Caso.
A. X
B. Y
CORRECTA: A
Justificacion detallada del caso clinico completa.
BIBLIOGRAFIA
[1] Primera referencia del articulo publicado en BMJ 2024
[2] Segunda referencia del estudio clinico en Lancet 2023
[3] Tercera referencia del metaanalisis publicado JAMA 2022"""
    p = parse_case(test9)
    assert len(p.bibliography) == 3, f"Test 9 FAIL: got {len(p.bibliography)} refs"
    print("Test 9 PASS: [1] [2] [3] bibliography format")

    # ── Test 10: Bibliography with no prefixes (plain lines) ──
    test10 = """Caso.
A. X
B. Y
CORRECTA: B
Justificacion del caso clinico detallada y completa.
BIBLIOGRAFIA
Tuddenham S, Ghanem KG. Approach to genital ulcers. UpToDate 2026
Workowski KA, Bachmann LH. STI Treatment Guidelines. MMWR 2021
Hernandez MI. Sindrome ulceroso genital. Universidad de Antioquia 2021"""
    p = parse_case(test10)
    assert len(p.bibliography) == 3, f"Test 10 FAIL: got {len(p.bibliography)} refs: {p.bibliography}"
    print("Test 10 PASS: Plain lines bibliography (no prefixes)")

    # ── Test 11: LECTURAS RECOMENDADAS ──
    test11 = """Caso clinico.
A. Op A
B. Op B
CORRECTA: A
La justificacion es suficientemente larga para el test.
LECTURAS RECOMENDADAS
Harrison Principios de Medicina Interna 21a ed capitulo completo
Farreras Rozman Medicina Interna volumen 2 capitulo 15 edicion"""
    p = parse_case(test11)
    assert len(p.bibliography) >= 2, f"Test 11 FAIL: {len(p.bibliography)} refs"
    print("Test 11 PASS: LECTURAS RECOMENDADAS as bibliography")

    # ── Test 12: ANALISIS Y FUNDAMENTACION header ──
    test12 = """Pregunta de caso.
A. Primera
B. Segunda
C. Tercera
CORRECTA: C
ANALISIS Y FUNDAMENTACION DEL CASO CLINICO
La tercera opcion es correcta debido a que el paciente presenta una condicion especifica.
TIP
Siempre evaluar el contexto clinico completo antes de decidir tratamiento."""
    p = parse_case(test12)
    assert p.parsed_ok, f"Test 12 FAIL: {p.errors}"
    assert "tercera" in p.justification.lower(), f"Test 12 FAIL: just={p.justification[:50]}"
    assert p.tip != ""
    print("Test 12 PASS: ANALISIS Y FUNDAMENTACION + plain TIP")

    # ── Test 13: Mixed bullet bib ──
    test13 = """Caso.
A. X
B. Y
CORRECTA: A
Justificacion amplia y detallada del caso clinico.
BIBLIOGRAFÍA
• Primer articulo de revista medica importante publicado recientemente
• Segundo articulo con resultados clinicos relevantes del estudio"""
    p = parse_case(test13)
    assert len(p.bibliography) == 2, f"Test 13 FAIL: got {len(p.bibliography)}"
    print("Test 13 PASS: Bullet bibliography with accented header")

    # ── Test 14: lowercase correcta ──
    test14 = """Caso.
A. X
B. Y
C. Z
correcta: c
Justificacion en minusculas del caso clinico completo."""
    p = parse_case(test14)
    assert p.correct_letter == "C", f"Test 14 FAIL: got {p.correct_letter}"
    print("Test 14 PASS: lowercase correcta")

    # ── Test 15: RESP format ──
    test15 = """Caso.
A. Op A
B. Op B
C. Op C
RESP: C
Justificacion valida y completa del caso medico."""
    p = parse_case(test15)
    assert p.correct_letter == "C"
    print("Test 15 PASS: RESP format")

    # ── Test 16: NOTA CLINICA as TIP ──
    test16 = """Caso.
A. X
B. Y
CORRECTA: A
Justificacion completa y detallada del caso medico.
NOTA CLINICA
Esta nota clinica es muy relevante para la practica diaria."""
    p = parse_case(test16)
    assert p.tip != "", f"Test 16 FAIL: tip empty"
    print("Test 16 PASS: NOTA CLINICA as TIP")

    # ── Test 17: PUNTO CLAVE as TIP ──
    test17 = """Caso.
A. X
B. Y
CORRECTA: B
Justificacion detallada del caso para evaluacion medica.
PUNTO CLAVE
Este es el punto clave principal a recordar siempre."""
    p = parse_case(test17)
    assert p.tip != "", f"Test 17 FAIL: tip empty"
    print("Test 17 PASS: PUNTO CLAVE as TIP")

    # ── Test 18: EXPLICACION DE LA RESPUESTA ──
    test18 = """Caso.
A. X
B. Y
CORRECTA: A
EXPLICACION DE LA RESPUESTA
La opcion A es la correcta por multiples razones clinicas y farmacologicas.
TIP ACAMEDICO
Recordar siempre verificar contraindicaciones del farmaco."""
    p = parse_case(test18)
    assert "opcion A" in p.justification or "razones" in p.justification, f"Test 18 FAIL: {p.justification[:50]}"
    print("Test 18 PASS: EXPLICACION DE LA RESPUESTA")

    # ── Test 19: SUSTENTO CLINICO ──
    test19 = """Caso.
A. X
B. Y
C. Z
CORRECTA: C
SUSTENTO CLINICO
El sustento clinico se basa en la evidencia de multiples ensayos aleatorios."""
    p = parse_case(test19)
    assert "sustento" in p.justification.lower() or "evidencia" in p.justification.lower()
    print("Test 19 PASS: SUSTENTO CLINICO")

    # ── Test 20: RPTA CORRECTA ──
    test20 = """Caso medico complejo.
A. Opcion 1
B. Opcion 2
RPTA CORRECTA: A
La opcion 1 es la adecuada para este caso clinico."""
    p = parse_case(test20)
    assert p.correct_letter == "A", f"Test 20 FAIL: got {p.correct_letter}"
    print("Test 20 PASS: RPTA CORRECTA")

    # ── Test 21: Telegram emoji B) → 😎 ──
    test21 = """Lactante de 5 meses con desnutricion.
A) Hospitalizar inmediatamente
😎 Dar consejeria nutricional y citar en 14 dias
C) Iniciar F-75 ambulatoria
D) Remitir a urgencias
CORRECTA: A
Justificacion completa del caso de desnutricion aguda."""
    p = parse_case(test21)
    assert p.parsed_ok, f"Test 21 FAIL: {p.errors}"
    assert len(p.options) == 4, f"Test 21 FAIL: got {len(p.options)} options: {[o['letter'] for o in p.options]}"
    assert p.options[1]["letter"] == "B", f"Test 21 FAIL: second option is {p.options[1]['letter']}"
    assert p.correct_letter == "A"
    print("Test 21 PASS: Telegram emoji 😎 → B)")

    # ── Test 22: Telegram emoji D) → 😄 ──
    test22 = """Caso clinico.
A. Opcion A
B. Opcion B
C. Opcion C
😄 Opcion D
CORRECTA: D
La opcion D es correcta por razones clinicas."""
    p = parse_case(test22)
    assert p.parsed_ok, f"Test 22 FAIL: {p.errors}"
    assert len(p.options) == 4, f"Test 22 FAIL: got {len(p.options)} options"
    assert p.options[3]["letter"] == "D", f"Test 22 FAIL: fourth option is {p.options[3]['letter']}"
    print("Test 22 PASS: Telegram emoji 😄 → D)")

    # ── Test 23: Inline emoji in justification text ──
    text23 = """Viñeta: Paciente con fiebre.
A) Opción A
B) Opción B
C) Opción C
D) Opción D
CORRECTA: A
Justificación: La opción 😎 no corresponde porque el tratamiento indicado no es ambulatorio. La opción 😄 tampoco es correcta.
Tip: Recordar el protocolo
Bibliografía: Ref 1"""
    p = parse_case(text23)
    assert p.parsed_ok, f"Test 23 FAIL: {p.errors}"
    assert "B)" in p.justification, f"Test 23 FAIL: inline 😎 not replaced. Got: {p.justification[:100]}"
    assert "D)" in p.justification, f"Test 23 FAIL: inline 😄 not replaced. Got: {p.justification[:100]}"
    assert "😎" not in p.justification, f"Test 23 FAIL: 😎 still in justification"
    assert "😄" not in p.justification, f"Test 23 FAIL: 😄 still in justification"
    print("Test 23 PASS: Inline emojis in justification replaced")

    # ── Test 24: Inline options on single line (A. B. C. D. format) ──
    text24 = """PEDIATRÍA - NUTRICIÓN PEDIÁTRICA
Niña de 20 meses con desnutrición aguda severa. ¿Cuál es la conducta más adecuada?
A. Suspender la FTLC y dar egreso  B. Continuar FTLC y seguimiento ambulatorio hasta alcanzar P/T ≥-1 DE  C. Suspender la FTLC e iniciar sulfato ferroso  D. Cambiar a complementación alimentaria convencional
CORRECTA: B
La opción B es correcta porque el criterio de egreso exige P/T ≥-1 DE.
Tip: Recordar criterios de egreso.
Bibliografía: Resolución 115 de 2026."""
    p = parse_case(text24)
    assert p.parsed_ok, f"Test 24 FAIL: {p.errors}"
    assert len(p.options) == 4, f"Test 24 FAIL: got {len(p.options)} options, expected 4. Options: {[o['letter'] for o in p.options]}"
    assert p.options[0]["letter"] == "A", f"Test 24 FAIL: first option is {p.options[0]['letter']}"
    assert p.options[1]["letter"] == "B", f"Test 24 FAIL: second option is {p.options[1]['letter']}"
    assert p.options[2]["letter"] == "C", f"Test 24 FAIL: third option is {p.options[2]['letter']}"
    assert p.options[3]["letter"] == "D", f"Test 24 FAIL: fourth option is {p.options[3]['letter']}"
    assert p.correct_letter == "B", f"Test 24 FAIL: correct is {p.correct_letter}"
    print("Test 24 PASS: Inline options on single line (A. B. C. D.)")

    # ── Test 25: Mixed - some options on same line, some on separate ──
    text25 = """Caso clínico de prueba.
A) Primera opción  B) Segunda opción
C) Tercera opción
D) Cuarta opción
CORRECTA: C
La C es correcta.
Tip: Un tip.
Bibliografía: Ref."""
    p = parse_case(text25)
    assert p.parsed_ok, f"Test 25 FAIL: {p.errors}"
    assert len(p.options) == 4, f"Test 25 FAIL: got {len(p.options)} options"
    assert p.options[0]["letter"] == "A", f"Test 25 FAIL: first is {p.options[0]['letter']}"
    assert p.options[1]["letter"] == "B", f"Test 25 FAIL: second is {p.options[1]['letter']}"
    print("Test 25 PASS: Mixed inline + separate options")

    print("\n=== ALL 25 TESTS PASSED ===")
