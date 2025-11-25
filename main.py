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
    if not update.message: return
    
    args = context.args
    
    if args and len(args) > 0:
        await handle_justification_request(update, context)
        return
    
    await update.message.reply_text(
        f"👋 **Bienvenido a Academeds**\n\n"
        "Bot de entrega de contenido clínico.\n"
        f"Los contenidos se borran a los {AUTO_DELETE_MINUTES} min.",
        parse_mode="Markdown"
    )

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Panel de administrador"""
    if not is_admin(update.effective_user.id): 
        return
    
    await update.message.reply_text(
        "🛠️ **PANEL DE ADMINISTRADOR**\n\n"
        
        "📦 **LOTES**\n"
        "`/lote` — Iniciar modo lote\n"
        "`/enviar` — Publicar lote al canal\n"
        "`/cancelar` — Descartar lote\n\n"
        
        "📢 **PUBLICIDAD**\n"
        "`/set_ads` — Crear anuncio programado\n"
        "`/list_ads` — Ver anuncios activos\n"
        "`/delete_ads` — Eliminar anuncio\n\n"
        
        "🔗 **SINTAXIS DE BOTONES**\n"
        "`%%% t.me/canal/22` → Botón justificación\n"
        "`@@@ Texto | link` → Botón con URL\n"
        "`@@@ Texto | @user` → Botón a perfil\n"
        "`@@@ Texto | t.me/canal/33` → Botón a contenido\n\n"
        
        "⏱️ **TIEMPOS ADS**\n"
        "`5m` = 5 minutos\n"
        "`1h` = 1 hora\n"
        "`8` = 8 horas (legacy)\n\n"
        
        "💡 El botón solo (%%% o @@@) se pega al mensaje anterior."
    , parse_mode="Markdown")

async def handle_private_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router para mensajes privados de admins"""
    user_id = update.effective_user.id
    if not is_admin(user_id) or not update.message: 
        return

    # Si está creando un anuncio
    if context.user_data.get('creating_ad', False):
        await ads_handler.handle_private_message_for_ads(update, context)
        return

    # Si está en modo lote, procesar
    processed = await batch_handler.handle_batch_message(update, context)
    
    # Si NO está en modo lote y no se procesó, NO enviar mensaje molesto
    # Simplemente ignorar (el admin puede usar /lote cuando quiera)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    
    # Lotes
    app.add_handler(CommandHandler("lote", batch_handler.cmd_lote))
    app.add_handler(CommandHandler("enviar", batch_handler.cmd_enviar))
    app.add_handler(CommandHandler("cancelar", batch_handler.cmd_cancelar))
    
    # Ads
    app.add_handler(CommandHandler("set_ads", ads_handler.cmd_set_ads))
    app.add_handler(CommandHandler("list_ads", ads_handler.cmd_list_ads))
    app.add_handler(CommandHandler("delete_ads", ads_handler.cmd_delete_ads))

    # Router de mensajes privados
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_router))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(ads_handler.handle_ads_callback, pattern="^del_ad_"))

    logger.info("🚀 Bot iniciado")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
