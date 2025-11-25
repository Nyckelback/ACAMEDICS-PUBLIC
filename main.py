# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

from config import BOT_TOKEN, ADMIN_USER_IDS
from justifications_handler import (
    handle_justification_link_message,
    handle_justification_request,
    cmd_test_justification
)
from buttons_handler import handle_button_creation
from ads_handler import (
    cmd_set_ads,
    cmd_delete_ads,
    cmd_list_ads,
    handle_ads_callback
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    """Verifica si un usuario es admin"""
    return user_id in ADMIN_USER_IDS

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja /start y deep links de justificaciones"""
    if not update.message:
        return
    
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Si es un deep link de justificación (formato: /start just_123)
    if text.startswith("/start just_"):
        await handle_justification_request(update, context)
        return
    
    # Mensaje de bienvenida diferenciado
    if is_admin(user_id):
        welcome_text = (
            "🔐 **Panel de Administrador**\n\n"
            "**Gestión de Justificaciones:**\n"
            "• `%%% https://t.me/canal/ID` - Crear botón de justificación\n"
            "• `/test_just ID` - Probar justificación\n\n"
            "**Gestión de Botones:**\n"
            "• `@@@ Texto | URL` - Crear botón con link\n"
            "• `@@@ Texto solo` - Crear botón sin link\n"
            "• Puedes agregar múltiples botones por mensaje\n\n"
            "**Gestión de Publicidad:**\n"
            "• `/set_ads` - Crear nueva publicidad\n"
            "• `/list_ads` - Ver publicidades activas\n"
            "• `/delete_ads` - Eliminar publicidad\n\n"
            "📢 Las ADS se publican automáticamente en el canal público"
        )
    else:
        welcome_text = (
            "👋 **Bienvenido al Bot de Justificaciones**\n\n"
            "Este bot te ayuda a acceder a contenido educativo protegido.\n\n"
            "Para usar el bot, haz clic en los botones **'Ver justificación 📚'** "
            "que encuentres en los canales educativos.\n\n"
            "⚠️ **Importante:** Los mensajes se auto-eliminan después de 10 minutos. "
            "Guarda el contenido importante en tus mensajes guardados."
        )
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes en canales (para detectar %%% y @@@)"""
    msg = update.channel_post
    if not msg:
        return
    
    text = msg.text or msg.caption or ""
    
    # Detectar %%% para justificaciones
    if "%%%" in text:
        await handle_justification_link_message(update, context)
        return
    
    # Detectar @@@ para botones
    if "@@@" in text:
        await handle_button_creation(update, context)
        return

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Maneja errores"""
    logger.exception("Error en el bot", exc_info=context.error)

def main():
    """Función principal"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos públicos
    app.add_handler(CommandHandler("start", cmd_start))
    
    # Comandos de admin
    app.add_handler(CommandHandler("set_ads", cmd_set_ads))
    app.add_handler(CommandHandler("delete_ads", cmd_delete_ads))
    app.add_handler(CommandHandler("list_ads", cmd_list_ads))
    app.add_handler(CommandHandler("test_just", cmd_test_justification))
    
    # Handler para mensajes de canal (detecta %%% y @@@)
    app.add_handler(MessageHandler(
        filters.ChatType.CHANNEL,
        handle_channel_message
    ))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_ads_callback, pattern="^ads_"))
    
    # Error handler
    app.add_error_handler(on_error)
    
    logger.info("🚀 Bot iniciado - Sistema de Justificaciones y ADS")
    logger.info(f"👥 Admins autorizados: {ADMIN_USER_IDS}")
    
    app.run_polling(
        allowed_updates=["message", "channel_post", "callback_query"],
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
