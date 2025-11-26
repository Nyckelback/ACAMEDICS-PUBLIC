# -*- coding: utf-8 -*-
"""
BATCH HANDLER - Sistema de lotes para publicación
CORREGIDO: Deep links, múltiples botones, formato @@@ con |
"""
import logging
import asyncio
import re
from typing import Dict, List, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS

logger = logging.getLogger(__name__)

# ============ ALMACENAMIENTO ============
active_batches: Dict[int, List[Dict[str, Any]]] = {}
batch_mode: Dict[int, bool] = {}

# ============ PATRONES REGEX ============
# %%% para justificaciones - captura TODO después de %%%
JUSTIFICATION_PATTERN = re.compile(r'%%%\s*(https?://t\.me/[^\s]+)', re.IGNORECASE)

# @@@ REQUIERE separador | obligatorio
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)\s*\|\s*([^\n]+)', re.MULTILINE)

# Detectar links de Telegram (público y privado)
TELEGRAM_LINK_PATTERN = re.compile(
    r'(?:https?://)?t\.me/(?:c/(\d+)|([a-zA-Z][a-zA-Z0-9_]*))/(\d+)',
    re.IGNORECASE
)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def extract_telegram_deep_link(link_text: str, bot_username: str, include_channel: bool = False) -> Optional[str]:
    """
    Convierte link de Telegram a deep link válido.
    
    Si include_channel=False (%%%):
        t.me/canal/20 → bot?start=20 (usa JUSTIFICATIONS_CHAT_ID)
    
    Si include_channel=True (@@@):
        t.me/canal/20 → bot?start=d_p_canal-20 (incluye info del canal, sin chiste)
        t.me/c/123456/20 → bot?start=d_c_123456-20 (canal privado, sin chiste)
    """
    match = TELEGRAM_LINK_PATTERN.search(link_text)
    if not match:
        # Si es solo número
        if link_text.strip().isdigit():
            return f"https://t.me/{bot_username}?start={link_text.strip()}"
        return None
    
    private_id = match.group(1)      # Para t.me/c/XXXX/YY
    public_username = match.group(2)  # Para t.me/username/YY
    message_id = match.group(3)
    
    # %%% → Solo message_id (usa JUSTIFICATIONS_CHAT_ID por defecto)
    if not include_channel:
        return f"https://t.me/{bot_username}?start={message_id}"
    
    # @@@ → Incluir info del canal (prefijo d_ = delivery sin chiste)
    if private_id:
        # Canal privado: d_c_CHANNELID-MSGID
        return f"https://t.me/{bot_username}?start=d_c_{private_id}-{message_id}"
    elif public_username:
        # Canal público: d_p_USERNAME-MSGID
        return f"https://t.me/{bot_username}?start=d_p_{public_username}-{message_id}"
    
    return None


def process_button_url(url_text: str) -> str:
    """Procesa URL de botón para formato correcto"""
    url = url_text.strip()
    if not url:
        return ""
    
    # Ya tiene protocolo
    if url.startswith(('http://', 'https://', 'tg://')):
        return url
    
    # Es @username
    if url.startswith('@'):
        return f"https://t.me/{url[1:]}"
    
    # Es link de telegram sin https
    if url.startswith('t.me/'):
        return f"https://{url}"
    
    # Es dominio normal
    if '.' in url:
        return f"https://{url}"
    
    return ""


def has_special_syntax(text: str) -> bool:
    """Verifica si tiene sintaxis especial %%% o @@@"""
    if not text:
        return False
    return bool(JUSTIFICATION_PATTERN.search(text) or BUTTON_PATTERN.search(text))


def is_button_only_message(text: str) -> bool:
    """Verifica si el mensaje es SOLO botón(es) sin otro contenido"""
    if not text:
        return False
    
    # Quitar todas las sintaxis especiales
    clean = JUSTIFICATION_PATTERN.sub('', text)
    clean = BUTTON_PATTERN.sub('', clean)
    clean = clean.strip()
    
    # Si queda vacío y tenía sintaxis, es solo botón
    return len(clean) == 0 and has_special_syntax(text)


def clean_special_syntax(text: str) -> str:
    """Limpia el texto de sintaxis especiales"""
    clean = JUSTIFICATION_PATTERN.sub('', text)
    clean = BUTTON_PATTERN.sub('', clean)
    return clean.strip()


