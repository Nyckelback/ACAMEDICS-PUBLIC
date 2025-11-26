# -*- coding: utf-8 -*-
"""
ADS HANDLER - Sistema de publicidad programada
MEJORADO: Sistema inteligente - máximo 1 ad visible en el canal
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

# GLOBAL: ID del último mensaje de publicidad en el canal (compartido entre todas las ads)
LAST_AD_MESSAGE_ID: Optional[int] = None
AD_LOCK = asyncio.Lock()

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


async def delete_last_ad(bot) -> bool:
    """Elimina el último ad del canal (global, compartido entre todas las ads)"""
    global LAST_AD_MESSAGE_ID
    
    async with AD_LOCK:
        if LAST_AD_MESSAGE_ID:
            try:
                await bot.delete_message(
                    chat_id=PUBLIC_CHANNEL_ID,
                    message_id=LAST_AD_MESSAGE_ID
                )
                logger.info(f"🗑️ Ad anterior eliminado: {LAST_AD_MESSAGE_ID}")
                LAST_AD_MESSAGE_ID = None
                return True
            except Exception as e:
                logger.warning(f"No se pudo eliminar ad anterior: {e}")
                LAST_AD_MESSAGE_ID = None
    return False


async def set_last_ad(message_id: int):
    """Registra el nuevo ad como el último"""
    global LAST_AD_MESSAGE_ID
    async with AD_LOCK:
        LAST_AD_MESSAGE_ID = message_id


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
    """Detiene TODA la publicidad activa"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    count = 0
    
    # Cancelar TODAS las tareas de ads
    for uid in list(ads_tasks.keys()):
        if uid in ads_tasks:
            ads_tasks[uid].cancel()
            del ads_tasks[uid]
            count += 1
    
    # Limpiar configuraciones
    active_ads.clear()
    context.user_data.clear()
    
    # Eliminar último ad del canal
    await delete_last_ad(context.bot)
    
    await update.message.reply_text(f"🛑 Publicidad detenida. ({count} ads cancelados)")


async def cmd_list_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista las ads activas"""
    if not is_admin(update.effective_user.id):
        return
    
    if not active_ads:
        await update.message.reply_text("📭 No hay publicidad activa.")
        return
    
    lines = ["📢 **ADS ACTIVAS:**\n"]
    for uid, data in active_ads.items():
        minutes = data.get('interval_minutes', 0)
        if minutes >= 60:
            time_str = f"{minutes // 60}h"
        else:
            time_str = f"{minutes}m"
        lines.append(f"• Cada {time_str}")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
            f"💡 Si activas otra ad, ambas se turnarán\n"
            f"🔁 Máximo 1 ad visible (se reemplazan)\n\n"
            f"Usa **/stop_ads** para detener todas",
            parse_mode="Markdown"
        )
        return True
    
    return False


async def ads_loop(context: ContextTypes.DEFAULT_TYPE, user_id: int, content: dict, interval_minutes: int):
    """Loop de publicidad - INTELIGENTE: borra ad anterior antes de enviar"""
    try:
        while True:
            # SIEMPRE borrar el ad anterior (de CUALQUIER ads) antes de enviar
            await delete_last_ad(context.bot)
            
            # Pequeña pausa para evitar rate limits
            await asyncio.sleep(0.5)
            
            # Enviar nuevo anuncio
            try:
                sent = await context.bot.copy_message(
                    chat_id=PUBLIC_CHANNEL_ID,
                    from_chat_id=content['chat_id'],
                    message_id=content['msg_id']
                )
                # Registrar como último ad global
                await set_last_ad(sent.message_id)
                logger.info(f"📢 Ad enviado: {sent.message_id} (cada {interval_minutes}m)")
            except Exception as e:
                logger.error(f"Error enviando anuncio: {e}")
            
            # Esperar intervalo
            await asyncio.sleep(interval_minutes * 60)
    
    except asyncio.CancelledError:
        logger.info(f"🛑 Loop de ads cancelado para user {user_id}")
        # NO borrar el ad aquí - se borra en cmd_stop_ads si el usuario quiere
