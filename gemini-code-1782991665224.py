import asyncio
import logging
from telethon import events
from telethon.tl.functions.channels import EditTitleRequest, EditDescriptionRequest
from telethon.tl.types import MessageService, MessageActionChatEditTitle

from .. import loader, utils

logger = logging.getLogger(__name__)

@loader.tds
class MusicBroadcastMod(loader.Module):
    """Прямая трансляция треков из Apple Shortcuts через чат-посредник"""
    
    strings = {"name": "MusicBroadcast"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            "channel_id", 0, "ID канала",
            "message_id", 0, "ID сообщения для редактирования",
            "trigger_chat_id", 0, "ID чата (Kira), куда iPhone шлет команды",
            "default_title", "Мой канал", "Название, когда ничего не играет",
            "default_about", "⎯", "Описание, когда ничего не играет"
        )
        self._last_state = None

    async def client_ready(self, client, db):
        self._client = client

    @loader.watcher()  # Убрали incoming=True, чтобы ловить исходящие от тебя сообщения
    async def watcher(self, message):
        trigger_chat = self.config["trigger_chat_id"]
        if not trigger_chat or message.chat_id != int(trigger_chat):
            return

        text = message.raw_text or ""
        if not text.startswith(".settrack "):
            return

        # Извлекаем аргументы после .settrack
        args = text.split(".settrack ", 1)[1].strip()
        channel_id = int(self.config["channel_id"])
        message_id = int(self.config["message_id"])
        
        if not channel_id or not message_id:
            return

        # Логика обработки трека
        if args.lower() == "stop":
            new_title = self.config["default_title"]
            new_about = self.config["default_about"]
            new_text = "⎯"
            current_state = "stopped"
        else:
            try:
                track_name, artist_name = [x.strip() for x in args.split("—", 1)]
            except ValueError:
                track_name = args
                artist_name = "Apple Music"
            
            new_title = track_name
            new_about = artist_name
            new_text = f"🎶 Сейчас играет: {track_name} — {artist_name}"
            current_state = f"{track_name}_{artist_name}"

        if self._last_state == current_state:
            return
        self._last_state = current_state

        try:
            # Обновляем целевой канал
            await self._client(EditTitleRequest(channel=channel_id, title=new_title))
            await self._client(EditDescriptionRequest(channel=channel_id, description=new_about))
            await self._client.edit_message(entity=channel_id, message=message_id, text=new_text)
            
            # Удаляем системный спам «Channel renamed to...»
            async for msg in self._client.iter_messages(channel_id, limit=3):
                if isinstance(msg, MessageService) and isinstance(msg.action, MessageActionChatEditTitle):
                    await self._client.delete_messages(channel_id, msg.id)
        except Exception as e:
            logger.error(f"[Music] Ошибка обновления: {e}")

    @loader.command()
    async def setmusicidcmd(self, message):
        """<channel_id> <message_id> <trigger_chat_id> - Привязать все ID"""
        args = utils.get_args_raw(message).split()
        if len(args) != 3:
            await utils.answer(message, "Использование: <code>.setmusicid &lt;channel_id&gt; &lt;message_id&gt; &lt;trigger_chat_id&gt;</code>")
            return
        try:
            self.config["channel_id"] = int(args[0])
            self.config["message_id"] = int(args[1])
            self.config["trigger_chat_id"] = int(args[2])
            await utils.answer(message, "<b>[Music]</b> Все ID успешно привязаны!")
        except ValueError:
            await utils.answer(message, "<b>[Music]</b> Ошибка: ID должны быть числами!")
