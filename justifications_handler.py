# -*- coding: utf-8 -*-
import logging
import re
import asyncio
import random
from typing import Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES

logger = logging.getLogger(__name__)

# Patrón para detectar %%% con ?start=just_ID o ?start=just_ID-ID-ID
# Detecta: %%% https://t.me/CUALQUIER_COSA?start=just_30
# Detecta: %%% https://t.me/CUALQUIER_COSA?start=just_30-31-32
JUSTIFICATION_PATTERN = re.compile(
    r'%%%\s*https?://t\.me/[^\s]+\?start=just_(\d+(?:-\d+)*)',
    re.IGNORECASE
)

# Cache para auto-eliminación
user_justification_messages: Dict[int, List[int]] = {}


async def handle_justification_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Detecta %%% URL?start=just_30 y convierte en botón.
    Soporta múltiples: ?start=just_30-31-32
    """
    msg = update.channel_post
    if not msg:
        return
    
    text = msg.text or msg.caption or ""
    
    match = JUSTIFICATION_PATTERN.search(text)
    if not match:
        logger.warning(f"No match para: {text}")
        return
    
    ids_string = match.group(1)  # "30" o "30-31-32"
    
    try:
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        
        # Deep link: https://t.me/BOT?start=jst_30 o jst_30-31-32
        deep_link = f"https://t.me/{bot_username}?start=jst_{ids_string}"
        
        # Botón con emoji 💬
        button = InlineKeyboardButton("Ver justificación 💬", url=deep_link)
        keyboard = InlineKeyboardMarkup([[button]])
        
        # Limpiar texto
        clean_text = JUSTIFICATION_PATTERN.sub('', text).strip()
        
        # Editar mensaje
        if msg.photo or msg.video or msg.document:
            await msg.edit_caption(
                caption=clean_text if clean_text else None,
                reply_markup=keyboard
            )
        else:
            if clean_text:
                await msg.edit_text(text=clean_text, reply_markup=keyboard)
            else:
                await msg.edit_text(text="📚 Contenido disponible:", reply_markup=keyboard)
        
        logger.info(f"✅ Botón creado: jst_{ids_string}")
        
    except TelegramError as e:
        logger.error(f"❌ Error: {e}")


async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja /start jst_30 o /start jst_30-31-32
    Extrae IDs y envía las justificaciones.
    """
    if not update.message:
        return
    
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Extraer IDs después de "jst_"
    match = re.search(r'jst_(\d+(?:-\d+)*)', text)
    if not match:
        await update.message.reply_text("❌ Link inválido")
        return
    
    ids_string = match.group(1)
    justification_ids = [int(x) for x in ids_string.split('-')]
    
    logger.info(f"🔍 Usuario {user_id} solicitó justificaciones: {justification_ids}")
    
    # Mensaje de procesamiento
    processing = await update.message.reply_text(
        "🔄 Obteniendo justificación..." if len(justification_ids) == 1 
        else f"🔄 Obteniendo {len(justification_ids)} justificaciones...",
        disable_notification=True
    )
    
    sent_messages = []
    
    for jid in justification_ids:
        try:
            copied = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=JUSTIFICATIONS_CHAT_ID,
                message_id=jid,
                protect_content=True
            )
            sent_messages.append(copied.message_id)
            
            # Pausa entre mensajes múltiples
            if len(justification_ids) > 1:
                await asyncio.sleep(0.3)
                
        except TelegramError as e:
            logger.error(f"❌ Error justificación #{jid}: {e}")
    
    # Borrar procesamiento
    await processing.delete()
    
    if not sent_messages:
        await update.message.reply_text("❌ No se encontraron las justificaciones")
        return
    
    # Mensaje motivacional
    try:
        from justification_messages import get_weighted_random_message
        motivational = get_weighted_random_message()
    except ImportError:
        motivational = "📚 ¡Justificación enviada!"
    
    # Advertencia (30% probabilidad)
    if random.random() < 0.3:
        motivational += (
            f"\n\n⚠️ **Importante:** Este mensaje se eliminará "
            f"en {AUTO_DELETE_MINUTES} minutos.\n"
            f"💾 Guárdalo en tus mensajes guardados."
        )
    
    warning_msg = await context.bot.send_message(
        chat_id=user_id,
        text=motivational,
        parse_mode="Markdown",
        disable_notification=True
    )
    
    sent_messages.append(warning_msg.message_id)
    
    # Guardar para auto-eliminación
    user_justification_messages[user_id] = sent_messages
    
    # Programar eliminación
    if AUTO_DELETE_MINUTES > 0:
        asyncio.create_task(schedule_message_deletion(context, user_id))
    
    logger.info(f"✅ {len(justification_ids)} justificación(es) enviada(s) a {user_id}")


async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Auto-elimina mensajes después de X minutos"""
    try:
        await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
        
        if user_id in user_justification_messages:
            for msg_id in user_justification_messages[user_id]:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except:
                    pass
            
            del user_justification_messages[user_id]
            logger.info(f"🗑️ Auto-eliminado para {user_id}")
    
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error auto-eliminación: {e}")


async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /test_just 30"""
    from config import ADMIN_USER_IDS
    
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("Uso: `/test_just 30`", parse_mode="Markdown")
        return
    
    try:
        msg_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID debe ser número")
        return
    
    try:
        await context.bot.copy_message(
            chat_id=update.effective_user.id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=msg_id,
            protect_content=True
        )
        await update.message.reply_text(f"✅ Justificación #{msg_id} enviada")
    except TelegramError as e:
        await update.message.reply_text(f"❌ Error: {e}")
