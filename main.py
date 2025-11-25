# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes
)

from config import BOT_TOKEN, ADMIN_USER_IDS, AUTO_DELETE_MINUTES
# Handlers
from justifications_handler import handle_justification_request
import batch_handler
import ads_handler

logging.basicConfig(format="%(asctime)s - %(name)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja /start.
    Si tiene argumentos (ej: /start 22), entrega contenido.
    Si no tiene argumentos, saluda (solo para usuarios nuevos).
    """
    if not update.message: return
    
    # Verificamos argumentos (lo que viene después de /start)
    args = context.args
    
    if args and len(args) > 0:
        # Si hay argumentos (ej: '22', 'jst_22', '30-31'), vamos directo a entregar
        await handle_justification_request(update, context)
        return
    
    # SOLO si no hay argumentos mandamos bienvenida
    await update.message.reply_text(
        f"👋 **Bienvenido a Acamedics**\n\n"
        "Bot de entrega de contenido clínico.\n"
        f"Los contenidos se borran a los {AUTO_DELETE_MINUTES} min.",
        parse_mode="Markdown"
    )

async def cmd_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text(
        "🛠️ **COMANDOS**\n"
        "/lote - Iniciar carga\n"
        "/enviar - Publicar\n"
        "/cancelar - Borrar lote\n"
        "/set_ads - Crear anuncio\n"
        "/list_ads - Ver anuncio\n"
        "/delete_ads - Borrar anuncio\n\n"
        "🔗 **LINKS AUTOMÁTICOS**\n"
        "Pega: `%%% https://t.me/canal/22`\n"
        "Sale: Botón 'VER JUSTIFICACIÓN 💬' que lleva al msg 22"
    , parse_mode="Markdown")

async def handle_private_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id) or not update.message: return

    if context.user_data.get('creating_ad', False):
        await ads_handler.handle_private_message_for_ads(update, context)
        return

    processed = await batch_handler.handle_batch_message(update, context)
    if not processed:
        await update.message.reply_text("Usa /lote para empezar.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin_help))
    
    # Lotes
    app.add_handler(CommandHandler("lote", batch_handler.cmd_lote))
    app.add_handler(CommandHandler("enviar", batch_handler.cmd_enviar))
    app.add_handler(CommandHandler("cancelar", batch_handler.cmd_cancelar))
    
    # Ads
    app.add_handler(CommandHandler("set_ads", ads_handler.cmd_set_ads))
    app.add_handler(CommandHandler("list_ads", ads_handler.cmd_list_ads))
    app.add_handler(CommandHandler("delete_ads", ads_handler.cmd_delete_ads))

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_router))
    app.add_handler(CallbackQueryHandler(ads_handler.handle_ads_callback, pattern="^del_ad_"))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
