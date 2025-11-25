# -*- coding: utf-8 -*-
import logging
import asyncio
import re
from typing import Dict, List, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import MessageLimit

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS

logger = logging.getLogger(__name__)

# --- ALMACENAMIENTO TEMPORAL ---
# Guardamos el objeto procesado listo para enviar
active_batches: Dict[int, List[Dict[str, Any]]] = {}
batch_mode: Dict[int, bool] = {} 

# --- PATRONES REGEX ---
JUSTIFICATION_PATTERN = re.compile(r'%%%\s*https?://t\.me/[^\s]+\?start=jst_(\d+(?:-\d+)*)', re.IGNORECASE)
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)(?:\s*\|\s*(.+))?$', re.MULTILINE)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa modo lote"""
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id

    active_batches[user_id] = []
    batch_mode[user_id] = True
    
    await update.message.reply_text(
        "📦 **MODO LOTE ACTIVADO**\n\n"
        "1. Envía **Casos, Encuestas, Fotos, PDFs**.\n"
        "2. Para el botón de respuesta, envía un mensaje SOLO con:\n"
        "   `%%% https://t.me/bot?start=jst_30`\n"
        "3. Para otros botones: `@@@ Texto | Link`\n\n"
        "Al finalizar usa: **/enviar**"
    , parse_mode="Markdown")

async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    active_batches.pop(user_id, None)
    batch_mode[user_id] = False
    await update.message.reply_text("🗑️ Lote eliminado.")

async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publica el lote en el canal"""
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    
    items = active_batches.get(user_id, [])
    if not items:
        await update.message.reply_text("⚠️ El lote está vacío.")
        return

    await update.message.reply_text(f"🚀 Publicando {len(items)} mensajes...")

    try:
        for item in items:
            await send_item_to_channel(context, item)
            await asyncio.sleep(1) 
        
        await update.message.reply_text("✅ **Publicación completada**")
    except Exception as e:
        logger.error(f"Error publicando: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        active_batches[user_id] = []
        batch_mode[user_id] = False

async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Procesa y guarda el mensaje PREPARADO para salir"""
    user_id = update.effective_user.id
    if not batch_mode.get(user_id, False):
        return False

    msg = update.message
    
    # 1. Analizar Texto (Caption o Mensaje plano)
    raw_text = msg.text or msg.caption or ""
    clean_text = raw_text
    buttons = []
    
    # --- PROCESAR %%% (Justificaciones) ---
    just_match = JUSTIFICATION_PATTERN.search(raw_text)
    if just_match:
        ids_string = just_match.group(1)
        bot_info = await context.bot.get_me()
        deep_link = f"https://t.me/{bot_info.username}?start=jst_{ids_string}"
        buttons.append([InlineKeyboardButton("Ver justificación 💬", url=deep_link)])
        clean_text = JUSTIFICATION_PATTERN.sub('', clean_text).strip()

    # --- PROCESAR @@@ (Otros botones) ---
    custom_matches = BUTTON_PATTERN.findall(raw_text)
    if custom_matches:
        for label, url in custom_matches:
            label = label.strip()
            url = url.strip()
            if not url.startswith(('http', 'tg://')): url = 'https://' + url
            buttons.append([InlineKeyboardButton(label, url=url)])
        clean_text = BUTTON_PATTERN.sub('', clean_text).strip()

    # Crear Markup
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    # CASO ESPECIAL: Si era solo un link (%%%...) y el texto quedó vacío
    # Le ponemos un texto por defecto para que no falle o se vea feo
    if not clean_text and buttons and not (msg.photo or msg.video or msg.document or msg.poll):
        clean_text = "📚 **Ver Contenido / Respuesta** 👇"

    # 2. Empaquetar el item según su tipo
    item = {
        'msg_id': msg.message_id,
        'chat_id': msg.chat_id,
        'reply_markup': reply_markup
    }

    if msg.poll:
        item['type'] = 'poll'
        # Las encuestas NO soportan caption ni botones pegados, se mandan tal cual
        # Si quieres botones con encuesta, deben ir en un mensaje separado abajo.
    elif msg.photo or msg.video or msg.document:
        item['type'] = 'media'
        item['caption'] = clean_text # Usamos el texto limpio de códigos
    else:
        item['type'] = 'text'
        item['text'] = clean_text

    # Guardar
    if user_id not in active_batches:
        active_batches[user_id] = []
    active_batches[user_id].append(item)
    
    # Feedback visual
    tipo = "Encuesta" if msg.poll else "Mensaje"
    await msg.reply_text(f"➕ {tipo} agregado")
    return True

async def send_item_to_channel(context: ContextTypes.DEFAULT_TYPE, item: dict):
    """Envía el item al canal público"""
    target = PUBLIC_CHANNEL_ID
    
    if item['type'] == 'poll':
        # Copia exacta de la encuesta (mantiene quiz, opciones, respuesta correcta)
        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id']
        )
    
    elif item['type'] == 'media':
        # Copia el medio pero SOBRESCRIBE el caption (para quitar los códigos %%%)
        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id'],
            caption=item['caption'], # Texto limpio
            reply_markup=item['reply_markup'],
            parse_mode="Markdown"
        )
        
    elif item['type'] == 'text':
        # Mensajes de texto puro (como el del botón solo)
        if item['text']: # Solo enviar si quedó texto
            await context.bot.send_message(
                chat_id=target,
                text=item['text'],
                reply_markup=item['reply_markup'],
                disable_web_page_preview=True,
                parse_mode="Markdown"
            )
