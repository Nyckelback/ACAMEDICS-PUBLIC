# -*- coding: utf-8 -*-
"""
Sistema de Contenido Protegido - OPTIMIZADO
- Cache de canales (respuesta rápida)
- Borra mensaje anterior al enviar nuevo
- Eliminación en batch cada AUTO_DELETE_MINUTES
"""

import logging
import asyncio
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from config import AUTO_DELETE_MINUTES, JUSTIFICATIONS_CHAT_ID, TZ

logger = logging.getLogger(__name__)

# ============ CACHE ============
# Cache de canales: {"username": chat_id}
channel_cache: Dict[str, int] = {}

# Mensajes pendientes de eliminar: {user_id: [(msg_id, timestamp), ...]}
pending_deletions: Dict[int, List[Tuple[int, datetime]]] = {}

# Último contenido enviado por usuario (para borrar al enviar nuevo)
last_sent: Dict[int, List[int]] = {}

deletion_lock = asyncio.Lock()


# ============ LIMPIEZA PERIÓDICA ============

async def cleanup_old_messages(context: ContextTypes.DEFAULT_TYPE):
    """Elimina mensajes viejos en batch cada minuto."""
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
            
            if to_delete:
                async def delete_msg(uid, mid):
                    try:
                        await context.bot.delete_message(chat_id=uid, message_id=mid)
                        return True
                    except:
                        return False
                
                results = await asyncio.gather(
                    *[delete_msg(user_id, mid) for mid in to_delete],
                    return_exceptions=True
                )
                total_deleted += sum(1 for r in results if r is True)
        
        if total_deleted > 0:
            logger.info(f"🧹 Limpieza batch: {total_deleted} mensajes eliminados")


def schedule_cleanup_task(app):
    """Programa la tarea de limpieza periódica."""
    if AUTO_DELETE_MINUTES > 0:
        app.job_queue.run_repeating(
            cleanup_old_messages,
            interval=60,
            first=10,
            name="cleanup_messages"
        )
        logger.info(f"⏰ Limpieza automática cada 60s (elimina > {AUTO_DELETE_MINUTES} min)")


# ============ RESOLVER CANAL (CON CACHE) ============

async def resolve_channel(bot, identifier: str) -> Optional[int]:
    """
    Resuelve identificador a chat_id CON CACHE.
    - Si es número: retorna -100{numero}
    - Si es username: consulta a Telegram (solo primera vez)
    """
    # Si es número, no necesita cache
    if identifier.isdigit():
        return int(f"-100{identifier}")
    
    # Buscar en cache
    if identifier in channel_cache:
        return channel_cache[identifier]
    
    # No está en cache, consultar a Telegram
    try:
        chat = await bot.get_chat(f"@{identifier}")
        channel_cache[identifier] = chat.id
        logger.info(f"📦 Cache: @{identifier} → {chat.id}")
        return chat.id
    except Exception as e:
        logger.error(f"❌ No se pudo resolver canal @{identifier}: {e}")
        return None


# ============ BORRAR MENSAJES ANTERIORES ============

async def _delete_msgs_background(bot, user_id: int, msg_ids: List[int]):
    """Función auxiliar para borrar en background."""
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=user_id, message_id=mid)
        except:
            pass


async def delete_previous(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Borra los mensajes anteriores del usuario."""
    if user_id not in last_sent:
        return
    
    msg_ids = last_sent.pop(user_id, [])
    if not msg_ids:
        return
    
    # Ejecutar en background sin esperar
    asyncio.create_task(_delete_msgs_background(context.bot, user_id, msg_ids))


# ============ HANDLER PRINCIPAL ============

async def handle_justification_start(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE,
    param: str
) -> bool:
    """
    Maneja /start con parámetros de contenido.
    """
    user_id = update.effective_user.id
    
    # ========== COMPATIBILIDAD: Solo número ==========
    if param.isdigit():
        logger.info(f"📥 Compat: msg={param} → JUSTIFICATIONS")
        await send_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [int(param)], True)
        return True
    
    # ========== COMPATIBILIDAD: j_MSGID ==========
    if param.startswith('j_'):
        try:
            message_id = int(param[2:])
            logger.info(f"📥 Compat j_: msg={message_id} → JUSTIFICATIONS")
            await send_content(context, user_id, JUSTIFICATIONS_CHAT_ID, [message_id], True)
            return True
        except:
            pass
    
    # ========== NUEVO FORMATO ==========
    with_joke = True
    working_param = param
    
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
            
            logger.info(f"📥 Público: @{username} → chat={chat_id}, msgs={message_ids}")
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
            
            logger.info(f"📥 Privado: chat={chat_id}, msgs={message_ids}")
            await send_content(context, user_id, chat_id, message_ids, with_joke)
            return True
        
    except (ValueError, IndexError) as e:
        logger.warning(f"⚠️ Parámetro inválido: {param} → {e}")
        await update.message.reply_text("❌ Enlace inválido")
        return True
    
    return False


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
    
    # PRIMERO: Borrar mensajes anteriores
    await delete_previous(context, user_id)
    
    loading_msg = await context.bot.send_message(
        chat_id=user_id,
        text="⏳ Obteniendo contenido..."
    )
    
    sent_msg_ids = []
    
    try:
        # Enviar cada mensaje
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
        
        # Eliminar "cargando"
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
            from justification_messages import get_random_message
            text = get_random_message()
        else:
            text = "📦 ¡Contenido entregado!"
        
        companion_msg = await context.bot.send_message(
            chat_id=user_id,
            text=text
        )
        sent_msg_ids.append(companion_msg.message_id)
        
        # Guardar para borrar cuando pida otro
        last_sent[user_id] = sent_msg_ids.copy()
        
        # También agendar para batch (por si no pide otro)
        if AUTO_DELETE_MINUTES > 0:
            async with deletion_lock:
                if user_id not in pending_deletions:
                    pending_deletions[user_id] = []
                
                for mid in sent_msg_ids:
                    pending_deletions[user_id].append((mid, now))
            
            logger.info(f"📝 {len(sent_msg_ids)} msgs enviados, agendados para eliminar")
        
    except Exception as e:
        logger.error(f"❌ Error enviando contenido: {e}")
        
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=loading_msg.message_id)
        except:
            pass
        
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ No se pudo obtener el contenido. El enlace puede ser inválido."
        )