async def build_buttons(text: str, bot_username: str) -> List[List[InlineKeyboardButton]]:
    """Construye lista de botones desde el texto"""
    buttons = []
    
    # %%% Justificaciones → DEEP LINK al bot (con chiste médico)
    for match in JUSTIFICATION_PATTERN.finditer(text):
        link_text = match.group(1).strip()
        deep_link = extract_telegram_deep_link(link_text, bot_username, include_channel=False)
        if deep_link:
            buttons.append([InlineKeyboardButton("VER JUSTIFICACIÓN 💬", url=deep_link)])
            logger.info(f"✅ %%% Justificación: {deep_link}")
        else:
            logger.warning(f"⚠️ No se pudo crear deep link para: {link_text}")
    
    # @@@ Botones custom → INTELIGENTE
    for match in BUTTON_PATTERN.finditer(text):
        label = match.group(1).strip()
        url_raw = match.group(2).strip()
        
        if not label or not url_raw:
            continue
        
        # Detectar si es link de MENSAJE de canal (t.me/canal/NUMERO)
        telegram_msg_match = TELEGRAM_LINK_PATTERN.search(url_raw)
        
        if telegram_msg_match:
            # Es link de mensaje de canal → DEEP LINK (sin chiste)
            deep_link = extract_telegram_deep_link(url_raw, bot_username, include_channel=True)
            if deep_link:
                buttons.append([InlineKeyboardButton(label, url=deep_link)])
                logger.info(f"✅ @@@ Deep link (sin chiste): {label} → {deep_link}")
        else:
            # Cualquier otra cosa → Link DIRECTO
            processed_url = process_button_url(url_raw)
            if processed_url:
                buttons.append([InlineKeyboardButton(label, url=processed_url)])
                logger.info(f"✅ @@@ Link directo: {label} → {processed_url}")
    
    return buttons


# ============ COMANDOS ============

async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activa modo lote - CANCELA cualquier otro proceso activo"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    # CANCELAR cualquier proceso previo (ads, etc)
    context.user_data.clear()
    
    # Inicializar lote LIMPIO
    active_batches[user_id] = []
    batch_mode[user_id] = True
    
    await update.message.reply_text(
        "📦 **MODO LOTE ACTIVADO**\n\n"
        "Envía contenido (Encuestas, Fotos, Textos).\n\n"
        "**Sintaxis de botones:**\n"
        "🔹 `%%% t.me/canal/22` → Con chiste médico\n"
        "🔸 `@@@ Texto | t.me/canal/22` → Sin chiste\n"
        "🔸 `@@@ Texto | @usuario` → Link directo\n"
        "🔸 `@@@ Texto | web.com` → Link directo\n\n"
        "⚠️ Botón solo → se pega al mensaje anterior\n\n"
        "**/enviar** para publicar",
        parse_mode="Markdown"
    )


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela TODO - lote y cualquier otro proceso"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    # Limpiar TODO
    active_batches.pop(user_id, None)
    batch_mode[user_id] = False
    context.user_data.clear()
    
    await update.message.reply_text("🗑️ Cancelado.")


