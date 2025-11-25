# -*- coding: utf-8 -*-
import logging
import asyncio
from typing import Optional, Dict
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import TelegramError

from config import PUBLIC_CHANNEL_ID, ADMIN_USER_IDS, TZ

logger = logging.getLogger(__name__)

# Estados para el flujo de creación de ADS
WAITING_AD_CONTENT, WAITING_AD_INTERVAL = range(2)

# Almacenamiento de ADS activas
active_ads: Dict[str, Dict] = {}
# {
#     "ad_id": {
#         "message_id": int,  # ID del mensaje en el canal
#         "content": dict,  # Contenido del AD
#         "interval_minutes": int,
#         "task": asyncio.Task
#     }
# }

def is_admin(user_id: int) -> bool:
    """Verifica si el usuario es admin"""
    return user_id in ADMIN_USER_IDS

async def cmd_set_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando para crear una nueva publicidad.
    Uso: /set_ads
    """
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Solo administradores pueden usar este comando")
        return
    
    welcome_text = (
        "📢 **Crear Nueva Publicidad**\n\n"
        "**Paso 1:** Envíame el contenido de la publicidad\n"
        "Puedes enviar:\n"
        "• Imagen + texto\n"
        "• Video + texto\n"
        "• Solo texto\n\n"
        "**Importante:** Si quieres agregar botones, usa el formato:\n"
        "`@@@ Texto del botón | URL`\n\n"
        "Envía el contenido ahora o /cancel para cancelar."
    )
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")
    
    # Guardar contexto para el siguiente paso
    context.user_data['creating_ad'] = True
    context.user_data['ad_content'] = {}

async def handle_ad_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el contenido de la publicidad"""
    if not context.user_data.get('creating_ad'):
        return
    
    msg = update.message
    
    # Guardar el contenido
    ad_content = {
        'text': msg.text or msg.caption or "",
        'photo': msg.photo[-1].file_id if msg.photo else None,
        'video': msg.video.file_id if msg.video else None,
        'document': msg.document.file_id if msg.document else None,
    }
    
    context.user_data['ad_content'] = ad_content
    
    # Detectar botones @@@
    from buttons_handler import BUTTON_PATTERN
    text = ad_content['text']
    button_matches = BUTTON_PATTERN.findall(text)
    
    buttons_info = ""
    if button_matches:
        buttons_info = f"\n✅ {len(button_matches)} botón(es) detectado(s)"
    
    # Solicitar intervalo
    interval_text = (
        f"✅ Contenido guardado{buttons_info}\n\n"
        "**Paso 2:** ¿Cada cuánto tiempo quieres publicar este AD?\n\n"
        "Opciones rápidas:\n"
        "• `10m` - Cada 10 minutos\n"
        "• `30m` - Cada 30 minutos\n"
        "• `1h` - Cada 1 hora\n"
        "• `3h` - Cada 3 horas\n"
        "• `6h` - Cada 6 horas\n"
        "• `12h` - Cada 12 horas\n"
        "• `24h` - Cada 24 horas\n\n"
        "O escribe tu propio intervalo (ej: `45m`, `2h`)\n\n"
        "Envía el intervalo o /cancel para cancelar."
    )
    
    await msg.reply_text(interval_text, parse_mode="Markdown")
    context.user_data['waiting_interval'] = True

