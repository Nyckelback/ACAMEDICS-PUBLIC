# -*- coding: utf-8 -*-
"""
Sistema de Contenido Protegido - VERSIÓN FINAL
Compatible con main.py actual
Soporta TODOS los formatos de deep link
"""

import logging
import asyncio
import re
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from telegram.error import TelegramError

from config import TZ

logger = logging.getLogger(__name__)

# ============ CONFIGURACIÓN ============
# Importar desde config, con fallbacks
try:
    from config import AUTO_DELETE_MINUTES
except ImportError:
    AUTO_DELETE_MINUTES = 10

try:
    from config import JUSTIFICATIONS_CHAT_ID
except ImportError:
    JUSTIFICATIONS_CHAT_ID = -1003058530208

# ============ CACHE Y ESTADO ============
channel_cache: Dict[str, int] = {}
pending_deletions: Dict[int, List[Tuple[int, datetime]]] = {}
last_sent: Dict[int, List[int]] = {}
deletion_lock = asyncio.Lock()


# ============ RESOLVER CANAL (CON CACHE) ============

async def resolve_channel(bot, identifier: str) -> Optional[int]:
    """
    Resuelve identificador a chat_id CON CACHE.
    """
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
        logger.error(f"❌ No se pudo resolver canal @{identifier}: {e}")
        return None


# ============ ELIMINAR MENSAJES ANTERIORES ============

