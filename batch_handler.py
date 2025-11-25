# -*- coding: utf-8 -*-
import logging
import asyncio
import re
from typing import Dict, List, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS

logger = logging.getLogger(__name__)

# --- ALMACENAMIENTO TEMPORAL ---
active_batches: Dict[int, List[Dict[str, Any]]] = {}
batch_mode: Dict[int, bool] = {} 

# --- PATRONES REGEX ---
# Detecta links para el botón: %%% https://t.me/canal/22
CHANNEL_LINK_PATTERN = re.compile(r'%%%\s*(?:https?://)?t\.me/(?:c/|\w+/)+(\d+)', re.IGNORECASE)

# Detecta botones custom: @@@ Texto | Link
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)(?:\s*\|\s*(.+))?$', re.MULTILINE)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa modo lote"""
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    active_batches[user_id] = []
    batch_mode[user_id] = True
    await update.message.reply_text("📦 **MODO LOTE**\nEnvía todo (Encuestas, Fotos, Textos).\n\nPara el botón de respuesta:\n`%%% https://t.me/tu_canal/123`")

async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    active_batches.pop(user_id, None)
    batch_mode[user_id] = False
    await update.message.reply_text("🗑️ Cancelado.")

async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publica todo al canal"""
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    
    items = active_batches.get(user_id, [])
    if not items:
        await update.message.reply_text("⚠️ Lote vacío.")
        return

    status = await update.message.reply_text(f"🚀 Enviando {len(items)} mensajes...")

    try:
        count = 0
        for item in items:
            await send_item_reconstructed(context, item)
            count += 1
            await asyncio.sleep(1.5) # Pausa necesaria
        
        await status.edit_text(f"✅ **¡Listo! {count} mensajes enviados.**")
    except Exception as e:
        logger.error(f"Error lote: {e}")
        await status.edit_text(f"❌ Error enviando: {e}")
    finally:
        active_batches[user_id] = []
        batch_mode[user_id] = False

async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Captura y procesa los datos DEL MOMENTO"""
    user_id = update.effective_user.id
    if not batch_mode.get(user_id, False): return False

    msg = update.message
    item = {
        'chat_id': msg.chat_id,
        'msg_id': msg.message_id,
        'reply_markup': None
    }

    # 1. SI ES ENCUESTA (POLL) - EXTRAER DATOS PARA CLONAR
    if msg.poll:
        item['type'] = 'poll_clone'
        item['question'] = msg.poll.question
        item['options'] = [o.text for o in msg.poll.options]
        item['is_anonymous'] = msg.poll.is_anonymous
        item['poll_type'] = msg.poll.type # 'regular' o 'quiz'
        item['allows_multiple_answers'] = msg.poll.allows_multiple_answers
        item['correct_option_id'] = msg.poll.correct_option_id
        item['explanation'] = msg.poll.explanation
        item['explanation_entities'] = msg.poll.explanation_entities
        
        await msg.reply_text("➕ Encuesta capturada (Modo Clonación)")
    
    # 2. SI ES TEXTO O MEDIA - PROCESAR BOTONES
    else:
        raw_text = msg.text or msg.caption or ""
        clean_text = raw_text
        buttons = []

        # Botón de Justificación (%%%)
        just_match = CHANNEL_LINK_PATTERN.search(raw_text)
        if just_match:
            content_id = just_match.group(1)
            bot_info = await context.bot.get_me()
            deep_link = f"https://t.me/{bot_info.username}?start={content_id}"
            
            buttons.append([InlineKeyboardButton("VER JUSTIFICACIÓN 💬", url=deep_link)])
            clean_text = CHANNEL_LINK_PATTERN.sub('', clean_text).strip()

        # Botones Custom (@@@)
        custom_matches = BUTTON_PATTERN.findall(raw_text)
        if custom_matches:
            for label, url in custom_matches:
                url = url.strip()
                if not url.startswith(('http', 'tg://')): url = 'https://' + url
                buttons.append([InlineKeyboardButton(label.strip(), url=url)])
            clean_text = BUTTON_PATTERN.sub('', clean_text).strip()

        # Si el texto quedó vacío pero había botones (ej: mensaje solo con el link)
        if not clean_text and buttons and not (msg.photo or msg.video or msg.document):
            clean_text = "👇"

        item['type'] = 'media' if (msg.photo or msg.video or msg.document) else 'text'
        item['clean_text'] = clean_text
        item['reply_markup'] = InlineKeyboardMarkup(buttons) if buttons else None
        
        await msg.reply_text("➕ Mensaje agregado")

    # Guardar en memoria
    if user_id not in active_batches: active_batches[user_id] = []
    active_batches[user_id].append(item)
    return True

async def send_item_reconstructed(context: ContextTypes.DEFAULT_TYPE, item: dict):
    target = PUBLIC_CHANNEL_ID
    
    # CASO 1: CLONAR ENCUESTA (Evita el error "Message can't be copied")
    if item['type'] == 'poll_clone':
        await context.bot.send_poll(
            chat_id=target,
            question=item['question'],
            options=item['options'],
            is_anonymous=item['is_anonymous'],
            type=item['poll_type'],
            allows_multiple_answers=item['allows_multiple_answers'],
            correct_option_id=item['correct_option_id'],
            explanation=item['explanation'],
            explanation_entities=item['explanation_entities']
        )
        return

    # CASO 2: MENSAJES NORMALES
    if item['type'] == 'media':
        # Usamos copy_message pero pisamos el caption
        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id'],
            caption=item['clean_text'],
            reply_markup=item['reply_markup'],
            parse_mode="Markdown"
        )
    elif item['type'] == 'text':
        # Enviar texto limpio
        if item['clean_text']:
            await context.bot.send_message(
                chat_id=target,
                text=item['clean_text'],
                reply_markup=item['reply_markup'],
                disable_web_page_preview=True,
                parse_mode="Markdown"
            )