async def handle_ad_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el intervalo y crea el AD"""
    if not context.user_data.get('waiting_interval'):
        return
    
    interval_text = update.message.text.strip().lower()
    
    # Parsear intervalo
    import re
    match = re.match(r'(\d+)(m|h)', interval_text)
    
    if not match:
        await update.message.reply_text(
            "❌ Formato inválido. Usa: `10m`, `1h`, `30m`, etc."
        )
        return
    
    value = int(match.group(1))
    unit = match.group(2)
    
    # Convertir a minutos
    if unit == 'h':
        interval_minutes = value * 60
    else:
        interval_minutes = value
    
    # Validar intervalo mínimo (5 minutos)
    if interval_minutes < 5:
        await update.message.reply_text(
            "❌ El intervalo mínimo es 5 minutos"
        )
        return
    
    # Crear el AD
    ad_id = f"ad_{datetime.now(tz=TZ).strftime('%Y%m%d_%H%M%S')}"
    ad_content = context.user_data['ad_content']
    
    try:
        # Publicar el primer AD inmediatamente
        message_id = await publish_ad(context, ad_content)
        
        if not message_id:
            await update.message.reply_text("❌ Error publicando el AD")
            return
        
        # Programar publicaciones periódicas
        task = asyncio.create_task(
            schedule_ad_republish(context, ad_id, ad_content, interval_minutes)
        )
        
        # Guardar en activos
        active_ads[ad_id] = {
            'message_id': message_id,
            'content': ad_content,
            'interval_minutes': interval_minutes,
            'task': task,
            'created_at': datetime.now(tz=TZ)
        }
        
        # Limpiar contexto
        context.user_data.clear()
        
        # Confirmar creación
        confirm_text = (
            f"✅ **Publicidad creada exitosamente**\n\n"
            f"🆔 ID: `{ad_id}`\n"
            f"⏱️ Intervalo: Cada {interval_minutes} minutos\n"
            f"📅 Primera publicación: Ahora\n"
            f"🔄 Siguiente: En {interval_minutes} minutos\n\n"
            f"Usa /list_ads para ver todas las publicidades activas\n"
            f"Usa /delete_ads para eliminar una publicidad"
        )
        
        await update.message.reply_text(confirm_text, parse_mode="Markdown")
        
        logger.info(f"✅ AD creado: {ad_id} (cada {interval_minutes}min)")
        
    except Exception as e:
        logger.error(f"❌ Error creando AD: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def publish_ad(context: ContextTypes.DEFAULT_TYPE, ad_content: dict) -> Optional[int]:
    """Publica un AD en el canal público"""
    try:
        text = ad_content['text']
        
        # Procesar botones si existen
        from buttons_handler import BUTTON_PATTERN
        button_matches = BUTTON_PATTERN.findall(text)
        
        reply_markup = None
        if button_matches:
            buttons = []
            for match in button_matches:
                label = match[0].strip()
                url = match[1].strip() if match[1] else None
                
                if label and url:
                    if not url.startswith(('http://', 'https://', 'tg://')):
                        if url.startswith('t.me/'):
                            url = 'https://' + url
                        elif '.' in url:
                            url = 'https://' + url
                    
                    buttons.append(InlineKeyboardButton(label, url=url))
            
            if buttons:
                keyboard = []
                for i in range(0, len(buttons), 2):
                    keyboard.append(buttons[i:i+2])
                reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Limpiar texto de botones
            text = BUTTON_PATTERN.sub('', text).strip()
        
        # Publicar según el tipo de contenido
        if ad_content['photo']:
            msg = await context.bot.send_photo(
                chat_id=PUBLIC_CHANNEL_ID,
                photo=ad_content['photo'],
                caption=text if text else None,
                reply_markup=reply_markup
            )
        elif ad_content['video']:
            msg = await context.bot.send_video(
                chat_id=PUBLIC_CHANNEL_ID,
                video=ad_content['video'],
                caption=text if text else None,
                reply_markup=reply_markup
            )
        elif ad_content['document']:
            msg = await context.bot.send_document(
                chat_id=PUBLIC_CHANNEL_ID,
                document=ad_content['document'],
                caption=text if text else None,
                reply_markup=reply_markup
            )
        else:
            msg = await context.bot.send_message(
                chat_id=PUBLIC_CHANNEL_ID,
                text=text if text else "📢 Publicidad",
                reply_markup=reply_markup
            )
        
        return msg.message_id
        
    except Exception as e:
        logger.error(f"❌ Error publicando AD: {e}")
        return None

async def schedule_ad_republish(
    context: ContextTypes.DEFAULT_TYPE,
    ad_id: str,
    ad_content: dict,
    interval_minutes: int
):
    """Programa la republicación periódica de un AD"""
    try:
        while True:
            # Esperar el intervalo
            await asyncio.sleep(interval_minutes * 60)
            
            # Eliminar el AD anterior si existe
            if ad_id in active_ads:
                old_message_id = active_ads[ad_id].get('message_id')
                if old_message_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=PUBLIC_CHANNEL_ID,
                            message_id=old_message_id
                        )
                        logger.info(f"🗑️ AD anterior eliminado: {old_message_id}")
                    except:
                        pass
            
            # Publicar nuevo AD
            new_message_id = await publish_ad(context, ad_content)
            
            if new_message_id and ad_id in active_ads:
                active_ads[ad_id]['message_id'] = new_message_id
                logger.info(f"🔄 AD republicado: {ad_id} (mensaje {new_message_id})")
            
    except asyncio.CancelledError:
        logger.info(f"⏹️ Tarea de AD cancelada: {ad_id}")
    except Exception as e:
        logger.error(f"❌ Error en republicación de AD {ad_id}: {e}")

async def cmd_list_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todas las publicidades activas"""
    if not is_admin(update.effective_user.id):
        return
    
    if not active_ads:
        await update.message.reply_text("📭 No hay publicidades activas")
        return
    
    ads_list = ["📢 **Publicidades Activas:**\n"]
    
    for ad_id, ad_info in active_ads.items():
        interval = ad_info['interval_minutes']
        created = ad_info['created_at'].strftime("%Y-%m-%d %H:%M")
        
        ads_list.append(
            f"• `{ad_id}`\n"
            f"  ⏱️ Cada {interval} minutos\n"
            f"  📅 Creado: {created}\n"
        )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Eliminar publicidad", callback_data="ads_delete")]
    ])
    
    await update.message.reply_text(
        "\n".join(ads_list),
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def cmd_delete_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Elimina una publicidad activa"""
    if not is_admin(update.effective_user.id):
        return
    
    if not active_ads:
        await update.message.reply_text("📭 No hay publicidades para eliminar")
        return
    
    # Mostrar lista con botones
    keyboard = []
    for ad_id in active_ads.keys():
        keyboard.append([
            InlineKeyboardButton(
                f"🗑️ {ad_id}",
                callback_data=f"ads_del_{ad_id}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("❌ Cancelar", callback_data="ads_cancel")
    ])
    
    await update.message.reply_text(
        "Selecciona la publicidad que deseas eliminar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_ads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja callbacks de publicidad"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
    
    data = query.data
    
    if data == "ads_delete":
        # Redirigir a cmd_delete_ads
        if not active_ads:
            await query.edit_message_text("📭 No hay publicidades para eliminar")
            return
        
        keyboard = []
        for ad_id in active_ads.keys():
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑️ {ad_id}",
                    callback_data=f"ads_del_{ad_id}"
                )
            ])
        
        keyboard.append([
            InlineKeyboardButton("❌ Cancelar", callback_data="ads_cancel")
        ])
        
        await query.edit_message_text(
            "Selecciona la publicidad que deseas eliminar:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith("ads_del_"):
        ad_id = data.replace("ads_del_", "")
        
        if ad_id not in active_ads:
            await query.edit_message_text("❌ Publicidad no encontrada")
            return
        
        try:
            # Cancelar tarea
            ad_info = active_ads[ad_id]
            if 'task' in ad_info:
                ad_info['task'].cancel()
            
            # Eliminar mensaje del canal
            if 'message_id' in ad_info:
                try:
                    await context.bot.delete_message(
                        chat_id=PUBLIC_CHANNEL_ID,
                        message_id=ad_info['message_id']
                    )
                except:
                    pass
            
            # Eliminar de activos
            del active_ads[ad_id]
            
            await query.edit_message_text(
                f"✅ Publicidad `{ad_id}` eliminada exitosamente",
                parse_mode="Markdown"
            )
            
            logger.info(f"🗑️ AD eliminado: {ad_id}")
            
        except Exception as e:
            await query.edit_message_text(f"❌ Error: {e}")
    
    elif data == "ads_cancel":
        await query.edit_message_text("❌ Operación cancelada")

# Handler para mensajes privados (creación de ADS)
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes privados durante la creación de ADS"""
    if not is_admin(update.effective_user.id):
        return
    
    if update.message.text and update.message.text.startswith('/'):
        return  # Es un comando, ignorar
    
    if context.user_data.get('creating_ad') and not context.user_data.get('waiting_interval'):
        await handle_ad_content(update, context)
    elif context.user_data.get('waiting_interval'):
        await handle_ad_interval(update, context)
