# -*- coding: utf-8 -*-
import logging
import re
import asyncio
from typing import Optional, Dict, List
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES, TZ

logger = logging.getLogger(__name__)

# Patrón para detectar %%% links
JUSTIFICATION_PATTERN = re.compile(
    r'%%%\s*https?://t\.me/[^/]+/(\d+)',
    re.IGNORECASE
)

# Cache para mensajes enviados (para auto-eliminación)
user_justification_messages: Dict[int, List[int]] = {}

async def handle_justification_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Detecta mensajes con %%% y los convierte en botones de justificación.
    Formato: %%% https://t.me/canal/123
    """
    msg = update.channel_post
    if not msg:
        return
    
    text = msg.text or msg.caption or ""
    
    # Buscar patrón %%%
    match = JUSTIFICATION_PATTERN.search(text)
    if not match:
        return
    
    message_id = match.group(1)
    
    try:
        # Obtener info del bot para crear deep link
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        
        # Crear deep link
        deep_link = f"https://t.me/{bot_username}?start=just_{message_id}"
        
        # Crear botón
        button = InlineKeyboardButton("Ver justificación 📚", url=deep_link)
        keyboard = InlineKeyboardMarkup([[button]])
        
        # Limpiar el texto (remover %%% link)
        clean_text = JUSTIFICATION_PATTERN.sub('', text).strip()
        
        # Si el mensaje original tiene contenido multimedia, editarlo
        if msg.photo or msg.video or msg.document:
            await msg.edit_caption(
                caption=clean_text if clean_text else None,
                reply_markup=keyboard
            )
        else:
            # Si es solo texto, editar el texto
            if clean_text:
                await msg.edit_text(
                    text=clean_text,
                    reply_markup=keyboard
                )
            else:
                # Si no hay texto adicional, eliminar el mensaje %%% y enviar uno nuevo con botón
                await msg.delete()
                await context.bot.send_message(
                    chat_id=msg.chat_id,
                    text="📚 Contenido disponible:",
                    reply_markup=keyboard
                )
        
        logger.info(f"✅ Botón de justificación creado para mensaje {message_id}")
        
    except Exception as e:
        logger.error(f"❌ Error creando botón de justificación: {e}")

async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja solicitudes de justificación vía deep link.
    Formato: /start just_123
    """
    if not update.message:
        return
    
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Extraer ID del mensaje
    if not text.startswith("/start just_"):
        return
    
    try:
        message_id = int(text.replace("/start just_", ""))
    except ValueError:
        await update.message.reply_text("❌ Link de justificación inválido")
        return
    
    logger.info(f"🔍 Usuario {user_id} solicitó justificación {message_id}")
    
    # Mensaje de procesamiento
    processing = await update.message.reply_text(
        "🔄 Obteniendo justificación...",
        disable_notification=True
    )
    
    try:
        # Intentar copiar el mensaje del canal de justificaciones
        copied = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id,
            protect_content=True
        )
        
        # Borrar mensaje de procesamiento
        await processing.delete()
        
        # Enviar mensaje motivacional
        try:
            from justification_messages import get_weighted_random_message
            motivational = get_weighted_random_message()
        except ImportError:
            motivational = "📚 Justificación enviada exitosamente"
        
        # Advertencia ocasional sobre auto-eliminación (30% de probabilidad)
        import random
        show_warning = random.random() < 0.3
        
        if show_warning:
            warning_text = (
                f"{motivational}\n\n"
                f"⚠️ **Importante:** Este mensaje se auto-eliminará en {AUTO_DELETE_MINUTES} minutos. "
                f"Guárdalo en tus mensajes guardados si lo necesitas."
            )
        else:
            warning_text = motivational
        
        warning_msg = await context.bot.send_message(
            chat_id=user_id,
            text=warning_text,
            parse_mode="Markdown",
            disable_notification=True
        )
        
        # Guardar IDs para auto-eliminación
        if user_id not in user_justification_messages:
            user_justification_messages[user_id] = []
        
        user_justification_messages[user_id].append(copied.message_id)
        user_justification_messages[user_id].append(warning_msg.message_id)
        
        # Programar auto-eliminación
        await schedule_message_deletion(context, user_id)
        
        logger.info(f"✅ Justificación {message_id} enviada a usuario {user_id}")
        
    except TelegramError as e:
        await processing.delete()
        error_msg = str(e).lower()
        
        if "not found" in error_msg or "message to copy not found" in error_msg:
            await update.message.reply_text(
                "❌ Justificación no encontrada. Puede que el mensaje haya sido eliminado."
            )
        else:
            await update.message.reply_text(
                f"❌ Error al obtener la justificación: {e}"
            )
        
        logger.error(f"❌ Error enviando justificación {message_id}: {e}")

async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Programa la auto-eliminación de mensajes"""
    async def delete_messages():
        try:
            await asyncio.sleep(AUTO_DELETE_MINUTES * 60)
            
            if user_id in user_justification_messages:
                for msg_id in user_justification_messages[user_id]:
                    try:
                        await context.bot.delete_message(
                            chat_id=user_id,
                            message_id=msg_id
                        )
                    except:
                        pass
                
                del user_justification_messages[user_id]
                logger.info(f"🗑️ Mensajes auto-eliminados para usuario {user_id}")
        
        except Exception as e:
            logger.error(f"Error en auto-eliminación: {e}")
    
    asyncio.create_task(delete_messages())

async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando de prueba para admins: /test_just 123
    """
    from config import ADMIN_USER_IDS
    
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("Uso: /test_just <message_id>")
        return
    
    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID inválido")
        return
    
    user_id = update.effective_user.id
    
    try:
        copied = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id,
            protect_content=True
        )
        
        await update.message.reply_text(f"✅ Justificación {message_id} enviada como prueba")
        
    except TelegramError as e:
        await update.message.reply_text(f"❌ Error: {e}")
