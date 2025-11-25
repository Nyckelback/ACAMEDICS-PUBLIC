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
active_batches: Dict[int, List[int]] = {} # Guardamos solo los IDs de los mensajes
batch_mode: Dict[int, bool] = {} 

# --- PATRONES REGEX (MODIFICADOS) ---
# 1. Detecta links de canales: https://t.me/canal/123  o  https://t.me/c/123456789/123
# Captura el ID numérico final.
CHANNEL_LINK_PATTERN = re.compile(r'%%%\s*(?:https?://)?t\.me/(?:c/)?[\w_]+/(?P<id>\d+)', re.IGNORECASE)

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
    await update.message.reply_text("📦 **MODO LOTE**\nEnvía lo que quieras (Encuestas, Fotos, etc).\n\nPara justificaciones, envía un mensaje con:\n`%%% https://t.me/tu_canal/123`")

async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    active_batches.pop(user_id, None)
    batch_mode[user_id] = False
    await update.message.reply_text("🗑️ Cancelado.")

async def cmd_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publica todo tal cual al canal"""
    if not is_admin(update.effective_user.id): return
    user_id = update.effective_user.id
    
    # Obtenemos los IDs de los mensajes que el admin le mandó al bot
    msg_ids = active_batches.get(user_id, [])
    if not msg_ids:
        await update.message.reply_text("⚠️ Nada que enviar.")
        return

    await update.message.reply_text(f"🚀 Enviando {len(msg_ids)} mensajes...")

    try:
        # Iteramos sobre los mensajes guardados
        for msg_id in msg_ids:
            # Recuperamos el mensaje original enviado por el admin al bot
            # Nota: No guardamos el objeto, lo procesamos al vuelo para tener datos frescos
            await process_and_send(context, user_id, msg_id)
            await asyncio.sleep(1) # Pausa de seguridad
        
        await update.message.reply_text("✅ **Listo**")
    except Exception as e:
        logger.error(f"Error lote: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        active_batches[user_id] = []
        batch_mode[user_id] = False

async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Solo guarda el ID del mensaje para procesarlo luego"""
    user_id = update.effective_user.id
    if not batch_mode.get(user_id, False):
        return False

    # Guardamos el ID del mensaje
    if user_id not in active_batches:
        active_batches[user_id] = []
    
    active_batches[user_id].append(update.message.message_id)
    
    # Feedback simple
    tipo = "Encuesta" if update.message.poll else "Mensaje"
    await update.message.reply_text(f"➕ {tipo} guardado")
    return True

async def process_and_send(context: ContextTypes.DEFAULT_TYPE, from_chat_id: int, message_id: int):
    """Logica CORE: Analiza el mensaje original y lo envía transformado al canal"""
    
    # 1. Copiamos el mensaje del admin para analizarlo (sin enviarlo aun al canal publico)
    # Esto es un truco: leemos el mensaje que el admin YA mandó al bot.
    # Pero como 'active_batches' solo tiene IDs, necesitamos acceder al contenido.
    # Telegram Bot API no deja "leer" historial arbitrario, pero ya tenemos el update.
    # MEJOR ESTRATEGIA: Copiar del chat del admin al canal publico DIRECTAMENTE, modificando lo necesario.

    bot = context.bot
    target = PUBLIC_CHANNEL_ID
    
    # Truco: Copiamos el mensaje al chat privado del bot (si mismo) para ver su contenido? 
    # No, no podemos ver contenido de copy_message.
    # Corrección: Debemos confiar en que el mensaje original (message_id) en el chat (from_chat_id) sigue existiendo.
    # Pero no podemos "leer" el texto para buscar %%% sin tener el objeto Message.
    # POR ESO, en handle_batch_message DEBIMOS guardar el contenido si era texto/caption.
    # Vamos a ajustar `handle_batch_message` para guardar el objeto Message en memoria es arriesgado si se reinicia, 
    # pero para este flujo rápido está bien. 
    # VAMOS A SIMPLIFICAR: No guardaremos objetos, haremos el análisis "in situ" en handle_batch_message y guardaremos DICTS.
    pass 

# --- REESCRIBIENDO LA LÓGICA DE ALMACENAMIENTO PARA QUE FUNCIONE ---
# Reemplaza todo lo de arriba con esto corregido:

async def handle_batch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if not batch_mode.get(user_id, False): return False

    msg = update.message
    
    # Analizamos el texto AHORA que tenemos el mensaje
    raw_text = msg.text or msg.caption or ""
    clean_text = raw_text
    buttons = []
    
    # 1. BUSCAR %%% (Tu link original: https://t.me/just_clinicase/22)
    just_match = CHANNEL_LINK_PATTERN.search(raw_text)
    if just_match:
        content_id = just_match.group('id') # Extrae "22"
        bot_info = await context.bot.get_me()
        # CREA EL LINK: https://t.me/mibot?start=22
        deep_link = f"https://t.me/{bot_info.username}?start={content_id}"
        
        # BOTÓN EN MAYÚSCULAS
        buttons.append([InlineKeyboardButton("VER JUSTIFICACIÓN 💬", url=deep_link)])
        clean_text = CHANNEL_LINK_PATTERN.sub('', clean_text).strip()

    # 2. BUSCAR @@@ (Botones custom)
    custom_matches = BUTTON_PATTERN.findall(raw_text)
    if custom_matches:
        for label, url in custom_matches:
            buttons.append([InlineKeyboardButton(label.strip(), url=url.strip())])
        clean_text = BUTTON_PATTERN.sub('', clean_text).strip()

    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    # Si era SOLO el link, el texto queda vacío. Ponemos un placeholder.
    if not clean_text and buttons and not (msg.photo or msg.video or msg.poll):
        clean_text = "👇"

    # Guardamos la estructura procesada
    item = {
        'msg_id': msg.message_id,
        'chat_id': msg.chat_id,
        'type': 'poll' if msg.poll else ('media' if (msg.photo or msg.video or msg.document) else 'text'),
        'clean_text': clean_text,
        'reply_markup': reply_markup
    }

    if user_id not in active_batches: active_batches[user_id] = []
    active_batches[user_id].append(item) # Guardamos el dict
    
    await msg.reply_text("➕ Agregado")
    return True

async def process_and_send(context, user_id, msg_id):
    # Esta función ya no se usa con el nuevo enfoque de arriba
    pass

async def send_item_to_channel(context: ContextTypes.DEFAULT_TYPE, item: dict):
    target = PUBLIC_CHANNEL_ID
    
    if item['type'] == 'poll':
        # ENCUESTAS: Se copian EXACTAS (Mantiene quiz y respuesta correcta)
        # Nota: Telegram no permite poner botones inline a encuestas. Se van solas.
        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id']
        )
    
    elif item['type'] == 'media':
        # FOTOS/VIDEOS: Usamos copy_message pero reemplazamos el caption
        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=item['chat_id'],
            message_id=item['msg_id'],
            caption=item['clean_text'],
            reply_markup=item['reply_markup'],
            parse_mode="Markdown"
        )
        
    elif item['type'] == 'text':
        # TEXTO: Usamos send_message para mandar el texto limpio
        if item['clean_text']:
            await context.bot.send_message(
                chat_id=target,
                text=item['clean_text'],
                reply_markup=item['reply_markup'],
                disable_web_page_preview=True,
                parse_mode="Markdown"
            )
