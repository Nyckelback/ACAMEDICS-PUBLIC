# -*- coding: utf-8 -*-
import logging
import asyncio
import re
from typing import Dict, List, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS

logger = logging.getLogger(__name__)

# --- ALMACENAMIENTO TEMPORAL ---
active_batches: Dict[int, List[Dict[str, Any]]] = {}
batch_mode: Dict[int, bool] = {} 

# --- PATRONES REGEX ---
# %%% para justificaciones (genera botón "VER JUSTIFICACIÓN")
JUSTIFICATION_PATTERN = re.compile(r'%%%\s*(.+?)(?:\n|$)', re.IGNORECASE)

# @@@ para botones custom
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)(?:\s*\|\s*(.+))?$', re.MULTILINE)

# Detectar links de Telegram: t.me/c/123456/789 o t.me/username/789
TELEGRAM_LINK = re.compile(r'(?:https?://)?t\.me/(?:c/(\d+)|([a-zA-Z_][a-zA-Z0-9_]*))/(\d+)', re.IGNORECASE)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

def extract_telegram_deep_link(link_text: str, bot_username: str) -> Optional[str]:
    """
    Extrae un link de Telegram y lo convierte en deep link para el bot.
    IMPORTANTE: Telegram solo permite A-Z, a-z, 0-9, _ y - en start=
    
    - t.me/c/1234567890/123 → bot?start=c_1234567890_123
    - t.me/just_clinicase/30 → bot?start=p_just_clinicase_30 (p = público)
    - Solo ID (30) → bot?start=30
    """
    match = TELEGRAM_LINK.search(link_text)
    if match:
        private_id = match.group(1)  # t.me/c/XXXXX/ID
        public_username = match.group(2)  # t.me/username/ID
        message_id = match.group(3)
        
        if private_id:
            # Canal privado: c_CHANNELID_MSGID
            return f"https://t.me/{bot_username}?start=c_{private_id}_{message_id}"
        elif public_username:
            # Canal público: p_USERNAME_MSGID (sin @ porque Telegram no lo permite)
            return f"https://t.me/{bot_username}?start=p_{public_username}_{message_id}"
    
    # Si es solo un número
    if link_text.strip().isdigit():
        return f"https://t.me/{bot_username}?start={link_text.strip()}"
    
    return None

def process_button_url(url_text: str) -> str:
    """
    Procesa la URL de un botón de forma inteligente:
    - @username → https://t.me/username (perfil de Telegram)
    - t.me/... → https://t.me/...
    - link.com → https://link.com
    - https://... → tal cual
    - tg://... → tal cual
    """
    url = url_text.strip()
    
    if not url:
        return ""
    
    # Ya tiene protocolo
    if url.startswith(('http://', 'https://', 'tg://')):
        return url
    
    # Es un @username de Telegram
    if url.startswith('@'):
        username = url[1:]  # Quitar @
        return f"https://t.me/{username}"
    
    # Es un link de t.me
    if url.startswith('t.me/'):
        return f"https://{url}"
    
    # Es un dominio normal
    if '.' in url:
        return f"https://{url}"
    
    # Si no tiene nada, dejarlo vacío
    return ""

def has_special_syntax(text: str) -> bool:
    if not text:
        return False
    return bool(JUSTIFICATION_PATTERN.search(text) or BUTTON_PATTERN.search(text))

def is_button_only_message(text: str) -> bool:
    if not text:
        return False
    clean = BUTTON_PATTERN.sub('', text).strip()
    clean = JUSTIFICATION_PATTERN.sub('', clean).strip()
    return len(clean) == 0 and has_special_syntax(text)

async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    active_batches[user_id] = []
    batch_mode[user_id] = True
    
    await update.message.reply_text(
        "📦 **MODO LOTE ACTIVADO**\n\n"
        "Envía todo (Encuestas, Fotos, Textos).\n\n"
        "🔹 **Justificación:** `%%% t.me/canal/22`\n"
        "🔸 **Botón:** `@@@ Texto | link` o `@@@ Texto | @user`\n"
        "⚠️ Botón solo → se pega al mensaje anterior\n\n"
        "**/enviar** para publicar"
    , parse_mode="Markdown")

async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    active_batches.pop(user_id, None)
    batch_mode[user_id] = False
    await update.message.reply_text("🗑️ Cancelado.")

async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    
    items = active_batches.get(user_id, [])
    if not items:
        await update.message.reply_text("⚠️ Lote vacío.")
        return

    status = await update.message.reply_text(f"🚀 Enviando {len(items)}...")

    try:
        count = 0
        last_sent_message = None
        
        for item in items:
            # Botón para asociar al anterior
            if item['type'] == 'button_for_previous':
                if last_sent_message:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=PUBLIC_CHANNEL_ID,
                            message_id=last_sent_message.message_id,
                            reply_markup=item['reply_markup']
                        )
                    except Exception as e:
                        logger.error(f"Error asociando botón: {e}")
                continue
            
            sent = await send_item_reconstructed(context, item)
            if sent:
                last_sent_message = sent
            count += 1
            await asyncio.sleep(1.5)
        
        await status.edit_text(f"✅ {count} mensajes enviados.")
    except Exception as e:
        logger.error(f"Error: {e}")
        await status.edit_text(f"❌ Error: {e}")
    finally:
        active_batches[user_id] = []
        batch_mode[user_id] = False

