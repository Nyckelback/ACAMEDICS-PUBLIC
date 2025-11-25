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
# active_batches guarda una lista de diccionarios con la info del mensaje
active_batches: Dict[int, List[Dict[str, Any]]] = {}
batch_mode: Dict[int, bool] = {} 

# --- PATRONES REGEX ---
# 1. Detecta links de canales para justificación:
# Soporta: https://t.me/canal/123 Y TAMBIÉN https://t.me/c/123456789/123
CHANNEL_LINK_PATTERN = re.compile(r'%%%\s*(?:https?://)?t\.me/(?:c/|\w+/)+(\d+)', re.IGNORECASE)

# 2. Detecta botones personalizados: @@@ Texto | Link
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
        "Envía tus mensajes:\n"
        "1. **Encuestas/Quiz:** Envíalas tal cual.\n"
        "2. **Justificación:** Envía el link del canal con `%%%` antes.\n"
        "   Ej: `%%% https://t.me/just_clinicase/22`\n"
        "3. **Otros botones:** `@@@ Texto | Link`\n\n"
        "Finaliza con **/enviar**"
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
        await update.message.reply_text("⚠️ El lote está vacío. Envía algo primero.")
        return

    status_msg = await update.message.reply_text(f"🚀 Enviando {len(items)} mensajes...")

    try:
        sent_count = 0
        for item in items:
            await send_item_to_channel(context, item)
            sent_count += 1
            await asyncio.sleep(1.5) # Pausa para asegurar orden
        
        await status_msg.edit_text(f"✅ **¡Listo! {sent_count} mensajes publicados.**")
    except Exception as e:
        logger.error(f"Error publicando: {e}")
        await update.message.reply_text(f"❌ Error crítico: {e}")
    finally:
        active_batches[user_id] = []
        batch_mode[user_id] = False

async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Procesa y guarda el mensaje"""
    user_id = update.effective_user.id
    
    # Verificación estricta de modo
    if not batch_mode.get(user_id, False):
        return False

    msg = update.message
    
    # Preparar el item para guardar
    raw_text = msg.text or msg.caption or ""
    clean_text = raw_text
    buttons = []
    
    # 1. LOGICA DE JUSTIFICACIÓN (%%%)
    # Tu querías: %%% https://t.me/just_clinicase/20 -> Botón VER JUSTIFICACIÓN -> start=20
    just_match = CHANNEL_LINK_PATTERN.search(raw_text)
    if just_match:
        msg_id_target = just_match.group(1) # Extrae el numero "20"
        bot_info = await context.bot.get_me()
        
        # Deep Link: t.me/bot?start=20
        deep_link = f"https://t.me/{bot_info.username}?start={msg_id_target}"
        
        # Botón en mayúsculas como pediste
        buttons.append([InlineKeyboardButton("VER JUSTIFICACIÓN 💬", url=deep_link)])
        
        # Eliminamos el link del texto visible
        clean_text = CHANNEL_LINK_PATTERN.sub('', clean_text).strip()

    # 2. LOGICA DE OTROS BOTONES (@@@)
    custom_matches = BUTTON_PATTERN.findall(raw_text)
    if custom_matches:
        for label, url in custom_matches:
            # Limpieza básica de URL
            url = url.strip()
            if not url.startswith(('http', 'tg://')): url = 'https://' + url
            buttons.append([InlineKeyboardButton(label.strip(), url=url)])
        
        clean_text = BUTTON_PATTERN.sub('', clean_text).strip()

    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    # Si limpiamos todo el texto y quedaron botones, ponemos un emoji para que no falle
    if not clean_text and buttons and not (msg.photo or msg.video or msg.document or msg.poll):
        clean_text = "👇"

    # Guardamos todo lo necesario para reconstruir el mensaje
    item = {
        'msg_id': msg.message_id,
        'chat_id': msg.chat_id,
        'type': 'poll' if msg.poll else 'standard',
        'clean_text': clean_text,
        'reply_markup': reply_markup
    }

    if user_id not in active_batches:
        active_batches[user_id] = []
    
    active_batches[user_id].append(item)
    
    tipo = "Encuesta" if msg.poll else "Mensaje"
    await msg.reply_text(f"➕ {tipo} agregado correctamente.")
    return True

async def send_item_to_channel(context: ContextTypes.DEFAULT_TYPE, item: dict):
    """Envía el item al canal público"""
    target = PUBLIC_CHANNEL_ID
    
    if item['type'] == 'poll':
        # LAS ENCUESTAS SE COPIAN EXACTAS (Copy Message es lo único que mantiene la respuesta correcta)
        # Nota: Telegram NO permite poner botones (reply_markup) a las encuestas copiadas.
        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id']
        )
    
    else:
        # MENSAJES NORMALES (Texto, Foto, Video)
        # Aquí sí usamos copy_message pero inyectamos nuestro texto limpio y botones
        try:
            await context.bot.copy_message(
                chat_id=target,
                from_chat_id=item['chat_id'],
                message_id=item['msg_id'],
                caption=item['clean_text'], # Sobrescribimos el texto original (que tenía %%%)
                reply_markup=item['reply_markup'],
                parse_mode="Markdown"
            )
        except Exception as e:
            # Si falla copy_message (ej: es solo texto y copy_message a veces molesta con caption),
            # intentamos send_message si no hay media
            if item['clean_text']:
                 await context.bot.send_message(
                    chat_id=target,
                    text=item['clean_text'],
                    reply_markup=item['reply_markup'],
                    disable_web_page_preview=True
                )
