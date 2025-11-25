# -*- coding: utf-8 -*-
"""
MAIN.PY - Bot principal de casos clínicos
CORREGIDO: Router sin superposiciones, cancelación cruzada
"""
import logging
from telegram import Update
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters,
    ContextTypes
)

from config import BOT_TOKEN, ADMIN_USER_IDS

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


# ============ COMANDOS ============

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start - maneja justificaciones o bienvenida"""
    text = update.message.text or ""
    
    # Si tiene parámetros, puede ser justificación
    if ' ' in text:
        from justifications_handler import handle_justification_start
        handled = await handle_justification_start(update, context)
        if handled:
            return
    
    # Bienvenida normal
    await update.message.reply_text(
        "👋 ¡Bienvenido!\n\n"
        "Este bot envía casos clínicos educativos.\n"
        "Suscríbete al canal para recibir contenido."
    )


async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa modo lote - CANCELA otros procesos"""
    if not is_admin(update.effective_user.id):
        return
    
    # Cancelar ads si estaba en configuración
    context.user_data.clear()
    
    # Llamar al handler de lote
    from batch_handler import cmd_lote as batch_cmd_lote
    await batch_cmd_lote(update, context)


async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el lote"""
    if not is_admin(update.effective_user.id):
        return
    
    from batch_handler import cmd_enviar as batch_cmd_enviar
    await batch_cmd_enviar(update, context)


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela TODO - lote, ads, cualquier proceso"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    # Limpiar estado de usuario
    context.user_data.clear()
    
    # Cancelar modo lote
    from batch_handler import batch_mode, active_batches
    batch_mode[user_id] = False
    active_batches.pop(user_id, None)
    
    # Cancelar ads en configuración (NO detiene ads activos)
    # Para detener ads activos usar /stop_ads
    
    await update.message.reply_text("🗑️ Cancelado.")


async def cmd_set_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configura publicidad - CANCELA modo lote"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    # Cancelar modo lote
    from batch_handler import batch_mode, active_batches
    batch_mode[user_id] = False
    active_batches.pop(user_id, None)
    
    # Iniciar configuración de ads
    from ads_handler import cmd_set_ads as ads_cmd_set_ads
    await ads_cmd_set_ads(update, context)


async def cmd_stop_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detiene publicidad activa"""
    if not is_admin(update.effective_user.id):
        return
    
    from ads_handler import cmd_stop_ads as ads_cmd_stop_ads
    await ads_cmd_stop_ads(update, context)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Panel de administrador"""
    if not is_admin(update.effective_user.id):
        return
    
    await update.message.reply_text(
        "🔧 **PANEL DE ADMINISTRADOR**\n\n"
        "**📦 LOTES:**\n"
        "`/lote` - Iniciar modo lote\n"
        "`/enviar` - Publicar lote\n"
        "`/cancelar` - Cancelar proceso actual\n\n"
        "**📢 PUBLICIDAD:**\n"
        "`/set_ads` - Configurar anuncio\n"
        "`/stop_ads` - Detener anuncio\n\n"
        "**📝 SINTAXIS DE BOTONES:**\n"
        "`%%% t.me/canal/22` → Justificación\n"
        "`@@@ Texto | link.com` → Botón custom\n"
        "`@@@ Texto | @usuario` → Link a perfil\n\n"
        "**⏰ TIEMPOS ADS:**\n"
        "`5m` → 5 minutos\n"
        "`1h` → 1 hora\n"
        "`8` → 8 horas (legacy)",
        parse_mode="Markdown"
    )


# ============ ROUTER DE MENSAJES ============

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router principal de mensajes"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Solo procesar mensajes de admins
    if not is_admin(user_id):
        return
    
    # PRIORIDAD 1: Verificar si está en configuración de ADS
    ads_state = context.user_data.get('ads_state')
    if ads_state:
        from ads_handler import handle_ads_message
        handled = await handle_ads_message(update, context)
        if handled:
            return
    
    # PRIORIDAD 2: Verificar si está en modo LOTE
    from batch_handler import batch_mode
    if batch_mode.get(user_id, False):
        from batch_handler import handle_batch_message
        handled = await handle_batch_message(update, context)
        if handled:
            return
    
    # Si no está en ningún modo, ignorar
    # (los comandos ya se manejan por CommandHandler)


# ============ MAIN ============

def main():
    """Función principal"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("lote", cmd_lote))
    app.add_handler(CommandHandler("enviar", cmd_enviar))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CommandHandler("cancel", cmd_cancelar))
    app.add_handler(CommandHandler("set_ads", cmd_set_ads))
    app.add_handler(CommandHandler("stop_ads", cmd_stop_ads))
    app.add_handler(CommandHandler("admin", cmd_admin))
    
    # Router de mensajes (después de comandos)
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_message
    ))
    
    logger.info("🚀 Bot iniciado!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
