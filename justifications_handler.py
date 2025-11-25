# -*- coding: utf-8 -*-
import logging
import re
import asyncio
import random
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES

logger = logging.getLogger(__name__)

# Patrón para detectar %%% links
# Formato: %%% https://t.me/just_clinicase/6
JUSTIFICATION_PATTERN = re.compile(
    r'%%%\s*https?://t\.me/[^/]+/(\d+)',
    re.IGNORECASE
)

# Cache para mensajes enviados (para auto-eliminación)
user_justification_messages: Dict[int, List[int]] = {}


async def handle_justification_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Detecta mensajes con %%% y los convierte en botones de justificación.
    
    Entrada: %%% https://t.me/just_clinicase/6
    Salida: Botón "Ver justificación 📚" con deep link https://t.me/BOT?start=jst_6
    """
    msg = update.channel_post
    if not msg:
        return
    
    text = msg.text or msg.caption or ""
    
    # Buscar patrón %%%
    match = JUSTIFICATION_PATTERN.search(text)
    if not match:
        return
    
    # Extraer el ID del mensaje de justificación
    justification_id = match.group(1)
    
    try:
        # Obtener username del bot para crear deep link
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        
        # Crear deep link con formato: https://t.me/BOT?start=jst_ID
        deep_link = f"https://t.me/{bot_username}?start=jst_{justification_id}"
        
        # Crear botón
        button = InlineKeyboardButton("Ver justificación 📚", url=deep_link)
        keyboard = InlineKeyboardMarkup([[button]])
        
        # Limpiar el texto (remover %%% y el link completo)
        clean_text = JUSTIFICATION_PATTERN.sub('', text).strip()
        
        # Editar el mensaje según el tipo de contenido
        if msg.photo or msg.video or msg.document:
            # Si tiene multimedia, editar el caption
            await msg.edit_caption(
                caption=clean_text if clean_text else None,
                reply_markup=keyboard
            )
        else:
            # Si es solo texto
            if clean_text:
                await msg.edit_text(
                    text=clean_text,
                    reply_markup=keyboard
                )
            else:
                # Si solo era el %%% sin texto adicional
                await msg.edit_text(
                    text="📚 Contenido disponible:",
                    reply_markup=keyboard
                )
        
        logger.info(f"✅ Botón de justificación creado: jst_{justification_id} → {deep_link}")
        
    except TelegramError as e:
        logger.error(f"❌ Error creando botón de justificación: {e}")
    except Exception as e:
        logger.error(f"❌ Error inesperado: {e}")


async def handle_justification_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja solicitudes de justificación cuando el usuario hace clic en el deep link.
    
    El usuario hace clic en: https://t.me/BOT?start=jst_6
    Telegram envía al bot: /start jst_6
    El bot extrae el ID (6) y copia el mensaje del canal de justificaciones.
    """
    if not update.message:
        return
    
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Verificar que sea un deep link de justificación
    # Formato esperado: /start jst_6
    if not text.startswith("/start jst_"):
        return False
    
    # Extraer el ID del mensaje
    try:
        justification_id = int(text.replace("/start jst_", ""))
    except ValueError:
        await update.message.reply_text("❌ Link de justificación inválido")
        return True
    
    logger.info(f"🔍 Usuario {user_id} solicitó justificación #{justification_id}")
    
    # Mensaje de procesamiento
    processing = await update.message.reply_text(
        "🔄 Obteniendo justificación...",
        disable_notification=True
    )
    
    try:
        # Copiar el mensaje desde el canal de justificaciones
        copied = await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=justification_id,
            protect_content=True  # Proteger contenido (no se puede reenviar)
        )
        
        # Borrar mensaje de procesamiento
        await processing.delete()
        
        # Enviar mensaje motivacional
        try:
            from justification_messages import get_weighted_random_message
            motivational = get_weighted_random_message()
        except ImportError:
            motivational = "📚 ¡Justificación enviada exitosamente!"
        
        # Advertencia sobre auto-eliminación (30% de probabilidad)
        show_warning = random.random() < 0.3
        
        if show_warning:
            warning_text = (
                f"{motivational}\n\n"
                f"⚠️ **Importante:** Este mensaje se eliminará automáticamente "
                f"en {AUTO_DELETE_MINUTES} minutos.\n"
                f"💾 Guárdalo en tus mensajes guardados si lo necesitas."
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
        if AUTO_DELETE_MINUTES > 0:
            asyncio.create_task(
                schedule_message_deletion(context, user_id, AUTO_DELETE_MINUTES)
            )
        
        logger.info(f"✅ Justificación #{justification_id} enviada a usuario {user_id}")
        return True
        
    except TelegramError as e:
        await processing.delete()
        error_msg = str(e).lower()
        
        if "not found" in error_msg or "message to copy not found" in error_msg:
            await update.message.reply_text(
                "❌ Justificación no encontrada.\n"
                "Puede que el mensaje haya sido eliminado del canal."
            )
        else:
            await update.message.reply_text(f"❌ Error al obtener la justificación: {e}")
        
        logger.error(f"❌ Error enviando justificación #{justification_id}: {e}")
        return True


async def schedule_message_deletion(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    minutes: int
):
    """Programa la auto-eliminación de mensajes después de X minutos"""
    try:
        await asyncio.sleep(minutes * 60)
        
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
    
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error en auto-eliminación: {e}")


async def cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando de prueba para admins: /test_just 6
    Prueba directamente la entrega de una justificación
    """
    from config import ADMIN_USER_IDS
    
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    
    if not context.args:
        await update.message.reply_text(
            "**Uso:** `/test_just ID`\n\n"
            "**Ejemplo:** `/test_just 6`\n\n"
            "Esto enviará el mensaje #6 del canal de justificaciones.",
            parse_mode="Markdown"
        )
        return
    
    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID debe ser un número")
        return
    
    user_id = update.effective_user.id
    
    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=JUSTIFICATIONS_CHAT_ID,
            message_id=message_id,
            protect_content=True
        )
        
        await update.message.reply_text(
            f"✅ Justificación #{message_id} enviada como prueba"
        )
        
    except TelegramError as e:
        await update.message.reply_text(f"❌ Error: {e}")
