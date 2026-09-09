"""Explicitly approved vignette images and attribution shared with the Mini App."""
import json
from pathlib import Path
from html import escape

ASSETS = json.loads((Path(__file__).parent / "miniapp/case_images.json").read_text())

async def send_case_images(bot, chat_id, case_id):
    for asset in ASSETS.get(str(case_id), []):
        caption = (escape(asset["credit"]) + '\n'
                   + '<a href="' + escape(asset["source"], quote=True) + '">Fuente</a> · '
                   + '<a href="' + escape(asset["license"], quote=True) + '">'
                   + escape(asset["license_label"]) + '</a>')
        await bot.send_photo(chat_id=chat_id, photo=asset["url"],
                             caption=caption, parse_mode="HTML")
