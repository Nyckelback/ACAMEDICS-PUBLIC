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
    Entrada única:
    1. Si trae jst_XXX -> Entrega contenido (justificación o PDF)
    2. Si no -> Saluda
    """
    if not update.message: return
    text = update.message.text.strip()
    
    # Entrega de contenido (Ya sea justificación o PDF linkeado con @@@)
    if "jst_" in text:
        await handle_justification_request(update, context)
        return
    
    # Bienvenida genérica
    await update.message.reply_text(
        f"👋 **Bienvenido a Acamedics**\n\n"
        "Aquí recibirás el contenido protegido que solicites en el canal.\n"
        f"⚠️ Los mensajes se borran en {AUTO_DELETE_MINUTES} minutos.",
        parse_mode="Markdown"
    )

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resumen simple para el admin"""
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text(
        "🛠️ **COMANDOS ADMIN**\n\n"
        "📦 **Contenido (Lotes)**\n"
        "/lote - Iniciar carga\n"
        "/enviar - Publicar lote\n"
        "/cancelar - Cancelar\n\n"
        "📢 **Publicidad (ADS)**\n"
        "/set_ads - Crear AD\n"
        "/list_ads - Ver activas\n"
        "/delete_ads - Borrar\n\n"
        "📝 **Sintaxis en textos:**\n"
        "`%%% URL` → Botón 'Ver Justificación'\n"
        "`@@@ Texto | URL` → Botón Personalizado"
    , parse_mode="Markdown")

async def handle_private_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Router inteligente: 
    ¿Está creando un AD? -> ads_handler
    ¿Está en modo Lote? -> batch_handler
    """
    user_id = update.effective_user.id
    if not is_admin(user_id) or not update.message: return

    # 1. Prioridad: Creación de ADS
    if context.user_data.get('creating_ad', False):
        await ads_handler.handle_private_message_for_ads(update, context)
        return

    # 2. Prioridad: Modo LOTE
    # Si handle_batch_message devuelve True, es que procesó el mensaje
    processed = await batch_handler.handle_batch_message(update, context)
    if processed:
        return

    # 3. Si escribe sin comandos
    await update.message.reply_text("Usa /lote o /set_ads para empezar.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos Públicos
    app.add_handler(CommandHandler("start", cmd_start))
    
    # Comandos Admin (Lotes)
    app.add_handler(CommandHandler("lote", batch_handler.cmd_lote))
    app.add_handler(CommandHandler("enviar", batch_handler.cmd_enviar))
    app.add_handler(CommandHandler("cancelar", batch_handler.cmd_cancelar))
    
    # Comandos Admin (Ads)
    app.add_handler(CommandHandler("set_ads", ads_handler.cmd_set_ads))
    app.add_handler(CommandHandler("list_ads", ads_handler.cmd_list_ads))
    app.add_handler(CommandHandler("delete_ads", ads_handler.cmd_delete_ads))
    app.add_handler(CommandHandler("admin", cmd_admin))

    # Router de mensajes privados
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_router))
    
    # Callbacks (para borrar ads)
    app.add_handler(CallbackQueryHandler(ads_handler.handle_ads_callback, pattern="^del_ad_"))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