async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if not batch_mode.get(user_id, False): 
        return False

    msg = update.message
    raw_text = msg.text or msg.caption or ""
    bot_info = await context.bot.get_me()
    
    item = {
        'chat_id': msg.chat_id,
        'msg_id': msg.message_id,
        'reply_markup': None
    }

    # ========== ENCUESTA ==========
    if msg.poll:
        item['type'] = 'poll_clone'
        item['question'] = msg.poll.question
        item['options'] = [o.text for o in msg.poll.options]
        item['is_anonymous'] = True
        item['poll_type'] = msg.poll.type
        item['allows_multiple_answers'] = msg.poll.allows_multiple_answers
        item['correct_option_id'] = msg.poll.correct_option_id
        item['explanation'] = msg.poll.explanation
        item['explanation_entities'] = msg.poll.explanation_entities
        
        # Verificar si es quiz y si tiene respuesta detectada
        if msg.poll.type == 'quiz':
            if msg.poll.correct_option_id is not None:
                letra = chr(65 + msg.poll.correct_option_id)  # A, B, C, D
                await msg.reply_text(f"✅ **ENCUESTA CAPTURADA**\nRespuesta correcta: **{letra}**", parse_mode="Markdown")
            else:
                await msg.reply_text(
                    "⚠️ **QUIZ SIN RESPUESTA DETECTADA**\n"
                    "Vota en la encuesta antes de enviarla.\n"
                    "Se enviará con opción A como correcta por defecto."
                , parse_mode="Markdown")
                item['correct_option_id'] = 0
        else:
            await msg.reply_text("✅ Encuesta capturada")
    
    # ========== SOLO BOTÓN → ASOCIAR AL ANTERIOR ==========
    elif is_button_only_message(raw_text):
        buttons = await build_buttons(raw_text, bot_info.username)
        
        if buttons:
            item['type'] = 'button_for_previous'
            item['reply_markup'] = InlineKeyboardMarkup(buttons)
            await msg.reply_text("🔗 **BOTÓN CAPTURADO** (se asociará al mensaje anterior)", parse_mode="Markdown")
        else:
            return True
    
    # ========== CONTENIDO + BOTÓN ==========
    elif has_special_syntax(raw_text):
        buttons = await build_buttons(raw_text, bot_info.username)
        clean_text = clean_special_syntax(raw_text)
        
        item['type'] = 'media' if (msg.photo or msg.video or msg.document) else 'text'
        item['clean_text'] = clean_text
        item['reply_markup'] = InlineKeyboardMarkup(buttons) if buttons else None
        
        await msg.reply_text("✅ **MENSAJE + BOTÓN CAPTURADO**", parse_mode="Markdown")
    
    # ========== MENSAJE NORMAL ==========
    else:
        item['type'] = 'forward'
        await msg.reply_text("✅ Mensaje capturado")

    if user_id not in active_batches: 
        active_batches[user_id] = []
    active_batches[user_id].append(item)
    return True

async def build_buttons(text: str, bot_username: str) -> List[List[InlineKeyboardButton]]:
    """Construye botones desde el texto"""
    buttons = []
    
    # Procesar %%% (justificaciones)
    for match in JUSTIFICATION_PATTERN.finditer(text):
        link_text = match.group(1).strip()
        deep_link = extract_telegram_deep_link(link_text, bot_username)
        if deep_link:
            buttons.append([InlineKeyboardButton("VER JUSTIFICACIÓN 💬", url=deep_link)])
    
    # Procesar @@@ (botones custom)
    for match in BUTTON_PATTERN.finditer(text):
        label = match.group(1).strip()
        url_raw = (match.group(2) or "").strip()
        
        if not label:
            continue
        
        # Si tiene URL
        if url_raw:
            # Verificar si es un link de Telegram para contenido
            if TELEGRAM_LINK.search(url_raw):
                # Es un link de mensaje → convertir a deep link
                deep_link = extract_telegram_deep_link(url_raw, bot_username)
                if deep_link:
                    buttons.append([InlineKeyboardButton(label, url=deep_link)])
            else:
                # Es un link normal o @username
                processed_url = process_button_url(url_raw)
                if processed_url:
                    buttons.append([InlineKeyboardButton(label, url=processed_url)])
        else:
            # Sin URL - botón de solo display (callback vacío)
            buttons.append([InlineKeyboardButton(label, callback_data="none")])
    
    return buttons

def clean_special_syntax(text: str) -> str:
    """Limpia %%% y @@@ del texto"""
    clean = JUSTIFICATION_PATTERN.sub('', text)
    clean = BUTTON_PATTERN.sub('', clean)
    return clean.strip()

async def send_item_reconstructed(context: ContextTypes.DEFAULT_TYPE, item: dict) -> Optional[object]:
    target = PUBLIC_CHANNEL_ID
    
    if item['type'] == 'poll_clone':
        return await context.bot.send_poll(
            chat_id=target,
            question=item['question'],
            options=item['options'],
            is_anonymous=True,
            type=item['poll_type'],
            allows_multiple_answers=item['allows_multiple_answers'],
            correct_option_id=item['correct_option_id'],
            explanation=item['explanation'],
            explanation_entities=item['explanation_entities']
        )
    
    if item['type'] == 'forward':
        return await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id']
        )
    
    if item['type'] == 'media':
        return await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id'],
            caption=item.get('clean_text'),
            reply_markup=item.get('reply_markup')
        )
    
    if item['type'] == 'text':
        text = item.get('clean_text', '')
        if text:
            return await context.bot.send_message(
                chat_id=target,
                text=text,
                reply_markup=item.get('reply_markup'),
                disable_web_page_preview=False
            )
    
    return None
