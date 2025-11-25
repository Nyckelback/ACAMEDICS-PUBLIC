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
CHANNEL_LINK_PATTERN = re.compile(r'%%%\s*(?:https?://)?t\.me/(?:c/|\w+/)+(\d+)', re.IGNORECASE)
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)(?:\s*\|\s*(.+))?$', re.MULTILINE)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

def has_special_syntax(text: str) -> bool:
    """Verifica si el texto tiene %%% o @@@"""
    if not text:
        return False
    return bool(CHANNEL_LINK_PATTERN.search(text) or BUTTON_PATTERN.search(text))

def is_button_only_message(text: str) -> bool:
    """Verifica si el mensaje es SOLO un botón @@@ sin otro contenido"""
    if not text:
        return False
    # Quitar los patrones de botón y ver si queda algo
    clean = BUTTON_PATTERN.sub('', text).strip()
    clean = CHANNEL_LINK_PATTERN.sub('', clean).strip()
    return len(clean) == 0 and bool(BUTTON_PATTERN.search(text) or CHANNEL_LINK_PATTERN.search(text))

async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa modo lote"""
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    active_batches[user_id] = []
    batch_mode[user_id] = True
    
    await update.message.reply_text(
        "📦 **MODO LOTE ACTIVADO**\n\n"
        "Envía todo (Encuestas, Fotos, Textos, Links).\n\n"
        "🔹 **Justificación:** `%%% t.me/canal/22`\n"
        "🔸 **Botón custom:** `@@@ Texto | Link`\n"
        "⚠️ El botón se pega al mensaje ANTERIOR\n\n"
        "Finaliza con **/enviar**"
    , parse_mode="Markdown")

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
        last_sent_message = None
        
        for item in items:
            # Si es un botón para asociar al anterior
            if item['type'] == 'button_for_previous':
                if last_sent_message:
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=PUBLIC_CHANNEL_ID,
                            message_id=last_sent_message.message_id,
                            reply_markup=item['reply_markup']
                        )
                        logger.info(f"✅ Botón asociado al mensaje {last_sent_message.message_id}")
                    except Exception as e:
                        logger.error(f"Error asociando botón: {e}")
                        # Fallback: enviar como mensaje separado
                        await context.bot.send_message(
                            chat_id=PUBLIC_CHANNEL_ID,
                            text="👇",
                            reply_markup=item['reply_markup']
                        )
                continue
            
            # Enviar el item normal
            sent = await send_item_reconstructed(context, item)
            if sent:
                last_sent_message = sent
            count += 1
            await asyncio.sleep(1.5)
        
        await status.edit_text(f"✅ **¡Listo! {count} mensajes enviados.**")
    except Exception as e:
        logger.error(f"Error lote: {e}")
        await status.edit_text(f"❌ Error enviando: {e}")
    finally:
        active_batches[user_id] = []
        batch_mode[user_id] = False

async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Captura mensajes para el lote"""
    user_id = update.effective_user.id
    if not batch_mode.get(user_id, False): 
        return False

    msg = update.message
    raw_text = msg.text or msg.caption or ""
    
    item = {
        'chat_id': msg.chat_id,
        'msg_id': msg.message_id,
        'reply_markup': None
    }

    # ========== CASO 1: ENCUESTA ==========
    if msg.poll:
        item['type'] = 'poll_clone'
        item['question'] = msg.poll.question
        item['options'] = [o.text for o in msg.poll.options]
        item['is_anonymous'] = True  # Forzado para canales
        item['poll_type'] = msg.poll.type
        item['allows_multiple_answers'] = msg.poll.allows_multiple_answers
        item['correct_option_id'] = msg.poll.correct_option_id
        item['explanation'] = msg.poll.explanation
        item['explanation_entities'] = msg.poll.explanation_entities
        
        # Aviso si es quiz sin respuesta detectada
        if msg.poll.type == 'quiz' and msg.poll.correct_option_id is None:
            await msg.reply_text(
                "⚠️ **QUIZ SIN RESPUESTA DETECTADA**\n"
                "Vota en la encuesta antes de enviarla.\n"
                "Se enviará con opción A como correcta por defecto."
            , parse_mode="Markdown")
            item['correct_option_id'] = 0  # Fallback a A
        
        await msg.reply_text("➕ Encuesta capturada")
    
    # ========== CASO 2: MENSAJE SOLO CON BOTÓN (%%% o @@@) ==========
    elif is_button_only_message(raw_text):
        buttons = []
        
        # Procesar %%%
        just_match = CHANNEL_LINK_PATTERN.search(raw_text)
        if just_match:
            content_id = just_match.group(1)
            bot_info = await context.bot.get_me()
            deep_link = f"https://t.me/{bot_info.username}?start={content_id}"
            buttons.append([InlineKeyboardButton("VER JUSTIFICACIÓN 💬", url=deep_link)])
        
        # Procesar @@@
        custom_matches = BUTTON_PATTERN.findall(raw_text)
        for label, url in custom_matches:
            url = (url or "").strip()
            if url:
                if not url.startswith(('http', 'tg://')): 
                    url = 'https://' + url
                buttons.append([InlineKeyboardButton(label.strip(), url=url)])
        
        if buttons:
            item['type'] = 'button_for_previous'
            item['reply_markup'] = InlineKeyboardMarkup(buttons)
            await msg.reply_text("🔗 Botón capturado (se asociará al mensaje anterior)")
        else:
            return True  # Ignorar si no hay botones válidos
    
    # ========== CASO 3: MENSAJE CON CONTENIDO + BOTÓN ==========
    elif has_special_syntax(raw_text):
        buttons = []
        clean_text = raw_text
        
        # Procesar %%%
        just_match = CHANNEL_LINK_PATTERN.search(raw_text)
        if just_match:
            content_id = just_match.group(1)
            bot_info = await context.bot.get_me()
            deep_link = f"https://t.me/{bot_info.username}?start={content_id}"
            buttons.append([InlineKeyboardButton("VER JUSTIFICACIÓN 💬", url=deep_link)])
            clean_text = CHANNEL_LINK_PATTERN.sub('', clean_text).strip()
        
        # Procesar @@@
        custom_matches = BUTTON_PATTERN.findall(raw_text)
        for label, url in custom_matches:
            url = (url or "").strip()
            if url:
                if not url.startswith(('http', 'tg://')): 
                    url = 'https://' + url
                buttons.append([InlineKeyboardButton(label.strip(), url=url)])
        clean_text = BUTTON_PATTERN.sub('', clean_text).strip()
        
        item['type'] = 'media' if (msg.photo or msg.video or msg.document) else 'text'
        item['clean_text'] = clean_text
        item['reply_markup'] = InlineKeyboardMarkup(buttons) if buttons else None
        
        await msg.reply_text("➕ Mensaje con botón capturado")
    
    # ========== CASO 4: MENSAJE NORMAL - COPIAR TAL CUAL ==========
    else:
        item['type'] = 'forward'  # Copiar sin modificar
        await msg.reply_text("➕ Mensaje capturado")

    # Guardar
    if user_id not in active_batches: 
        active_batches[user_id] = []
    active_batches[user_id].append(item)
    return True

async def send_item_reconstructed(context: ContextTypes.DEFAULT_TYPE, item: dict) -> Optional[object]:
    """Envía un item al canal y retorna el mensaje enviado"""
    target = PUBLIC_CHANNEL_ID
    
    # CASO 1: CLONAR ENCUESTA
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
    
    # CASO 2: FORWARD - Copiar mensaje exacto sin modificar
    if item['type'] == 'forward':
        return await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id']
        )
    
    # CASO 3: MEDIA con botones
    if item['type'] == 'media':
        return await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id'],
            caption=item.get('clean_text'),
            reply_markup=item.get('reply_markup')
        )
    
    # CASO 4: TEXTO con botones
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
