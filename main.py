# -*- coding: utf-8 -*-
"""
MAIN.PY - Bot de casos clínicos
TODO INTEGRADO - Sin imports externos problemáticos
PROTECCIÓN: Timeout + try/except para no trabarse
"""
import logging
import asyncio
import re
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters,
    ContextTypes
)

from config import BOT_TOKEN, ADMIN_USER_IDS, TZ

# ============ CONFIGURACIÓN ============
try:
    from config import AUTO_DELETE_MINUTES
except ImportError:
    AUTO_DELETE_MINUTES = 10

try:
    from config import JUSTIFICATIONS_CHAT_ID
except ImportError:
    JUSTIFICATIONS_CHAT_ID = -1003058530208

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ ESTADO GLOBAL ============
channel_cache: Dict[str, int] = {}
pending_deletions: Dict[int, List[tuple]] = {}
last_sent: Dict[int, List[int]] = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


# ============ FUNCIONES DE JUSTIFICACIONES (INTEGRADAS) ============

async def resolve_channel(bot, identifier: str) -> Optional[int]:
    """Resuelve username a chat_id con cache."""
    if identifier.isdigit():
        return int(f"-100{identifier}")
    
    if identifier in channel_cache:
        return channel_cache[identifier]
    
    try:
        chat = await bot.get_chat(f"@{identifier}")
        channel_cache[identifier] = chat.id
        logger.info(f"📦 Cache: @{identifier} → {chat.id}")
        return chat.id
    except Exception as e:
        logger.error(f"❌ No se pudo resolver @{identifier}: {e}")
        return None


async def delete_previous_messages(bot, user_id: int):
    """Borra mensajes anteriores del usuario."""
    if user_id not in last_sent:
        return
    
    msg_ids = last_sent.pop(user_id, [])
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=user_id, message_id=mid)
        except:
            pass


async def send_protected_content(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    source_chat_id: int,
    message_ids: List[int],
    with_joke: bool
):
    """Envía contenido protegido al usuario."""
    # Borrar mensajes anteriores
    await delete_previous_messages(context.bot, user_id)
    
    sent_msg_ids = []
    
    try:
        for msg_id in message_ids:
            try:
                sent = await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=source_chat_id,
                    message_id=msg_id,
                    protect_content=True
                )
                sent_msg_ids.append(sent.message_id)
                await asyncio.sleep(0.3)  # Pequeña pausa
            except Exception as e:
                logger.error(f"❌ Error copiando msg {msg_id}: {e}")
        
        if not sent_msg_ids:
            await context.bot.send_message(user_id, "❌ No se pudo obtener el contenido.")
            return
        
        # Mensaje de acompañamiento
        if with_joke:
            try:
                from justification_messages import get_random_message
                text = get_random_message()
            except:
                text = "📚 ¡Contenido entregado!"
        else:
            text = "📦 ¡Contenido entregado!"
        
        companion = await context.bot.send_message(user_id, text)
        sent_msg_ids.append(companion.message_id)
        
        # Guardar para borrar después
        last_sent[user_id] = sent_msg_ids.copy()
        
        # Agendar eliminación
        if AUTO_DELETE_MINUTES > 0:
            now = datetime.now(TZ)
            if user_id not in pending_deletions:
                pending_deletions[user_id] = []
            for mid in sent_msg_ids:
                pending_deletions[user_id].append((mid, now))
        
    except Exception as e:
        logger.error(f"❌ Error enviando contenido: {e}")


