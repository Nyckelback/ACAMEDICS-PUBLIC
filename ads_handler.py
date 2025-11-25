# -*- coding: utf-8 -*-
"""
ADS HANDLER - Sistema de publicidad programada
CORREGIDO: Cancelación cruzada con /lote
"""
import logging
import asyncio
import re
from typing import Dict, Optional
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS, TZ

logger = logging.getLogger(__name__)

# Estado de ads activos
active_ads: Dict[int, Dict] = {}
ads_tasks: Dict[int, asyncio.Task] = {}

# Estados de configuración
ADS_STATE_WAITING_CONTENT = 'waiting_content'
ADS_STATE_WAITING_INTERVAL = 'waiting_interval'


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def parse_interval(text: str) -> Optional[int]:
    """
    Parsea intervalo de tiempo. Retorna minutos.
    
    Formatos soportados:
    - 5m, 5min, 5 minutos → 5 minutos
    - 1h, 1hr, 1 hora → 60 minutos
    - 8 (solo número) → 8 horas (legacy) → 480 minutos
    """
    text = text.strip().lower()
    
    # Minutos: 5m, 5min, 5 minutos
    match = re.match(r'^(\d+)\s*(m|min|minutos?)$', text)
    if match:
        return int(match.group(1))
    
    # Horas: 1h, 1hr, 1 hora, 2 horas
    match = re.match(r'^(\d+)\s*(h|hr|horas?)$', text)
    if match:
        return int(match.group(1)) * 60
    
    # Solo número = horas (legacy)
    if text.isdigit():
        return int(text) * 60
    
    return None


async def cmd_set_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia configuración de publicidad"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    # CANCELAR cualquier proceso previo
    context.user_data.clear()
    
    # Desactivar modo lote si estaba activo
    try:
        from batch_handler import batch_mode, active_batches
        batch_mode[user_id] = False
        active_batches.pop(user_id, None)
    except:
        pass
    
    # Iniciar configuración de ads
    context.user_data['ads_state'] = ADS_STATE_WAITING_CONTENT
    
    await update.message.reply_text(
        "📢 **CONFIGURAR PUBLICIDAD**\n\n"
        "Envía el contenido del anuncio (texto, imagen, etc.)\n\n"
        "**/cancelar** para salir",
        parse_mode="Markdown"
    )


async def cmd_stop_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detiene publicidad activa"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    # Cancelar tarea si existe
    if user_id in ads_tasks:
        ads_tasks[user_id].cancel()
        del ads_tasks[user_id]
    
    if user_id in active_ads:
        del active_ads[user_id]
    
    context.user_data.clear()
    
    await update.message.reply_text("🛑 Publicidad detenida.")


async def handle_ads_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Procesa mensajes durante configuración de ads"""
    user_id = update.effective_user.id
    
    ads_state = context.user_data.get('ads_state')
    if not ads_state:
        return False
    
    msg = update.message
    text = msg.text or ""
    
    # PASO 1: Esperando contenido
    if ads_state == ADS_STATE_WAITING_CONTENT:
        # Guardar referencia del mensaje
        context.user_data['ads_content'] = {
            'chat_id': msg.chat_id,
            'msg_id': msg.message_id
        }
        context.user_data['ads_state'] = ADS_STATE_WAITING_INTERVAL
        
        await msg.reply_text(
            "⏰ **¿Cada cuánto se repite?**\n\n"
            "Ejemplos:\n"
            "• `5m` o `5 min` → 5 minutos\n"
            "• `1h` o `1 hora` → 1 hora\n"
            "• `8` → 8 horas (legacy)\n\n"
            "**/cancelar** para salir",
            parse_mode="Markdown"
        )
        return True
    
    # PASO 2: Esperando intervalo
    if ads_state == ADS_STATE_WAITING_INTERVAL:
        minutes = parse_interval(text)
        
        if not minutes:
            await msg.reply_text("❌ Formato inválido. Usa: `5m`, `30min`, `1h`, `8h`", parse_mode="Markdown")
            return True
        
        # Guardar configuración
        content = context.user_data.get('ads_content', {})
        
        active_ads[user_id] = {
            'content': content,
            'interval_minutes': minutes,
            'last_sent': None
        }
        
        # Limpiar estado
        context.user_data.clear()
        
        # Iniciar tarea de publicidad
        task = asyncio.create_task(
            ads_loop(context, user_id, content, minutes)
        )
        ads_tasks[user_id] = task
        
        # Formatear tiempo para mostrar
        if minutes >= 60:
            time_str = f"{minutes // 60}h"
            if minutes % 60:
                time_str += f" {minutes % 60}m"
        else:
            time_str = f"{minutes}m"
        
        await msg.reply_text(
            f"✅ **PUBLICIDAD ACTIVADA**\n\n"
            f"⏰ Intervalo: cada **{time_str}**\n"
            f"🔄 Primer envío: ahora\n\n"
            f"Usa **/stop_ads** para detener",
            parse_mode="Markdown"
        )
        return True
    
    return False


async def ads_loop(context: ContextTypes.DEFAULT_TYPE, user_id: int, content: dict, interval_minutes: int):
    """Loop de publicidad"""
    try:
        last_ad_message_id = None
        
        while True:
            # Eliminar anuncio anterior si existe
            if last_ad_message_id:
                try:
                    await context.bot.delete_message(
                        chat_id=PUBLIC_CHANNEL_ID,
                        message_id=last_ad_message_id
                    )
                except:
                    pass
            
            # Enviar nuevo anuncio
            try:
                sent = await context.bot.copy_message(
                    chat_id=PUBLIC_CHANNEL_ID,
                    from_chat_id=content['chat_id'],
                    message_id=content['msg_id']
                )
                last_ad_message_id = sent.message_id
                logger.info(f"📢 Anuncio enviado: {sent.message_id}")
            except Exception as e:
                logger.error(f"Error enviando anuncio: {e}")
            
            # Esperar intervalo
            await asyncio.sleep(interval_minutes * 60)
    
    except asyncio.CancelledError:
        # Eliminar último anuncio al cancelar
        if last_ad_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=PUBLIC_CHANNEL_ID,
                    message_id=last_ad_message_id
                )
            except:
                pass
        logger.info(f"🛑 Loop de ads cancelado para user {user_id}")
