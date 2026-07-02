import logging
from .. import loader, utils

logger = logging.getLogger(__name__)

@loader.tds
class MusicBroadcastMod(loader.Module):
    """Прямая трансляция треков из Apple Shortcuts"""
    
    strings = {"name": "MusicBroadcast"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            "channel_id", 0, "ID канала",
            "message_id", 0, "ID сообщения для редактирования",
            "trigger_chat_id", 0, "ID чата, куда iPhone шлет команды",
            "default_title", "Мой канал", "Название, когда ничего не играет",
            "default_about", "⎯", "Описание, когда ничего не играет",
            "enabled", True, "Включен ли модуль (True/False)"
        )
        self._last_state = None

    async def client_ready(self, client, db):
        self._client = client

    @loader.watcher()
    async def watcher(self, message):
        if not self.config["enabled"]:
            return

        trigger_chat = self.config["trigger_chat_id"]
        if not trigger_chat or message.chat_id != int(trigger_chat):
            return

        text = message.raw_text or ""
        if not text.startswith(".settrack "):
            return

        args = text.split(".settrack ", 1)[1].strip()
        channel_id = int(self.config["channel_id"])
        message_id = int(self.config["message_id"])
        
        if not channel_id or not message_id:
            return

        # Проверка на остановку или пустой шаблон от iOS
        if args.lower() == "stop" or "itunes media" in args.lower() or not args.strip():
            new_title = self.config["default_title"]
            new_about = self.config["default_about"]
            new_text = "⎯"
            current_state = "stopped"
        else:
            try:
                track_name, artist_name = [x.strip() for x in args.split("—", 1)]
            except Exception:
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
            # Импортируем функцию прямо внутри, чтобы у ядра Hikka не было вопросов при установке
            from telethon.tl.functions.channels import EditTitleRequest, EditDescriptionRequest
            
            # Обновляем канал
            await self._client(EditTitleRequest(channel=channel_id, title=new_title))
            await self._client(EditDescriptionRequest(channel=channel_id, description=new_about))
            await self._client.edit_message(entity=channel_id, message=message_id, text=new_text)
            
            # Чистим за собой логи переименования
            async for msg in self._client.iter_messages(channel_id, limit=5):
                if msg.is_service:
                    await self._client.delete_messages(channel_id, msg.id)
        except Exception as e:
            logger.error(f"[Music] Ошибка: {e}")

    @loader.command()
    async def togglefmcmd(self, message):
        """Переключить работу трансляции музыки (Вкл/Выкл)"""
        state = not self.config["enabled"]
        self.config["enabled"] = state
        status_text = "<b>включена</b> 🟢" if state else "<b>выключена</b> 🔴"
        await utils.answer(message, f"<b>[Music]</b> Трансляция {status_text}")

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
