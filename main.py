# -*- coding: utf-8 -*-
import logging
from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes
)

from config import BOT_TOKEN, ADMIN_USER_IDS, AUTO_DELETE_MINUTES
from justifications_handler import handle_justification_request
import batch_handler
import ads_handler

logging.basicConfig(format="%(asctime)s - %(name)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja /start para usuarios y entregas"""
    if not update.message: return
    text = update.message.text.strip()
    
    # Entrega de contenido (justificación)
    if "jst_" in text:
        await handle_justification_request(update, context)
        return
    
    # Bienvenida normal
    await update.message.reply_text(
        f"👋 **Bienvenido a Acamedics**\n\n"
        "Este bot entrega las justificaciones y contenidos de casos clínicos.\n"
        f"⚠️ Los mensajes entregados se borran en {AUTO_DELETE_MINUTES} mins.",
        parse_mode="Markdown"
    )

async def cmd_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra la lista de comandos para el admin"""
    if not is_admin(update.effective_user.id): return
    
    help_text = (
        "🛠️ **PANEL DE CONTROL ACAMEDICS**\n\n"
        "📦 **GESTIÓN DE LOTES (Casos/Contenido)**\n"
        "• `/lote` - Iniciar modo carga (envía fotos, encuestas, textos)\n"
        "• `/enviar` - Publicar todo lo cargado al canal\n"
        "• `/cancelar` - Borrar lo cargado y salir\n\n"
        "📢 **PUBLICIDAD (ADS)**\n"
        "• `/set_ads` - Crear anuncio (reemplaza al anterior)\n"
        "• `/list_ads` - Ver anuncio activo\n"
        "• `/delete_ads` - Borrar anuncio\n\n"
        "⚡ **ATAJOS DE BOTONES**\n"
        "_(Úsalos en el texto de la foto o en un mensaje aparte)_\n"
        "• `%%% LINK_DEL_BOT` → Botón 'Ver justificación 💬'\n"
        "• `@@@ Texto | URL` → Botón personalizado\n"
        "  _Ej: @@@ Descargar PDF | https://google.com_"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def handle_private_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router central de mensajes"""
    user_id = update.effective_user.id
    if not is_admin(user_id) or not update.message: return

    # 1. Crear ADS
    if context.user_data.get('creating_ad', False):
        await ads_handler.handle_private_message_for_ads(update, context)
        return

    # 2. Modo LOTE (Captura encuestas, fotos, textos)
    processed = await batch_handler.handle_batch_message(update, context)
    if processed:
        return

    # 3. Mensaje suelto
    await update.message.reply_text("Panel de control: /admin\nIniciar publicación: /lote")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos Públicos
    app.add_handler(CommandHandler("start", cmd_start))
    
    # Comandos Admin
    app.add_handler(CommandHandler("admin", cmd_admin_help))
    
    # Lotes
    app.add_handler(CommandHandler("lote", batch_handler.cmd_lote))
    app.add_handler(CommandHandler("enviar", batch_handler.cmd_enviar))
    app.add_handler(CommandHandler("cancelar", batch_handler.cmd_cancelar))
    
    # Ads
    app.add_handler(CommandHandler("set_ads", ads_handler.cmd_set_ads))
    app.add_handler(CommandHandler("list_ads", ads_handler.cmd_list_ads))
    app.add_handler(CommandHandler("delete_ads", ads_handler.cmd_delete_ads))

    # Router Mensajes
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_router))
    app.add_handler(CallbackQueryHandler(ads_handler.handle_ads_callback, pattern="^del_ad_"))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
