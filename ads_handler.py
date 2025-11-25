# -*- coding: utf-8 -*-
import logging
import asyncio
import re
from datetime import datetime
from typing import Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS, TZ

logger = logging.getLogger(__name__)

active_ads: Dict[str, Dict] = {}
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)(?:\s*\|\s*(.+))?$', re.MULTILINE)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

def process_button_url(url_text: str) -> str:
    """
    Procesa URL de forma inteligente:
    - @username → https://t.me/username
    - t.me/... → https://t.me/...
    - link.com → https://link.com
    - https://... → tal cual
    """
    url = url_text.strip()
    
    if not url:
        return ""
    
    # Ya tiene protocolo
    if url.startswith(('http://', 'https://', 'tg://')):
        return url
    
    # Es @username
    if url.startswith('@'):
        username = url[1:]
        return f"https://t.me/{username}"
    
    # Es t.me/...
    if url.startswith('t.me/'):
        return f"https://{url}"
    
    # Dominio normal
    if '.' in url:
        return f"https://{url}"
    
    return ""

def parse_interval(text: str) -> Optional[int]:
    """
    Parsea intervalo de tiempo.
    Retorna minutos.
    Ejemplos: "5m", "30m", "1h", "8h", "24", "5 min", "2 horas"
    """
    text = text.strip().lower()
    
    # Patrones
    patterns = [
        (r'^(\d+)\s*(?:m|min|minutos?)$', 1),      # 5m, 30min, 5 minutos
        (r'^(\d+)\s*(?:h|hr|horas?)$', 60),        # 1h, 8hr, 2 horas
        (r'^(\d+)$', 60),                           # Solo número = horas (legacy)
    ]
    
    for pattern, multiplier in patterns:
        match = re.match(pattern, text)
        if match:
            value = int(match.group(1))
            return value * multiplier
    
    return None

async def cmd_set_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    
    await update.message.reply_text(
        "📢 **CREAR PUBLICIDAD**\n\n"
        "Envía el contenido (texto/foto/video).\n"
        "Botones: `@@@ Texto | link` o `@@@ Texto | @usuario`\n\n"
        "Usa /cancel para cancelar."
    , parse_mode="Markdown")
    context.user_data['creating_ad'] = True
    context.user_data['ad_step'] = 'content'

async def handle_private_message_for_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text or ""

    if text == '/cancel':
        context.user_data.clear()
        await msg.reply_text("❌ Cancelado")
        return

    # PASO 1: CONTENIDO
    if context.user_data.get('ad_step') == 'content':
        context.user_data['ad_content'] = {
            'text': msg.text or msg.caption or "",
            'photo': msg.photo[-1].file_id if msg.photo else None,
            'video': msg.video.file_id if msg.video else None
        }
        context.user_data['ad_step'] = 'interval'
        await msg.reply_text(
            "⏱️ **¿Cada cuánto se repite?**\n\n"
            "Ejemplos:\n"
            "• `5m` o `5 min` → 5 minutos\n"
            "• `1h` o `1 hora` → 1 hora\n"
            "• `8` → 8 horas (legacy)"
        , parse_mode="Markdown")
        return

    # PASO 2: INTERVALO
    if context.user_data.get('ad_step') == 'interval':
        interval_mins = parse_interval(text)
        
        if interval_mins is None or interval_mins < 1:
            await msg.reply_text("❌ Formato inválido. Usa: 5m, 30min, 1h, 8h")
            return

        ad_id = f"ad_{datetime.now(tz=TZ).strftime('%H%M%S')}"
        ad_content = context.user_data['ad_content']
        
        # Publicar inmediatamente
        msg_id = await publish_ad(context, ad_content)
        
        # Crear tarea de repetición
        task = asyncio.create_task(
            schedule_ad_republish(context, ad_id, ad_content, interval_mins)
        )
        
        # Mostrar intervalo legible
        if interval_mins >= 60:
            interval_display = f"{interval_mins // 60}h"
        else:
            interval_display = f"{interval_mins}m"
        
        active_ads[ad_id] = {
            'message_id': msg_id,
            'task': task,
            'interval': interval_mins,
            'interval_display': interval_display
        }
        
        context.user_data.clear()
        await msg.reply_text(f"✅ **AD Creada**\nID: `{ad_id}`\nRepite cada: {interval_display}")

async def publish_ad(context: ContextTypes.DEFAULT_TYPE, content: dict) -> Optional[int]:
    text = content['text']
    reply_markup = None
    
    matches = BUTTON_PATTERN.findall(text)
    if matches:
        buttons = []
        for label, url_raw in matches:
            url = process_button_url(url_raw)
            if url:
                buttons.append([InlineKeyboardButton(label.strip(), url=url)])
        if buttons:
            reply_markup = InlineKeyboardMarkup(buttons)
        text = BUTTON_PATTERN.sub('', text).strip()

    try:
        if content['photo']:
            m = await context.bot.send_photo(PUBLIC_CHANNEL_ID, content['photo'], caption=text, reply_markup=reply_markup)
        elif content['video']:
            m = await context.bot.send_video(PUBLIC_CHANNEL_ID, content['video'], caption=text, reply_markup=reply_markup)
        else:
            m = await context.bot.send_message(PUBLIC_CHANNEL_ID, text=text, reply_markup=reply_markup)
        return m.message_id
    except Exception as e:
        logger.error(f"Error AD: {e}")
        return None

async def schedule_ad_republish(context, ad_id, content, interval_mins):
    try:
        while True:
            await asyncio.sleep(interval_mins * 60)
            
            if ad_id in active_ads and active_ads[ad_id]['message_id']:
                try:
                    await context.bot.delete_message(PUBLIC_CHANNEL_ID, active_ads[ad_id]['message_id'])
                except: pass
            
            new_id = await publish_ad(context, content)
            if new_id:
                active_ads[ad_id]['message_id'] = new_id
                
    except asyncio.CancelledError:
        pass

async def cmd_list_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not active_ads:
        await update.message.reply_text("📭 No hay anuncios activos.")
        return
    
    txt = "📢 **ADS ACTIVAS:**\n"
    for aid, data in active_ads.items():
        display = data.get('interval_display', f"{data['interval']}m")
        txt += f"• `{aid}` (cada {display})\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def cmd_delete_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not active_ads:
        await update.message.reply_text("📭 Nada que borrar.")
        return
        
    keyboard = [[InlineKeyboardButton(f"🗑️ {aid}", callback_data=f"del_ad_{aid}")] for aid in active_ads]
    await update.message.reply_text("Selecciona AD:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_ads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("del_ad_"):
        aid = data.replace("del_ad_", "")
        if aid in active_ads:
            active_ads[aid]['task'].cancel()
            try:
                await context.bot.delete_message(PUBLIC_CHANNEL_ID, active_ads[aid]['message_id'])
            except: pass
            del active_ads[aid]
            await query.edit_message_text(f"✅ AD `{aid}` eliminada.")
        else:
            await query.edit_message_text("❌ Ya no existe.")
