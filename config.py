# -*- coding: utf-8 -*-
"""
CONFIGURACIÓN DEL BOT
"""
import os
from zoneinfo import ZoneInfo

# ============ TOKEN ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ============ CANALES ============
# Canal público donde se publican los casos
PUBLIC_CHANNEL_ID = int(os.environ.get("PUBLIC_CHANNEL_ID", "-1002679848195"))

# Canal de justificaciones (solo para compatibilidad con links viejos)
JUSTIFICATIONS_CHAT_ID = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))

# ============ ADMINS ============
# IDs de usuarios que pueden usar comandos admin
# Separados por coma en la variable de entorno
_admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
if _admin_ids_str:
    ADMIN_USER_IDS = [int(x.strip()) for x in _admin_ids_str.split(",") if x.strip().isdigit()]
else:
    # IDs por defecto (agregar tu ID aquí)
    ADMIN_USER_IDS = [123456789]  # CAMBIAR POR TU ID

# ============ AUTO-DELETE ============
# Minutos antes de borrar justificaciones (0 = no borrar)
AUTO_DELETE_MINUTES = int(os.environ.get("AUTO_DELETE_MINUTES", "10"))

# ============ TIMEZONE ============
TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)
