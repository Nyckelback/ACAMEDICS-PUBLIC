# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes
)

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
    handle_ads_callback,
    handle_private_message_for_ads
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
    """
    Maneja /start y deep links de justificaciones.
    
    Casos:
    - /start → Mensaje de bienvenida
    - /start jst_6 → Entrega justificación #6
    """
    if not update.message:
        return
    
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Si es un deep link de justificación (formato: /start jst_6)
    if " jst_" in text or text.startswith("/start jst_"):
        # Reconstruir el comando completo si viene separado
        if " jst_" in text:
            # El texto viene como "/start jst_6"
            pass
        
        handled = await handle_justification_request(update, context)
        if handled:
            return
    
    # Mensaje de bienvenida normal
    if is_admin(user_id):
        welcome_text = (
            "🔐 **Panel de Administrador**\n\n"
            "**📚 Justificaciones:**\n"
            "• `%%% https://t.me/canal/ID` → Crear botón\n"
            "• `/test_just ID` → Probar entrega\n\n"
            "**🔘 Botones personalizados:**\n"
            "• `@@@ Texto | URL` → Botón con link\n"
            "• `@@@ Texto` → Botón sin link\n"
            "• Puedes agregar varios botones\n\n"
            "**📢 Publicidad:**\n"
            "• `/set_ads` → Crear nueva AD\n"
            "• `/list_ads` → Ver ADs activas\n"
            "• `/delete_ads` → Eliminar AD\n\n"
            "📡 Las ADS se publican en el canal público"
        )
    else:
        welcome_text = (
            "👋 **Bienvenido**\n\n"
            "Este bot entrega contenido educativo protegido.\n\n"
            "🔹 Haz clic en los botones **\"Ver justificación 📚\"** "
            "que encuentres en los canales.\n\n"
            f"⚠️ Los mensajes se auto-eliminan en {10} minutos.\n"
            "💾 Guarda el contenido importante."
        )
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja mensajes en canales para detectar:
    - %%% → Justificaciones
    - @@@ → Botones personalizados
    """
    msg = update.channel_post
    if not msg:
        return
    
    text = msg.text or msg.caption or ""
    
    # Detectar %%% para justificaciones (tiene prioridad)
    if "%%%" in text:
        await handle_justification_link_message(update, context)
        return
    
    # Detectar @@@ para botones
    if "@@@" in text:
        await handle_button_creation(update, context)
        return


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes privados (para creación de ADS)"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Solo admins pueden interactuar con el sistema de ADS
    if is_admin(user_id):
        await handle_private_message_for_ads(update, context)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Maneja errores del bot"""
    logger.exception("Error en el bot", exc_info=context.error)


def main():
    """Función principal"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comando /start (incluye deep links)
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
    
    # Handler para mensajes privados (creación de ADS)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_private_message
    ))
    
    # Callbacks (botones inline)
    app.add_handler(CallbackQueryHandler(handle_ads_callback, pattern="^ads_"))
    
    # Error handler
    app.add_error_handler(on_error)
    
    logger.info("🚀 Bot iniciado")
    logger.info(f"👥 Admins: {ADMIN_USER_IDS}")
    
    app.run_polling(
        allowed_updates=["message", "channel_post", "callback_query"],
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
