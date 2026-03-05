"""
Configuration module for Medical Clinical Cases Telegram Bot.
Loads environment variables with sensible defaults.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Configuration class with all settings."""

    # Telegram Bot Configuration
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_USER_IDS = [
        int(uid.strip())
        for uid in os.getenv("ADMIN_USER_IDS", "").split(",")
        if uid.strip()
    ]

    # Channel Configuration
    PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID", "0"))

    # Supabase Configuration
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

    # Mini App Configuration
    MINIAPP_URL = os.getenv("MINIAPP_URL", "")

    # Legacy Configuration
    JUSTIFICATIONS_CHAT_ID = int(os.getenv("JUSTIFICATIONS_CHAT_ID", "-1003058530208"))
    AUTO_DELETE_MINUTES = int(os.getenv("AUTO_DELETE_MINUTES", "10"))
    TZ = os.getenv("TZ", "America/Bogota")

    # Validation
    @staticmethod
    def validate():
        """Validate that all required configuration values are set."""
        required = ["BOT_TOKEN", "PUBLIC_CHANNEL_ID", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "MINIAPP_URL"]
        missing = [key for key in required if not getattr(Config, key)]
        if missing:
            raise ValueError(f"Missing required configuration values: {missing}")


# Validate on import
try:
    Config.validate()
except ValueError as e:
    print(f"Configuration Error: {e}")
    print("Please set all required environment variables before starting the bot.")