async def process_deep_link(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str) -> bool:
    """
    Procesa el parámetro del deep link.
    Retorna True si se manejó, False si no se reconoció.
    """
    user_id = update.effective_user.id
    logger.info(f"🔍 Deep link: user={user_id}, param='{param}'")
    
    try:
        # ========== FORMATO: just_30 ==========
        if param.startswith('just_'):
            msg_id = int(param[5:])
            await send_protected_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [msg_id], True)
            return True
        
        # ========== FORMATO: solo número ==========
        if param.isdigit():
            msg_id = int(param)
            await send_protected_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [msg_id], True)
            return True
        
        # ========== FORMATO: j_30 ==========
        if param.startswith('j_'):
            msg_id = int(param[2:])
            await send_protected_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [msg_id], True)
            return True
        
        # ========== NUEVOS FORMATOS ==========
        with_joke = True
        working = param
        
        # Prefijo n_ = sin chiste
        if param.startswith('n_'):
            with_joke = False
            working = param[2:]
        
        # p_USERNAME_MSGIDS (canal público)
        if working.startswith('p_'):
            parts = working[2:].rsplit('_', 1)
            if len(parts) == 2:
                username = parts[0]
                msg_ids = [int(x) for x in parts[1].split('-')]
                
                chat_id = await resolve_channel(context.bot, username)
                if chat_id:
                    await send_protected_content(context, user_id, chat_id, msg_ids, with_joke)
                    return True
                else:
                    await update.message.reply_text("❌ No se pudo acceder al canal")
                    return True
        
        # c_CHATID_MSGIDS (canal privado)
        if working.startswith('c_'):
            parts = working[2:].split('_')
            if len(parts) == 2:
                chat_id = int(f"-100{parts[0]}")
                msg_ids = [int(x) for x in parts[1].split('-')]
                await send_protected_content(context, user_id, chat_id, msg_ids, with_joke)
                return True
        
    except Exception as e:
        logger.error(f"❌ Error procesando deep link '{param}': {e}")
        # NO re-lanzar, solo log
    
    return False


# ============ COMANDO /START ============

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start - Maneja deep links o muestra bienvenida.
    ROBUSTO: No se traba aunque falle.
    """
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    try:
        # ¿Tiene parámetro de deep link?
        if context.args and len(context.args) > 0:
            param = context.args[0]
            
            # Si es admin, limpiar estados
            if is_admin(user_id):
                context.user_data.clear()
                try:
                    from batch_handler import batch_mode, active_batches
                    batch_mode[user_id] = False
                    active_batches.pop(user_id, None)
                except:
                    pass
            
            # Procesar deep link (con timeout de 10 segundos)
            try:
                handled = await asyncio.wait_for(
                    process_deep_link(update, context, param),
                    timeout=10.0
                )
                if handled:
                    return
            except asyncio.TimeoutError:
                logger.error(f"⏰ Timeout procesando deep link: {param}")
                await update.message.reply_text("⏰ Tiempo agotado. Intenta de nuevo.")
                return
            except Exception as e:
                logger.error(f"❌ Error en deep link: {e}")
                # Continuar a bienvenida
        
        # Bienvenida normal
        await update.message.reply_text(
            "👋 ¡Bienvenido!\n\n"
            "Este bot envía casos clínicos educativos.\n"
            "Suscríbete al canal para recibir contenido."
        )
    
    except Exception as e:
        logger.error(f"❌ Error fatal en cmd_start: {e}")
        # NO re-lanzar para no trabar el bot


# ============ OTROS COMANDOS ============

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra tu ID"""
    await update.message.reply_text(
        f"🆔 Tu User ID: `{update.effective_user.id}`",
        parse_mode="Markdown"
    )


async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa modo lote"""
    if not is_admin(update.effective_user.id):
        return
    
    context.user_data.clear()
    
    from batch_handler import cmd_lote as batch_cmd_lote
    await batch_cmd_lote(update, context)


async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el lote"""
    if not is_admin(update.effective_user.id):
        return
    
    from batch_handler import cmd_enviar as batch_cmd_enviar
    await batch_cmd_enviar(update, context)


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela todo"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    context.user_data.clear()
    
    try:
        from batch_handler import batch_mode, active_batches
        batch_mode[user_id] = False
        active_batches.pop(user_id, None)
    except:
        pass
    
    await update.message.reply_text("🗑️ Cancelado.")


