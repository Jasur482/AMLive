import logging
from .. import loader, utils

logger = logging.getLogger(__name__)

@loader.tds
class MusicBroadcastMod(loader.Module):
    """Прямая трансляция треков через команду .settrackfm"""
    
    strings = {"name": "MusicBroadcast"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            "channel_id", 0, "ID канала",
            "message_id", 0, "ID сообщения для редактирования",
            "default_title", "Мой канал", "Название, когда ничего не играет",
            "default_about", "⎯", "Описание, когда ничего не играет"
        )
        self._last_state = None

    async def client_ready(self, client, db):
        self._client = client

    @loader.command()
    async def settrackfmcmd(self, message):
        """<текст> или <название — артист> — обновить статус музыки в канале"""
        args = utils.get_args_raw(message)
        channel_id = int(self.config["channel_id"])
        message_id = int(self.config["message_id"])
        
        if not channel_id or not message_id:
            await utils.answer(message, "<b>[Music]</b> Ошибка: настройте channel_id и message_id в .config")
            return

        args_clean = args.strip() if args else ""

        # Проверка на стоп-слова или мусорные заглушки от iOS
        if (args_clean.lower() in ["stop", "none", "остановить", "выкл"] or 
            "itunes media" in args_clean.lower() or 
            not args_clean):
            new_title = self.config["default_title"]
            new_about = self.config["default_about"]
            new_text = "⎯"
            current_state = "stopped"
        else:
            # Умное разделение на Трек и Артиста (поддерживает "—", "-" или просто строку)
            if "—" in args_clean:
                separator = "—"
            elif "-" in args_clean:
                separator = "-"
            else:
                separator = None

            if separator:
                try:
                    track_name, artist_name = [x.strip() for x in args_clean.split(separator, 1)]
                except Exception:
                    track_name = args_clean
                    artist_name = "Apple Music"
            else:
                track_name = args_clean
                artist_name = "Apple Music"

            new_title = track_name
            new_about = artist_name
            new_text = f"🎶 Сейчас играет: {track_name} — {artist_name}"
            current_state = f"{track_name}_{artist_name}"

        # Защита от повторных одинаковых запросов
        if self._last_state == current_state:
            await message.delete()
            return
        self._last_state = current_state

        try:
            from hikkatl.tl import functions

            # 1. Меняем название канала
            await self._client(functions.channels.EditTitleRequest(
                channel=channel_id, 
                title=new_title
            ))
            
            # 2. Меняем описание (о канале)
            await self._client(functions.messages.EditChatDescriptionRequest(
                peer=channel_id, 
                description=new_about
            ))
            
            # 3. Редактируем закрепленный/выбранный пост
            await self._client.edit_message(
                entity=channel_id, 
                message=message_id, 
                text=new_text
            )
            
            # Удаляем саму команду .settrackfm из чата, чтобы не оставлять мусор
            await message.delete()

            # 4. Удаляем сервисный лог в самом канале ("Название канала изменено на...")
            async for msg in self._client.iter_messages(channel_id, limit=5):
                if msg.is_service:
                    await self._client.delete_messages(channel_id, msg.id)

        except Exception as e:
            logger.error(f"[Music] Ошибка обновления: {e}")
