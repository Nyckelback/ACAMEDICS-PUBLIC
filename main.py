# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes
)

from config import BOT_TOKEN, ADMIN_USER_IDS, AUTO_DELETE_MINUTES
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
    return user_id in ADMIN_USER_IDS


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start → Bienvenida
    /start jst_30 → Entrega justificación (SIEMPRE, sin importar si es admin)
    /start jst_30-31-32 → Entrega múltiples justificaciones
    """
    if not update.message:
        return
    
    text = update.message.text.strip()
    
    # Si contiene jst_ → SIEMPRE entregar justificación (admin o no)
    if "jst_" in text:
        await handle_justification_request(update, context)
        return
    
    # /start normal → Bienvenida simple
    welcome_text = (
        "👋 **Bienvenido**\n\n"
        "Este bot entrega contenido educativo protegido.\n\n"
        "🔹 Haz clic en los botones **\"Ver justificación 💬\"** "
        "que encuentres en los canales.\n\n"
        f"⚠️ Los mensajes se auto-eliminan en {AUTO_DELETE_MINUTES} minutos.\n"
        "💾 Guarda el contenido importante."
    )
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Panel de administrador - Solo para admins"""
    if not is_admin(update.effective_user.id):
        return
    
    admin_text = (
        "🔐 **Panel de Administrador**\n\n"
        "**📚 Justificaciones:**\n"
        "• `%%% https://t.me/canal?start=just_30` → Botón justificación\n"
        "• `%%% URL?start=just_30-31-32` → Múltiples justificaciones\n"
        "• `/test_just 30` → Probar entrega\n\n"
        "**🔘 Botones personalizados:**\n"
        "• `@@@ Texto | URL` → Botón con link\n"
        "• `@@@ Texto` → Botón sin link\n\n"
        "**📢 Publicidad:**\n"
        "• `/set_ads` → Crear AD\n"
        "• `/list_ads` → Ver ADs\n"
        "• `/delete_ads` → Eliminar AD"
    )
    
    await update.message.reply_text(admin_text, parse_mode="Markdown")


async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detecta %%% y @@@ en canales"""
    msg = update.channel_post
    if not msg:
        return
    
    text = msg.text or msg.caption or ""
    
    if "%%%" in text:
        await handle_justification_link_message(update, context)
        return
    
    if "@@@" in text:
        await handle_button_creation(update, context)
        return


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensajes privados para creación de ADS"""
    if not update.message:
        return
    
    if is_admin(update.effective_user.id):
        await handle_private_message_for_ads(update, context)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Error en el bot", exc_info=context.error)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("set_ads", cmd_set_ads))
    app.add_handler(CommandHandler("delete_ads", cmd_delete_ads))
    app.add_handler(CommandHandler("list_ads", cmd_list_ads))
    app.add_handler(CommandHandler("test_just", cmd_test_justification))
    
    # Mensajes de canal
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_message))
    
    # Mensajes privados
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_message))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_ads_callback, pattern="^ads_"))
    
    app.add_error_handler(on_error)
    
    logger.info("🚀 Bot iniciado")
    logger.info(f"👥 Admins: {ADMIN_USER_IDS}")
    
    app.run_polling(
        allowed_updates=["message", "channel_post", "callback_query"],
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
