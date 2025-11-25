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
# Reutilizamos el patrón de botones personalizados para los anuncios también
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)(?:\s*\|\s*(.+))?$', re.MULTILINE)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def cmd_set_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    
    await update.message.reply_text(
        "📢 **CREAR PUBLICIDAD**\n\n"
        "Envía el contenido (texto/foto/video).\n"
        "Puedes usar botones con `@@@ Texto | Link`"
    )
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
        await msg.reply_text("⏱️ ¿Cada cuántas HORAS se repite? (Ej: 8, 24)")
        return

    # PASO 2: INTERVALO
    if context.user_data.get('ad_step') == 'interval':
        try:
            hours = int(text.strip())
            interval_mins = hours * 60
        except ValueError:
            await msg.reply_text("❌ Envía solo el número de horas (ej: 8)")
            return

        ad_id = f"ad_{datetime.now(tz=TZ).strftime('%H%M%S')}"
        ad_content = context.user_data['ad_content']
        
        # Publicar inmediatamente
        msg_id = await publish_ad(context, ad_content)
        
        # Crear tarea de repetición
        task = asyncio.create_task(
            schedule_ad_republish(context, ad_id, ad_content, interval_mins)
        )
        
        active_ads[ad_id] = {
            'message_id': msg_id,
            'task': task,
            'interval': hours
        }
        
        context.user_data.clear()
        await msg.reply_text(f"✅ **AD Creada**\nID: `{ad_id}`\nRepite cada: {hours}h")

async def publish_ad(context: ContextTypes.DEFAULT_TYPE, content: dict) -> Optional[int]:
    """Publica y procesa botones @@@ para anuncios"""
    text = content['text']
    reply_markup = None
    
    # Procesar botones @@@ en anuncios
    matches = BUTTON_PATTERN.findall(text)
    if matches:
        buttons = []
        for label, url in matches:
            buttons.append([InlineKeyboardButton(label, url=url.strip())])
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
            
            # 1. ELIMINAR ANTERIOR (Limpieza)
            if ad_id in active_ads and active_ads[ad_id]['message_id']:
                try:
                    await context.bot.delete_message(PUBLIC_CHANNEL_ID, active_ads[ad_id]['message_id'])
                except: pass # Si ya no existe, ignorar
            
            # 2. PUBLICAR NUEVA
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
        txt += f"- `{aid}` ({data['interval']}h)\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def cmd_delete_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not active_ads:
        await update.message.reply_text("📭 Nada que borrar.")
        return
        
    keyboard = [[InlineKeyboardButton(f"🗑️ {aid}", callback_data=f"del_ad_{aid}")] for aid in active_ads]
    await update.message.reply_text("Selecciona AD para borrar:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_ads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("del_ad_"):
        aid = data.replace("del_ad_", "")
        if aid in active_ads:
            active_ads[aid]['task'].cancel() # Parar repetición
            try:
                # Intentar borrar el mensaje del canal
                await context.bot.delete_message(PUBLIC_CHANNEL_ID, active_ads[aid]['message_id'])
            except: pass
            del active_ads[aid]
            await query.edit_message_text(f"✅ AD `{aid}` eliminada.")
        else:
            await query.edit_message_text("❌ Ya no existe.")
