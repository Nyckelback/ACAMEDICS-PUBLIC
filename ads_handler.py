# -*- coding: utf-8 -*-
"""
ADS HANDLER - Sistema de publicidad programada
MEJORADO: Stop individual, mejor listado con snippet
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

# Estado de ads activos - ahora con ID único
active_ads: Dict[int, Dict] = {}  # {ad_id: {content, interval, task, snippet}}
ads_tasks: Dict[int, asyncio.Task] = {}

# Contador global para IDs únicos
AD_ID_COUNTER = 0

# GLOBAL: ID del último mensaje de publicidad en el canal
LAST_AD_MESSAGE_ID: Optional[int] = None
AD_LOCK = asyncio.Lock()

# Estados de configuración
ADS_STATE_WAITING_CONTENT = 'waiting_content'
ADS_STATE_WAITING_INTERVAL = 'waiting_interval'


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def get_next_ad_id() -> int:
    """Genera un ID único para cada ad"""
    global AD_ID_COUNTER
    AD_ID_COUNTER += 1
    return AD_ID_COUNTER


def parse_interval(text: str) -> Optional[int]:
    """
    Parsea intervalo de tiempo. Retorna minutos.
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
    """Elimina el último ad del canal"""
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
    """Detiene TODA la publicidad activa o una específica"""
    if not is_admin(update.effective_user.id):
        return
    
    # Verificar si hay argumento (ID específico)
    text = update.message.text or ""
    parts = text.split()
    
    if len(parts) > 1:
        # Stop individual
        try:
            ad_id = int(parts[1])
            if ad_id in active_ads:
                # Cancelar tarea
                if ad_id in ads_tasks:
                    ads_tasks[ad_id].cancel()
                    del ads_tasks[ad_id]
                
                # Eliminar de active_ads
                del active_ads[ad_id]
                
                await update.message.reply_text(f"✅ Ad #{ad_id} detenida.")
                return
            else:
                await update.message.reply_text(f"❌ No existe ad #{ad_id}. Usa `/list_ads` para ver las activas.", parse_mode="Markdown")
                return
        except ValueError:
            pass
    
    # Stop todas
    count = len(active_ads)
    
    # Cancelar todas las tareas
    for ad_id in list(ads_tasks.keys()):
        ads_tasks[ad_id].cancel()
        del ads_tasks[ad_id]
    
    # Limpiar configuraciones
    active_ads.clear()
    context.user_data.clear()
    
    # Eliminar último ad del canal
    await delete_last_ad(context.bot)
    
    await update.message.reply_text(f"🛑 {count} ads detenidas.")


