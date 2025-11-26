# -*- coding: utf-8 -*-
"""
JUSTIFICATIONS HANDLER - Versión mínima de compatibilidad
La lógica principal está en main.py
Este archivo solo existe por si algún import viejo lo referencia
"""

import logging

logger = logging.getLogger(__name__)


async def handle_justification_start(update, context, param=None):
    """
    COMPATIBILIDAD: Esta función ya no se usa.
    La lógica está en main.py → process_deep_link()
    """
    logger.warning("⚠️ handle_justification_start llamada desde archivo viejo - usar main.py")
    return False


def add_justification_handlers(application):
    """
    COMPATIBILIDAD: Ya no se necesita.
    Los handlers están en main.py
    """
    logger.info("ℹ️ add_justification_handlers: Lógica movida a main.py")
    pass
