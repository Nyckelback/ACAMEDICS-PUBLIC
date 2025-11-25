# -*- coding: utf-8 -*-
import logging
import asyncio
import re
from typing import Dict, List, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS

logger = logging.getLogger(__name__)

# --- ALMACENAMIENTO TEMPORAL (Solo el lote actual) ---
active_batches: Dict[int, List[Dict[str, Any]]] = {}
batch_mode: Dict[int, bool] = {} 

# --- PATRONES REGEX ---
# 1. %%%: Detecta enlaces deep link estándar y los convierte en "Ver justificación 💬"
JUSTIFICATION_PATTERN = re.compile(r'%%%\s*https?://t\.me/[^\s]+\?start=jst_(\d+(?:-\d+)*)', re.IGNORECASE)

# 2. @@@: Detecta botones personalizados. Formato: @@@ Texto | URL
BUTTON_PATTERN = re.compile(r'@@@\s*([^|\n]+?)(?:\s*\|\s*(.+))?$', re.MULTILINE)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el modo de captura"""
    user_id = update.effective_user.id
    if not is_admin(user_id): return

    active_batches[user_id] = []
    batch_mode[user_id] = True
    
    await update.message.reply_text(
        "📦 **MODO LOTE ACTIVADO**\n\n"
        "Envía todo el contenido (textos, fotos, pdfs).\n\n"
        "🔹 **Para Justificación:** Pega el link con `%%%`\n"
        "🔸 **Para Otro Botón:** Usa `@@@ Texto | Link`\n\n"
        "Cuando termines, usa /enviar o /cancelar"
    )

async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Borra todo y sale del modo"""
    user_id = update.effective_user.id
    if not is_admin(user_id): return

    active_batches.pop(user_id, None)
    batch_mode[user_id] = False
    await update.message.reply_text("🗑️ Lote eliminado.")

async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa y publica al canal público"""
    user_id = update.effective_user.id
    if not is_admin(user_id): return
    
    items = active_batches.get(user_id, [])
    if not items:
        await update.message.reply_text("⚠️ El lote está vacío.")
        return

    await update.message.reply_text(f"🚀 Publicando {len(items)} mensajes...")

    try:
        for item in items:
            await send_processed_item(context, PUBLIC_CHANNEL_ID, item)
            await asyncio.sleep(1) # Pequeña pausa para orden
        
        await update.message.reply_text("✅ **Publicación completada**")
    except Exception as e:
        logger.error(f"Error publicando: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        active_batches[user_id] = []
        batch_mode[user_id] = False

async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Captura el contenido si está en modo lote"""
    user_id = update.effective_user.id
    if not batch_mode.get(user_id, False):
        return False

    msg = update.message
    # Guardar estructura básica
    item = {
        'text': msg.text or msg.caption or "",
        'photo': msg.photo[-1].file_id if msg.photo else None,
        'video': msg.video.file_id if msg.video else None,
        'document': msg.document.file_id if msg.document else None
    }
    
    if user_id not in active_batches:
        active_batches[user_id] = []
    
    active_batches[user_id].append(item)
    await msg.reply_text("➕ Agregado")
    return True

async def send_processed_item(context: ContextTypes.DEFAULT_TYPE, chat_id: int, item: dict):
    """Lógica CORE: Detecta etiquetas, crea botones y limpia el texto"""
    text = item['text']
    buttons = []
    
    # 1. PROCESAR %%% (Justificaciones automáticas)
    just_match = JUSTIFICATION_PATTERN.search(text)
    if just_match:
        ids_string = just_match.group(1)
        bot_info = await context.bot.get_me()
        deep_link = f"https://t.me/{bot_info.username}?start=jst_{ids_string}"
        
        # Botón fijo para justificaciones
        buttons.append([InlineKeyboardButton("Ver justificación 💬", url=deep_link)])
        text = JUSTIFICATION_PATTERN.sub('', text).strip() # Borrar la etiqueta

    # 2. PROCESAR @@@ (Botones personalizados: PDFs, Links externos, etc)
    custom_matches = BUTTON_PATTERN.findall(text)
    custom_rows = []
    for label, url in custom_matches:
        label = label.strip()
        url = url.strip()
        
        if label and url:
            # Asegurar protocolo
            if not url.startswith(('http', 'tg://')):
                url = 'https://' + url
            custom_rows.append(InlineKeyboardButton(label, url=url))
    
    # Agregar botones personalizados (de 1 en 1 o 2 en 2 si prefieres, aquí van apilados)
    for btn in custom_rows:
        buttons.append([btn])
        
    if custom_matches:
        text = BUTTON_PATTERN.sub('', text).strip() # Borrar las etiquetas

    # 3. ENVIAR AL CANAL
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    
    # Si borramos todo el texto (solo eran etiquetas) y no hay media, poner un punto o emoji
    if not text and not (item['photo'] or item['video'] or item['document']):
        text = "📢"

    if item['photo']:
        await context.bot.send_photo(chat_id, photo=item['photo'], caption=text, reply_markup=reply_markup)
    elif item['video']:
        await context.bot.send_video(chat_id, video=item['video'], caption=text, reply_markup=reply_markup)
    elif item['document']:
        await context.bot.send_document(chat_id, document=item['document'], caption=text, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id, text=text, reply_markup=reply_markup, disable_web_page_preview=True)