async def delete_previous(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Borra los mensajes anteriores del usuario inmediatamente."""
    if user_id not in last_sent:
        return
    
    msg_ids = last_sent.pop(user_id, [])
    if not msg_ids:
        return
    
    for mid in msg_ids:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=mid)
        except:
            pass


# ============ LIMPIEZA PERIÓDICA ============

async def cleanup_old_messages(context: ContextTypes.DEFAULT_TYPE):
    """Elimina mensajes viejos en batch."""
    async with deletion_lock:
        now = datetime.now(TZ)
        cutoff = now - timedelta(minutes=AUTO_DELETE_MINUTES)
        
        users_to_clean = list(pending_deletions.keys())
        total_deleted = 0
        
        for user_id in users_to_clean:
            messages = pending_deletions.get(user_id, [])
            to_delete = []
            to_keep = []
            
            for msg_id, timestamp in messages:
                if timestamp < cutoff:
                    to_delete.append(msg_id)
                else:
                    to_keep.append((msg_id, timestamp))
            
            if to_keep:
                pending_deletions[user_id] = to_keep
            else:
                pending_deletions.pop(user_id, None)
            
            for mid in to_delete:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=mid)
                    total_deleted += 1
                except:
                    pass
        
        if total_deleted > 0:
            logger.info(f"🧹 Limpieza: {total_deleted} mensajes eliminados")


# ============ ENVIAR CONTENIDO ============

async def send_content(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    source_chat_id: int,
    message_ids: List[int],
    with_joke: bool
):
    """Envía contenido al usuario."""
    now = datetime.now(TZ)
    
    # Borrar mensajes anteriores primero
    await delete_previous(context, user_id)
    
    loading_msg = await context.bot.send_message(
        chat_id=user_id,
        text="⏳ Obteniendo contenido..."
    )
    
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
            except Exception as e:
                logger.error(f"❌ Error copiando msg {msg_id}: {e}")
        
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=loading_msg.message_id)
        except:
            pass
        
        if not sent_msg_ids:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ No se pudo obtener el contenido."
            )
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
        
        companion_msg = await context.bot.send_message(
            chat_id=user_id,
            text=text
        )
        sent_msg_ids.append(companion_msg.message_id)
        
        # Guardar para borrar cuando pida otro
        last_sent[user_id] = sent_msg_ids.copy()
        
        # Agendar para batch
        if AUTO_DELETE_MINUTES > 0:
            async with deletion_lock:
                if user_id not in pending_deletions:
                    pending_deletions[user_id] = []
                for mid in sent_msg_ids:
                    pending_deletions[user_id].append((mid, now))
        
    except Exception as e:
        logger.error(f"❌ Error enviando contenido: {e}")
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=loading_msg.message_id)
        except:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ No se pudo obtener el contenido."
        )


# ============ PROCESAR PARÁMETRO DE START ============

async def process_start_param(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str) -> bool:
    """
    Procesa el parámetro del /start.
    Soporta TODOS los formatos:
    - just_30 (formato viejo)
    - 30 (solo número)
    - j_30 (con prefijo j)
    - p_username_30 (canal público)
    - p_username_30-31-32 (múltiples)
    - c_123456_30 (canal privado)
    - n_p_username_30 (sin chiste)
    - n_c_123456_30 (sin chiste)
    """
    user_id = update.effective_user.id
    
    logger.info(f"🔍 Procesando param: '{param}' de usuario {user_id}")
    
    # ========== FORMATO VIEJO: just_30 ==========
    if param.startswith('just_'):
        try:
            message_id = int(param[5:])  # Quitar "just_"
            logger.info(f"📥 Formato just_: msg={message_id}")
            await send_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [message_id], True)
            return True
        except ValueError:
            pass
    
    # ========== SOLO NÚMERO: 30 ==========
    if param.isdigit():
        message_id = int(param)
        logger.info(f"📥 Solo número: msg={message_id}")
        await send_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [message_id], True)
        return True
    
    # ========== FORMATO j_30 ==========
    if param.startswith('j_'):
        try:
            message_id = int(param[2:])
            logger.info(f"📥 Formato j_: msg={message_id}")
            await send_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [message_id], True)
            return True
        except:
            pass
    
    # ========== NUEVOS FORMATOS ==========
    with_joke = True
    working_param = param
    
    # Detectar si es sin chiste (prefijo n_)
    if param.startswith('n_'):
        with_joke = False
        working_param = param[2:]
    
    try:
        # p_USERNAME_MSGIDS (canal público)
        if working_param.startswith('p_'):
            parts = working_param[2:].rsplit('_', 1)
            if len(parts) != 2:
                raise ValueError("Formato inválido")
            
            username = parts[0]
            msg_ids_str = parts[1]
            message_ids = [int(x) for x in msg_ids_str.split('-')]
            
            chat_id = await resolve_channel(context.bot, username)
            if not chat_id:
                await update.message.reply_text("❌ No se pudo acceder al canal")
                return True
            
            logger.info(f"📥 Público: @{username} msgs={message_ids}")
            await send_content(context, user_id, chat_id, message_ids, with_joke)
            return True
        
        # c_CHATID_MSGIDS (canal privado)
        if working_param.startswith('c_'):
            parts = working_param[2:].split('_')
            if len(parts) != 2:
                raise ValueError("Formato inválido")
            
            chat_id = int(f"-100{parts[0]}")
            msg_ids_str = parts[1]
            message_ids = [int(x) for x in msg_ids_str.split('-')]
            
            logger.info(f"📥 Privado: chat={chat_id} msgs={message_ids}")
            await send_content(context, user_id, chat_id, message_ids, with_joke)
            return True
        
    except (ValueError, IndexError) as e:
        logger.warning(f"⚠️ Parámetro inválido: {param} → {e}")
    
    return False


# ============ HANDLER PARA REGEX (MÁXIMA COMPATIBILIDAD) ============

async def handle_start_regex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler usando regex para máxima compatibilidad.
    Captura /start con cualquier parámetro.
    FUNCIONA EN PRIMERA VEZ (usuario nuevo).
    """
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    
    # Extraer parámetro
    match = re.match(r'^/start\s+(.+)$', text)
    if not match:
        # Solo /start sin parámetro
        await update.message.reply_text(
            "👋 ¡Hola! Soy el bot de ACADEMEDS.\n\n"
            "📚 Usa los botones en el canal para recibir justificaciones.\n"
            "🔒 El contenido está protegido y se elimina automáticamente."
        )
        return
    
    param = match.group(1).strip()
    handled = await process_start_param(update, context, param)
    
    if not handled:
        await update.message.reply_text(
            "👋 ¡Hola! Soy el bot de ACADEMEDS.\n\n"
            "📚 Usa los botones en el canal para recibir justificaciones.\n"
            "🔒 El contenido está protegido y se elimina automáticamente."
        )


# ============ COMPATIBILIDAD: Handler viejo para just_ID ============

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler de compatibilidad para el formato viejo /start just_ID
    """
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    
    # Solo manejar /start just_NÚMERO
    if not text.startswith("/start just_"):
        return
    
    try:
        message_id = int(text.replace("/start just_", ""))
        user_id = update.effective_user.id
        logger.info(f"📥 Compat just_: Usuario {user_id} → msg {message_id}")
        await send_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [message_id], True)
    except ValueError:
        await update.message.reply_text("❌ Link de justificación inválido")


# ============ REGISTRAR HANDLERS ============

def add_justification_handlers(application):
    """
    Agrega los handlers de justificaciones al bot principal.
    DEBE ser llamado desde main.py
    """
    
    # Handler principal: Captura /start con CUALQUIER parámetro
    # group=-1 = prioridad MÁS ALTA que otros handlers
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r'^/start') & filters.ChatType.PRIVATE,
            handle_start_regex
        ),
        group=-1
    )
    
    # Programar limpieza automática
    if hasattr(application, 'job_queue') and application.job_queue:
        application.job_queue.run_repeating(
            cleanup_old_messages,
            interval=60,
            first=10,
            name="cleanup_messages"
        )
        logger.info(f"⏰ Limpieza cada 60s (elimina > {AUTO_DELETE_MINUTES} min)")
    
    logger.info("✅ Handlers de justificaciones registrados")
    logger.info("   Formatos: just_X, X, j_X, p_user_X, c_id_X, n_*")