async def cmd_set_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configura publicidad"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    try:
        from batch_handler import batch_mode, active_batches
        batch_mode[user_id] = False
        active_batches.pop(user_id, None)
    except:
        pass
    
    from ads_handler import cmd_set_ads as ads_cmd_set_ads
    await ads_cmd_set_ads(update, context)


async def cmd_stop_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detiene publicidad"""
    if not is_admin(update.effective_user.id):
        return
    
    from ads_handler import cmd_stop_ads as ads_cmd_stop_ads
    await ads_cmd_stop_ads(update, context)


async def cmd_list_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista ads activas"""
    if not is_admin(update.effective_user.id):
        return
    
    from ads_handler import cmd_list_ads as ads_cmd_list_ads
    await ads_cmd_list_ads(update, context)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Panel admin"""
    if not is_admin(update.effective_user.id):
        return
    
    await update.message.reply_text(
        "🔧 **PANEL DE ADMINISTRADOR**\n\n"
        "**📦 LOTES:**\n"
        "`/lote` - Iniciar modo lote\n"
        "`/enviar` - Publicar lote\n"
        "`/cancelar` - Cancelar proceso\n\n"
        "**📢 PUBLICIDAD:**\n"
        "`/set_ads` - Configurar anuncio\n"
        "`/list_ads` - Ver ads activas\n"
        "`/stop_ads` - Detener ads\n\n"
        "**📝 SINTAXIS:**\n"
        "`%%% t.me/canal/22` → Con chiste\n"
        "`@@@ Texto | t.me/canal/22` → Sin chiste\n"
        "`@@@ Texto | @user` → Link directo",
        parse_mode="Markdown"
    )


# ============ ROUTER DE MENSAJES ============

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router de mensajes para admins"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return
    
    # ADS
    ads_state = context.user_data.get('ads_state')
    if ads_state:
        from ads_handler import handle_ads_message
        if await handle_ads_message(update, context):
            return
    
    # LOTE
    try:
        from batch_handler import batch_mode, handle_batch_message
        if batch_mode.get(user_id, False):
            if await handle_batch_message(update, context):
                return
    except:
        pass
    
    await update.message.reply_text(
        "⚠️ No hay modo activo.\n\nUsa `/lote` para empezar.",
        parse_mode="Markdown"
    )


# ============ LIMPIEZA PERIÓDICA ============

async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    """Elimina mensajes viejos cada minuto."""
    now = datetime.now(TZ)
    cutoff = now - timedelta(minutes=AUTO_DELETE_MINUTES)
    total = 0
    
    for user_id in list(pending_deletions.keys()):
        messages = pending_deletions.get(user_id, [])
        to_keep = []
        
        for msg_id, timestamp in messages:
            if timestamp < cutoff:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                    total += 1
                except:
                    pass
            else:
                to_keep.append((msg_id, timestamp))
        
        if to_keep:
            pending_deletions[user_id] = to_keep
        else:
            pending_deletions.pop(user_id, None)
    
    if total > 0:
        logger.info(f"🧹 Limpieza: {total} mensajes eliminados")


# ============ MAIN ============

def main():
    """Función principal"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("lote", cmd_lote))
    app.add_handler(CommandHandler("enviar", cmd_enviar))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CommandHandler("cancel", cmd_cancelar))
    app.add_handler(CommandHandler("set_ads", cmd_set_ads))
    app.add_handler(CommandHandler("stop_ads", cmd_stop_ads))
    app.add_handler(CommandHandler("list_ads", cmd_list_ads))
    app.add_handler(CommandHandler("admin", cmd_admin))
    
    # Router de mensajes
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_message
    ))
    
    # Job de limpieza cada 60 segundos
    if app.job_queue:
        app.job_queue.run_repeating(cleanup_job, interval=60, first=10)
        logger.info(f"⏰ Limpieza automática cada 60s")
    
    logger.info("🚀 Bot iniciado!")
    logger.info("⚠️ Asegúrate de que NO hay otra instancia corriendo")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
