# -*- coding: utf-8 -*-
"""
JUSTIFICATIONS HANDLER - Sistema de justificaciones protegidas
CORREGIDO: Parser de deep links con guión como separador
"""
import logging
import asyncio
import re
from typing import Optional, Dict, List
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES, TZ

logger = logging.getLogger(__name__)

# Cache de justificaciones enviadas
sent_justifications: Dict[int, Dict] = {}


async def handle_justification_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Maneja /start con parámetros de justificación.
    
    Formatos soportados:
    - /start 123 → ID directo, usa JUSTIFICATIONS_CHAT_ID
    - /start c_1234567890-123 → Canal privado
    - /start p_username-123 → Canal público (GUIÓN antes del ID)
    """
    if not update.message or not update.message.text:
        return False
    
    text = update.message.text.strip()
    
    # Debe empezar con /start
    if not text.startswith('/start'):
        return False
    
    # Extraer parámetro
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return False
    
    param = parts[1].strip()
    user_id = update.effective_user.id
    
    logger.info(f"🔍 Procesando justificación: param='{param}'")
    
    # Determinar chat_id y message_id
    chat_id = None
    message_id = None
    
    # Formato: c_CHANNELID-MSGID (canal privado)
    if param.startswith('c_'):
        # Buscar el último guión que separa el message_id
        rest = param[2:]  # Quitar 'c_'
        last_dash = rest.rfind('-')
        
        if last_dash > 0:
            channel_part = rest[:last_dash]
            msg_part = rest[last_dash + 1:]
            
            if channel_part.isdigit() and msg_part.isdigit():
                chat_id = int(f"-100{channel_part}")
                message_id = int(msg_part)
                logger.info(f"📍 Canal privado: chat_id={chat_id}, msg_id={message_id}")
    
    # Formato: p_USERNAME-MSGID (canal público)
    elif param.startswith('p_'):
        rest = param[2:]  # Quitar 'p_'
        last_dash = rest.rfind('-')
        
        if last_dash > 0:
            username = rest[:last_dash]
            msg_part = rest[last_dash + 1:]
            
            if msg_part.isdigit():
                chat_id = f"@{username}"
                message_id = int(msg_part)
                logger.info(f"📍 Canal público: chat_id={chat_id}, msg_id={message_id}")
    
    # Formato: solo número (usa JUSTIFICATIONS_CHAT_ID)
    elif param.isdigit():
        chat_id = JUSTIFICATIONS_CHAT_ID
        message_id = int(param)
        logger.info(f"📍 ID directo: chat_id={chat_id}, msg_id={message_id}")
    
    # No se pudo parsear
    if chat_id is None or message_id is None:
        logger.warning(f"⚠️ Formato no reconocido: {param}")
        await update.message.reply_text("❌ Formato inválido.")
        return True
    
    # Enviar justificación
    await send_justification(context, user_id, chat_id, message_id)
    return True


async def send_justification(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    source_chat_id,
    message_id: int
):
    """Envía la justificación al usuario"""
    try:
        # Limpiar justificaciones previas del usuario
        await clean_previous_justifications(context, user_id)
        
        # Copiar mensaje
        sent = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=source_chat_id,
            message_id=message_id,
            protect_content=True
        )
        
        logger.info(f"✅ Justificación enviada: user={user_id}, source={source_chat_id}, msg={message_id}")
        
        # Mensaje motivacional
        from justification_messages import get_weighted_random_message
        motivational = get_weighted_random_message()
        
        motiv_msg = await context.bot.send_message(
            chat_id=user_id,
            text=motivational,
            disable_notification=True
        )
        
        # Guardar referencias para auto-eliminación
        sent_justifications[user_id] = {
            'message_ids': [sent.message_id, motiv_msg.message_id],
            'sent_at': datetime.now(tz=TZ)
        }
        
        # Programar auto-eliminación
        if AUTO_DELETE_MINUTES > 0:
            asyncio.create_task(
                auto_delete_justification(context, user_id, AUTO_DELETE_MINUTES)
            )
    
    except TelegramError as e:
        logger.error(f"❌ Error enviando justificación: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ No se pudo obtener la justificación. Verifica el enlace."
        )


async def clean_previous_justifications(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Limpia justificaciones previas del usuario"""
    if user_id not in sent_justifications:
        return
    
    data = sent_justifications.pop(user_id, {})
    for msg_id in data.get('message_ids', []):
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except:
            pass


async def auto_delete_justification(context: ContextTypes.DEFAULT_TYPE, user_id: int, minutes: int):
    """Auto-elimina justificación después de X minutos"""
    await asyncio.sleep(minutes * 60)
    
    if user_id not in sent_justifications:
        return
    
    data = sent_justifications.pop(user_id, {})
    for msg_id in data.get('message_ids', []):
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except:
            pass
    
    logger.info(f"🗑️ Auto-eliminada justificación de user {user_id}")