async def cmd_list_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista las ads activas con ID y snippet"""
    if not is_admin(update.effective_user.id):
        return
    
    if not active_ads:
        await update.message.reply_text("📭 No hay publicidad activa.")
        return
    
    lines = ["📢 **ADS ACTIVAS:**\n"]
    
    for ad_id, data in sorted(active_ads.items()):
        minutes = data.get('interval_minutes', 0)
        snippet = data.get('snippet', '')[:30]  # Max 30 caracteres
        
        # Formatear tiempo
        if minutes >= 60:
            time_str = f"{minutes // 60}h"
            if minutes % 60:
                time_str += f" {minutes % 60}m"
        else:
            time_str = f"{minutes}m"
        
        lines.append(f"• **#{ad_id}** — Cada {time_str}")
        if snippet:
            lines.append(f"  `{snippet}...`")
    
    lines.append(f"\n💡 Para detener una: `/stop_ads ID`")
    lines.append(f"💡 Para detener todas: `/stop_ads`")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_ads_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Procesa mensajes durante configuración de ads"""
    user_id = update.effective_user.id
    
    ads_state = context.user_data.get('ads_state')
    if not ads_state:
        return False
    
    msg = update.message
    text = msg.text or msg.caption or ""
    
    # PASO 1: Esperando contenido
    if ads_state == ADS_STATE_WAITING_CONTENT:
        # Guardar referencia del mensaje y texto
        context.user_data['ads_content'] = {
            'chat_id': msg.chat_id,
            'msg_id': msg.message_id,
            'original_text': text
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
        
        # Generar ID único para esta ad
        ad_id = get_next_ad_id()
        
        # Obtener contenido
        content = context.user_data.get('ads_content', {})
        original_text = content.get('original_text', '')
        
        # Crear snippet para mostrar en listado
        snippet = original_text.replace('\n', ' ')[:40] if original_text else "(media)"
        
        # Guardar configuración
        active_ads[ad_id] = {
            'content': content,
            'interval_minutes': minutes,
            'snippet': snippet,
            'created_at': datetime.now(tz=TZ)
        }
        
        # Limpiar estado
        context.user_data.clear()
        
        # Iniciar tarea de publicidad
        task = asyncio.create_task(
            ads_loop(context, ad_id, content, minutes)
        )
        ads_tasks[ad_id] = task
        
        # Formatear tiempo para mostrar
        if minutes >= 60:
            time_str = f"{minutes // 60}h"
            if minutes % 60:
                time_str += f" {minutes % 60}m"
        else:
            time_str = f"{minutes}m"
        
        await msg.reply_text(
            f"✅ **PUBLICIDAD #{ad_id} ACTIVADA**\n\n"
            f"⏰ Intervalo: cada **{time_str}**\n"
            f"📝 Contenido: `{snippet[:20]}...`\n"
            f"🔄 Primer envío: ahora\n\n"
            f"🔁 Máximo 1 ad visible (se reemplazan)\n\n"
            f"Usa `/stop_ads {ad_id}` para detener esta\n"
            f"Usa `/stop_ads` para detener todas",
            parse_mode="Markdown"
        )
        return True
    
    return False


async def ads_loop(context: ContextTypes.DEFAULT_TYPE, ad_id: int, content: dict, interval_minutes: int):
    """Loop de publicidad - Procesa botones @@@ y %%%"""
    from batch_handler import (
        build_buttons, has_special_syntax, clean_special_syntax
    )
    
    try:
        # Obtener username del bot
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
        
        # Texto original
        original_text = content.get('original_text', '')
        
        while True:
            # SIEMPRE borrar ad anterior antes de enviar
            await delete_last_ad(context.bot)
            await asyncio.sleep(0.5)
            
            try:
                # Si tiene sintaxis @@@ o %%%, procesar botones
                if original_text and has_special_syntax(original_text):
                    buttons = await build_buttons(original_text, bot_username)
                    clean_text = clean_special_syntax(original_text)
                    
                    if buttons:
                        reply_markup = InlineKeyboardMarkup(buttons)
                        
                        sent = await context.bot.copy_message(
                            chat_id=PUBLIC_CHANNEL_ID,
                            from_chat_id=content['chat_id'],
                            message_id=content['msg_id'],
                            caption=clean_text if clean_text else None,
                            reply_markup=reply_markup
                        )
                        logger.info(f"📢 Ad #{ad_id} con botones: {sent.message_id}")
                    else:
                        sent = await context.bot.copy_message(
                            chat_id=PUBLIC_CHANNEL_ID,
                            from_chat_id=content['chat_id'],
                            message_id=content['msg_id']
                        )
                        logger.info(f"📢 Ad #{ad_id}: {sent.message_id}")
                else:
                    sent = await context.bot.copy_message(
                        chat_id=PUBLIC_CHANNEL_ID,
                        from_chat_id=content['chat_id'],
                        message_id=content['msg_id']
                    )
                    logger.info(f"📢 Ad #{ad_id}: {sent.message_id}")
                
                await set_last_ad(sent.message_id)
                
            except Exception as e:
                logger.error(f"Error enviando ad #{ad_id}: {e}")
            
            # Esperar intervalo
            await asyncio.sleep(interval_minutes * 60)
    
    except asyncio.CancelledError:
        logger.info(f"🛑 Ad #{ad_id} cancelada")
