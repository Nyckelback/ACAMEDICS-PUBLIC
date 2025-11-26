# -*- coding: utf-8 -*-
"""
Sistema de Contenido Protegido - SIMPLIFICADO
- Detecta el canal automáticamente del link
- %%% = con chiste médico
- @@@ = sin chiste
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

# ============ CACHE DE MENSAJES PARA ELIMINAR ============
pending_deletions: Dict[int, List[Tuple[int, datetime]]] = {}
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
            logger.info(f"🧹 Limpieza: {total_deleted} mensajes eliminados")


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


# ============ RESOLVER CANAL ============

async def resolve_channel(bot, identifier: str) -> Optional[int]:
    """
    Resuelve identificador a chat_id.
    - Si es número: retorna -100{numero}
    - Si es username: consulta a Telegram
    """
    if identifier.isdigit():
        return int(f"-100{identifier}")
    
    try:
        chat = await bot.get_chat(f"@{identifier}")
        return chat.id
    except Exception as e:
        logger.error(f"❌ No se pudo resolver canal @{identifier}: {e}")
        return None


# ============ HANDLER PRINCIPAL ============

async def handle_justification_start(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE,
    param: str
) -> bool:
    """
    Maneja /start con parámetros de contenido.
    
    Formatos:
    - p_USERNAME_MSGIDS  → Canal público (71 o 71-72-73)
    - c_CHATID_MSGIDS    → Canal privado
    - n_p_USER_MSGIDS    → Sin chiste
    - n_c_CHATID_MSGIDS  → Sin chiste
    - j_MSGID            → Compatibilidad
    - MSGID (número)     → Compatibilidad
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
            
            # Parsear IDs (puede ser "71" o "71-72-73")
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
            
            # Parsear IDs
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
    """Envía contenido al usuario (puede ser múltiples mensajes)."""
    now = datetime.now(TZ)
    
    loading_msg = await context.bot.send_message(
        chat_id=user_id,
        text="⏳ Obteniendo contenido..."
    )
    
    sent_msg_ids = []
    errors = 0
    
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
                errors += 1
        
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
        
        # Agendar eliminación
        if AUTO_DELETE_MINUTES > 0:
            async with deletion_lock:
                if user_id not in pending_deletions:
                    pending_deletions[user_id] = []
                
                for mid in sent_msg_ids:
                    pending_deletions[user_id].append((mid, now))
                pending_deletions[user_id].append((companion_msg.message_id, now))
            
            logger.info(f"📝 Agendado eliminar {len(sent_msg_ids)+1} msgs en {AUTO_DELETE_MINUTES} min")
        
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
