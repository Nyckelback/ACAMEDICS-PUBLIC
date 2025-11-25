# -*- coding: utf-8 -*-
import logging
import re
from typing import List, Tuple, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# Patrón para detectar @@@ botones
# Formato: @@@ Texto | URL  o  @@@ Texto solo
BUTTON_PATTERN = re.compile(
    r'@@@\s*([^|]+?)(?:\s*\|\s*(.+))?$',
    re.MULTILINE
)

async def handle_button_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Detecta mensajes con @@@ y agrega botones personalizados.
    
    Formatos soportados:
    - @@@ Texto | https://ejemplo.com  (botón con link)
    - @@@ Texto solo  (botón sin link, solo display)
    
    Soporta múltiples botones en un mismo mensaje.
    """
    msg = update.channel_post
    if not msg:
        return
    
    text = msg.text or msg.caption or ""
    
    # Buscar todos los botones @@@
    matches = BUTTON_PATTERN.findall(text)
    if not matches:
        return
    
    try:
        # Crear lista de botones
        buttons = []
        
        for match in matches:
            label = match[0].strip()
            url = match[1].strip() if match[1] else None
            
            if not label:
                continue
            
            # Si tiene URL, crear botón con link
            if url:
                # Asegurar que la URL tenga protocolo
                if not url.startswith(('http://', 'https://', 'tg://')):
                    if url.startswith('t.me/'):
                        url = 'https://' + url
                    elif '.' in url:
                        url = 'https://' + url
                
                buttons.append(InlineKeyboardButton(label, url=url))
            else:
                # Botón sin link (callback_data vacío, solo display)
                buttons.append(InlineKeyboardButton(label, callback_data="none"))
        
        if not buttons:
            logger.warning("No se encontraron botones válidos")
            return
        
        # Organizar botones (2 por fila)
        keyboard = []
        for i in range(0, len(buttons), 2):
            row = buttons[i:i+2]
            keyboard.append(row)
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Limpiar el texto (remover todas las líneas @@@)
        clean_text = BUTTON_PATTERN.sub('', text).strip()
        
        # Si el mensaje tiene contenido multimedia, editar caption
        if msg.photo or msg.video or msg.document:
            await msg.edit_caption(
                caption=clean_text if clean_text else None,
                reply_markup=reply_markup
            )
        else:
            # Si es solo texto, editar el texto
            if clean_text:
                await msg.edit_text(
                    text=clean_text,
                    reply_markup=reply_markup
                )
            else:
                # Si no hay texto adicional, mantener un mensaje mínimo
                await msg.edit_text(
                    text="👆 Opciones disponibles",
                    reply_markup=reply_markup
                )
        
        logger.info(f"✅ {len(buttons)} botón(es) personalizado(s) creado(s)")
        
    except Exception as e:
        logger.error(f"❌ Error creando botones: {e}")