async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envía el lote al canal"""
    if not is_admin(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    # Verificar modo lote activo
    if not batch_mode.get(user_id, False):
        await update.message.reply_text("⚠️ Primero usa /lote")
        return
    
    items = active_batches.get(user_id, [])
    if not items:
        await update.message.reply_text("⚠️ Lote vacío. Envía contenido primero.")
        return
    
    status = await update.message.reply_text(f"🚀 Enviando {len(items)} elementos...")
    
    try:
        count = 0
        last_sent_message = None
        pending_buttons = []  # Acumular botones para el mensaje anterior
        
        for i, item in enumerate(items):
            item_type = item.get('type', '')
            
            # Si es botón para mensaje anterior
            if item_type == 'button_for_previous':
                # Siempre acumular los botones
                pending_buttons.extend(item.get('buttons_list', []))
                
                # Si no hay mensaje anterior, crear placeholder
                if not last_sent_message:
                    sent = await context.bot.send_message(
                        chat_id=PUBLIC_CHANNEL_ID,
                        text="💭 Contenido disponible:"
                    )
                    last_sent_message = sent
                    count += 1
                continue
            
            # Antes de enviar el siguiente mensaje, aplicar botones pendientes
            if pending_buttons and last_sent_message:
                try:
                    await context.bot.edit_message_reply_markup(
                        chat_id=PUBLIC_CHANNEL_ID,
                        message_id=last_sent_message.message_id,
                        reply_markup=InlineKeyboardMarkup(pending_buttons)
                    )
                    logger.info(f"✅ {len(pending_buttons)} botones asociados a msg {last_sent_message.message_id}")
                except Exception as e:
                    logger.error(f"Error asociando botones: {e}")
                pending_buttons = []
            
            # Enviar el item
            sent = await send_item_to_channel(context, item)
            if sent:
                last_sent_message = sent
                count += 1
            
            await asyncio.sleep(1.5)
        
        # Aplicar botones pendientes al último mensaje
        if pending_buttons and last_sent_message:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=PUBLIC_CHANNEL_ID,
                    message_id=last_sent_message.message_id,
                    reply_markup=InlineKeyboardMarkup(pending_buttons)
                )
                logger.info(f"✅ Botones finales asociados")
            except Exception as e:
                logger.error(f"Error asociando botones finales: {e}")
        
        await status.edit_text(f"✅ {count} elementos enviados al canal.")
    
    except Exception as e:
        logger.exception(f"Error en envío: {e}")
        await status.edit_text(f"❌ Error: {e}")
    
    finally:
        # Limpiar
        active_batches[user_id] = []
        batch_mode[user_id] = False


async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Procesa mensaje en modo lote"""
    user_id = update.effective_user.id
    
    # Verificar modo lote activo
    if not batch_mode.get(user_id, False):
        return False
    
    msg = update.message
    raw_text = msg.text or msg.caption or ""
    
    # Obtener username del bot
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    
    # Preparar item base
    item = {
        'chat_id': msg.chat_id,
        'msg_id': msg.message_id,
    }
    
    # ========== ENCUESTA ==========
    if msg.poll:
        item['type'] = 'poll'
        item['question'] = msg.poll.question
        item['options'] = [o.text for o in msg.poll.options]
        item['is_anonymous'] = True
        item['poll_type'] = msg.poll.type
        item['allows_multiple_answers'] = msg.poll.allows_multiple_answers
        item['correct_option_id'] = msg.poll.correct_option_id
        item['explanation'] = msg.poll.explanation
        item['explanation_entities'] = msg.poll.explanation_entities
        
        if msg.poll.type == 'quiz':
            if msg.poll.correct_option_id is not None:
                letra = chr(65 + msg.poll.correct_option_id)
                await msg.reply_text(f"✅ **ENCUESTA CAPTURADA**\nRespuesta correcta: **{letra}**", parse_mode="Markdown")
            else:
                await msg.reply_text(
                    "⚠️ **QUIZ SIN RESPUESTA DETECTADA**\n"
                    "Vota en la encuesta antes de enviarla.\n"
                    "Se usará opción A por defecto.",
                    parse_mode="Markdown"
                )
                item['correct_option_id'] = 0
        else:
            await msg.reply_text("✅ Encuesta capturada")
    
    # ========== SOLO BOTÓN(ES) ==========
    elif is_button_only_message(raw_text):
        buttons = await build_buttons(raw_text, bot_username)
        
        if buttons:
            item['type'] = 'button_for_previous'
            item['buttons_list'] = buttons
            await msg.reply_text("🔗 **BOTÓN CAPTURADO** (se asociará al mensaje anterior)", parse_mode="Markdown")
        else:
            await msg.reply_text("⚠️ Formato inválido. Usa: `@@@ Texto | link`", parse_mode="Markdown")
            return True
    
    # ========== CONTENIDO + BOTÓN ==========
    elif has_special_syntax(raw_text):
        buttons = await build_buttons(raw_text, bot_username)
        clean_text = clean_special_syntax(raw_text)
        
        if msg.photo or msg.video or msg.document or msg.audio:
            item['type'] = 'media'
        else:
            item['type'] = 'text'
        
        item['clean_text'] = clean_text
        item['buttons_list'] = buttons
        
        await msg.reply_text("✅ **MENSAJE + BOTÓN CAPTURADO**", parse_mode="Markdown")
    
    # ========== MENSAJE NORMAL ==========
    else:
        item['type'] = 'forward'
        await msg.reply_text("✅ Mensaje capturado")
    
    # Guardar en lote
    if user_id not in active_batches:
        active_batches[user_id] = []
    active_batches[user_id].append(item)
    
    return True


async def send_item_to_channel(context: ContextTypes.DEFAULT_TYPE, item: dict) -> Optional[object]:
    """Envía un item al canal público"""
    target = PUBLIC_CHANNEL_ID
    item_type = item.get('type', '')
    
    # Encuesta
    if item_type == 'poll':
        return await context.bot.send_poll(
            chat_id=target,
            question=item['question'],
            options=item['options'],
            is_anonymous=True,
            type=item['poll_type'],
            allows_multiple_answers=item.get('allows_multiple_answers', False),
            correct_option_id=item.get('correct_option_id'),
            explanation=item.get('explanation'),
            explanation_entities=item.get('explanation_entities')
        )
    
    # Forward simple
    if item_type == 'forward':
        return await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id']
        )
    
    # Media con botones
    if item_type == 'media':
        buttons = item.get('buttons_list', [])
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        
        return await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id'],
            caption=item.get('clean_text'),
            reply_markup=reply_markup
        )
    
    # Texto con botones
    if item_type == 'text':
        text = item.get('clean_text', '')
        buttons = item.get('buttons_list', [])
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
        
        if text:
            return await context.bot.send_message(
                chat_id=target,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=False
            )
    
    return None
