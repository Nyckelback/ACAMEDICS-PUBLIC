# -*- coding: utf-8 -*-
import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
JUSTIFICATIONS_CHAT_ID = int(os.environ["JUSTIFICATIONS_CHAT_ID"])
PUBLIC_CHANNEL_ID = int(os.environ["PUBLIC_CHANNEL_ID"])
ADMIN_USER_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()]
AUTO_DELETE_MINUTES = int(os.environ.get("AUTO_DELETE_MINUTES", "10"))
TZNAME = os.environ.get("TIMEZONE", "America/Bogota")
TZ = ZoneInfo(TZNAME)
