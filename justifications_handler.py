# -*- coding: utf-8 -*-
"""
JUSTIFICATIONS HANDLER - Sistema de entregas protegidas
MEJORADO: Mensaje cargando, elimina /start, mejor UX
"""
import logging
import asyncio
from typing import Optional, Dict
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES, TZ

logger = logging.getLogger(__name__)

# Cache de entregas enviadas
sent_justifications: Dict[int, Dict] = {}


async def handle_justification_start(update: Update, context: ContextTypes.DEFAULT_TYPE, param: str = None) -> bool:
    """
    Maneja /start con parámetros de entrega.
    
    Formatos:
    - j_123 → %%% usa JUSTIFICATIONS_CHAT_ID + chiste médico
    - d_p_USERNAME-123 → @@@ canal público + mensaje general
    - d_c_CHANNELID-123 → @@@ canal privado + mensaje general
    - 123 (legacy) → igual que j_123
    """
    if not update.message:
        return False
    
    # Si no se pasó param, intentar extraer del texto (fallback)
    if param is None:
        text = (update.message.text or "").strip()
        if not text.startswith('/start'):
            return False
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return False
        param = parts[1].strip()
    
    user_id = update.effective_user.id
    user_msg_id = update.message.message_id
    
    logger.info(f"🔍 Procesando deep link: param='{param}'")
    
    chat_id = None
    message_id = None
    is_justification = True  # True = %%% (chiste médico), False = @@@ (mensaje general)
    
    # ========== @@@ ENTREGAS (prefijo d_) ==========
    
    # d_c_CHANNELID-MSGID (canal privado)
    if param.startswith('d_c_'):
        is_justification = False
        rest = param[4:]
        last_dash = rest.rfind('-')
        
        if last_dash > 0:
            channel_part = rest[:last_dash]
            msg_part = rest[last_dash + 1:]
            
            if channel_part.isdigit() and msg_part.isdigit():
                chat_id = int(f"-100{channel_part}")
                message_id = int(msg_part)
                logger.info(f"📍 @@@ Privado: {chat_id}, msg {message_id}")
    
    # d_p_USERNAME-MSGID (canal público)
    elif param.startswith('d_p_'):
        is_justification = False
        rest = param[4:]
        last_dash = rest.rfind('-')
        
        if last_dash > 0:
            username = rest[:last_dash]
            msg_part = rest[last_dash + 1:]
            
            if msg_part.isdigit():
                message_id = int(msg_part)
                # RESOLVER username a ID usando get_chat()
                try:
                    chat_obj = await context.bot.get_chat(f"@{username}")
                    chat_id = chat_obj.id
                    logger.info(f"📍 @@@ Público: @{username} → {chat_id}, msg {message_id}")
                except TelegramError as e:
                    logger.error(f"❌ No pude resolver @{username}: {e}")
                    await update.message.reply_text(
                        f"❌ No tengo acceso al canal @{username}.\n"
                        "Asegúrate de que el bot sea admin del canal."
                    )
                    return True
    
    # ========== %%% JUSTIFICACIONES ==========
    
    # Prefijo j_ → justificación con chiste (usa JUSTIFICATIONS_CHAT_ID)
    elif param.startswith('j_'):
        is_justification = True
        msg_part = param[2:]  # Quitar 'j_'
        if msg_part.isdigit():
            chat_id = JUSTIFICATIONS_CHAT_ID
            message_id = int(msg_part)
            logger.info(f"📍 %%% Justificación (j_): msg {message_id}")
    
    # Solo número (legacy) → también justificación
    elif param.isdigit():
        is_justification = True
        chat_id = JUSTIFICATIONS_CHAT_ID
        message_id = int(param)
        logger.info(f"📍 %%% Justificación (legacy): msg {message_id}")
    
    # No reconocido
    if chat_id is None or message_id is None:
        logger.warning(f"⚠️ Formato no reconocido: {param}")
        await update.message.reply_text("❌ Enlace inválido.")
        return True
    
    # Enviar contenido (pasamos el ID del mensaje /start para eliminarlo)
    await send_content(context, user_id, chat_id, message_id, is_justification, user_msg_id)
    return True


async def send_content(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    source_chat_id: int,
    message_id: int,
    is_justification: bool = True,
    user_command_msg_id: int = None
):
    """Envía contenido al usuario"""
    loading_msg = None
    try:
        # Limpiar entregas previas (incluye /start anteriores)
        await clean_previous(context, user_id)
        
        # MENSAJE DE CARGANDO
        loading_msg = await context.bot.send_message(
            chat_id=user_id,
            text="⏳ Obteniendo contenido...",
            disable_notification=True
        )
        
        # Copiar mensaje
        sent = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=source_chat_id,
            message_id=message_id,
            protect_content=True
        )
        
        # Eliminar mensaje de cargando
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=loading_msg.message_id)
        except:
            pass
        
        logger.info(f"✅ Enviado: user={user_id}, chat={source_chat_id}, msg={message_id}, tipo={'%%%' if is_justification else '@@@'}")
        
        message_ids = [sent.message_id]
        
        # Mensaje según tipo
        if is_justification:
            # %%% → Chiste médico
            from justification_messages import get_weighted_random_message
            text = get_weighted_random_message()
        else:
            # @@@ → Mensaje general
            from justification_messages import get_general_message
            text = get_general_message()
        
        msg = await context.bot.send_message(
            chat_id=user_id,
            text=text,
            disable_notification=True
        )
        message_ids.append(msg.message_id)
        
        # TAMBIÉN guardar el ID del comando /start para eliminarlo después
        if user_command_msg_id:
            message_ids.append(user_command_msg_id)
        
        # Guardar para auto-eliminación
        sent_justifications[user_id] = {
            'message_ids': message_ids,
            'sent_at': datetime.now(tz=TZ)
        }
        
        # Auto-eliminar
        if AUTO_DELETE_MINUTES > 0:
            asyncio.create_task(auto_delete(context, user_id, AUTO_DELETE_MINUTES))
    
    except TelegramError as e:
        logger.error(f"❌ Error: {e}")
        # Intentar eliminar loading si existe
        if loading_msg:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=loading_msg.message_id)
            except:
                pass
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ No se pudo obtener el contenido."
        )


async def clean_previous(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Limpia entregas previas (contenido + mensajes + /start)"""
    if user_id not in sent_justifications:
        return
    
    data = sent_justifications.pop(user_id, {})
    for msg_id in data.get('message_ids', []):
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except:
            pass


async def auto_delete(context: ContextTypes.DEFAULT_TYPE, user_id: int, minutes: int):
    """Auto-elimina después de X minutos"""
    await asyncio.sleep(minutes * 60)
    
    if user_id not in sent_justifications:
        return
    
    data = sent_justifications.pop(user_id, {})
    for msg_id in data.get('message_ids', []):
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except:
            pass
    
    logger.info(f"🗑️ Auto-eliminado: user {user_id}")
