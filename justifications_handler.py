# -*- coding: utf-8 -*-
import logging
import asyncio
import re
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES

logger = logging.getLogger(__name__)

# Cache para auto-eliminación
user_justification_messages = {}

def parse_message_ids(text: str) -> list:
    """Parsea IDs de mensajes. Soporta: 22, 22-25, 22,23,24"""
    ids = []
    if not text:
        return ids
    
    # Solo permitir dígitos, guiones y comas
    if not re.match(r'^[\d,\-]+$', text):
        return ids
    
    parts = text.replace(',', '-').split('-')
    for p in parts:
        p = p.strip()
        if p.isdigit():
            ids.append(int(p))
    
    return ids

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Procesa solicitudes de contenido.
    Formatos:
    - /start 22 (usa JUSTIFICATIONS_CHAT_ID)
    - /start c_1234567890_22 (canal privado específico)  
    - /start @username_22 (canal público)
    - /start 22-25 (rango)
    """
    if not update.message: 
        return
    
    user_id = update.effective_user.id
    
    # Obtener argumento
    raw_arg = ""
    if context.args and len(context.args) > 0:
        raw_arg = context.args[0]
    else:
        text = update.message.text or ""
        parts = text.split()
        if len(parts) > 1:
            raw_arg = parts[1]
    
    if not raw_arg:
        return
    
    logger.info(f"📥 Solicitud: user={user_id}, arg='{raw_arg}'")
    
    # Determinar canal y mensajes
    chat_id = JUSTIFICATIONS_CHAT_ID
    message_ids = []
    
    # Formato: c_CHANNELID_MSGID (canal privado)
    if raw_arg.startswith('c_'):
        parts = raw_arg.split('_')
        if len(parts) >= 3:
            chat_id = int(f"-100{parts[1]}")
            msg_part = '_'.join(parts[2:])
            message_ids = parse_message_ids(msg_part)
    
    # Formato: @username_MSGID (canal público)
    elif raw_arg.startswith('@'):
        parts = raw_arg.split('_', 1)
        if len(parts) >= 2:
            chat_id = parts[0]
            message_ids = parse_message_ids(parts[1])
    
    # Formato simple: solo IDs
    else:
        clean = raw_arg
        for prefix in ['jst_', 'just_', 'j_']:
            clean = clean.replace(prefix, '')
        message_ids = parse_message_ids(clean)
    
    if not message_ids:
        logger.error(f"No se extrajeron IDs de: '{raw_arg}'")
        await update.message.reply_text("❌ Formato inválido.")
        return
    
    logger.info(f"📋 Entregando {message_ids} de {chat_id}")
    
    await deliver_content(update, context, user_id, chat_id, message_ids)

async def deliver_content(update, context, user_id: int, chat_id, message_ids: list):
    """Entrega contenido al usuario"""
    
    processing = await update.message.reply_text("🔄 Buscando...")
    
    sent_msgs = []
    errors = []
    
    for msg_id in message_ids:
        try:
            logger.info(f"📤 Copiando {msg_id} de {chat_id} → {user_id}")
            
            msg = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=chat_id,
                message_id=msg_id,
                protect_content=True
            )
            sent_msgs.append(msg.message_id)
            logger.info(f"✅ Mensaje {msg_id} entregado")
            
        except TelegramError as e:
            error_str = str(e).lower()
            logger.error(f"❌ Error copiando {msg_id}: {e}")
            
            if "not found" in error_str:
                errors.append(f"ID {msg_id}: No existe")
            elif "chat not found" in error_str:
                errors.append(f"Canal no accesible")
            else:
                errors.append(f"ID {msg_id}: Error")
                
        except Exception as e:
            logger.exception(f"❌ Error: {e}")
            errors.append(f"ID {msg_id}: Error")
    
    try:
        await processing.delete()
    except:
        pass
    
    if errors and not sent_msgs:
        await update.message.reply_text(
            f"❌ **No se pudo entregar:**\n" + "\n".join(errors),
            parse_mode="Markdown"
        )
        return
    
    if sent_msgs:
        try:
            from justification_messages import get_weighted_random_message
            txt = get_weighted_random_message()
        except:
            txt = "📚 Contenido entregado."
        
        txt += f"\n\n⚠️ *Se borra en {AUTO_DELETE_MINUTES} min.*"
        
        avis = await context.bot.send_message(user_id, txt, parse_mode="Markdown")
        sent_msgs.append(avis.message_id)
        
        user_justification_messages[user_id] = sent_msgs
        asyncio.create_task(schedule_del(context, user_id))

async def schedule_del(context, user_id):
    await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
    
    if user_id in user_justification_messages:
        for mid in user_justification_messages[user_id]:
            try:
                await context.bot.delete_message(user_id, mid)
            except:
                pass
        del user_justification_messages[user_id]
