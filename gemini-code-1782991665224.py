import asyncio
import aiohttp
from telethon.tl.functions.channels import EditTitleRequest, EditDescriptionRequest
from telethon.tl.types import MessageService, MessageActionChatEditTitle
import logging

from .. import loader, utils

logger = logging.getLogger(__name__)

@loader.tds
class LastFmBroadcastMod(loader.Module):
    """Красивая трансляция треков Last.fm без мусора в истории"""
    
    strings = {"name": "LastFmBroadcast"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            "lastfm_username", "", "Имя пользователя Last.fm",
            "api_key", "", "API ключ Last.fm",
            "channel_id", 0, "ID канала (например, -1001234567890)",
            "message_id", 0, "ID сообщения для редактирования",
            "default_title", "Мой канал", "Название канала, когда музыка не играет",
            "default_about", "⎯", "Описание канала, когда музыка не играет"
        )
        self._task = None
        self._last_state = None

    async def client_ready(self, client, db):
        self._client = client
        self._task = asyncio.create_task(self._broadcast_loop())

    async def on_unload(self):
        if self._task:
            self._task.cancel()

    async def _broadcast_loop(self):
        while True:
            await self._update_track()
            await asyncio.sleep(15)

    async def _update_track(self):
        username = self.config["lastfm_username"]
        api_key = self.config["api_key"]
        channel_id = self.config["channel_id"]
        message_id = self.config["message_id"]

        if not username or not api_key or not channel_id or not message_id:
            return

        url = (
            f"http://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks"
            f"&user={username}&api_key={api_key}&format=json&limit=1"
        )

        is_playing = False
        track_name = ""
        artist_name = ""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        tracks = data.get("recenttracks", {}).get("track", [])
                        
                        if isinstance(tracks, list) and len(tracks) > 0:
                            track = tracks[0]
                        elif isinstance(tracks, dict):
                            track = tracks
                        else:
                            track = None

                        if track and track.get("@attr", {}).get("nowplaying") == "true":
                            is_playing = True
                            track_name = track.get("name", "Неизвестный трек")
                            artist_name = track.get("artist", {}).get("#text", "Неизвестный исполнитель")
        except Exception as e:
            logger.error(f"[LastFm] Ошибка API: {e}")
            return

        # Формируем данные на основе твоего нового дизайна
        if is_playing:
            new_title = track_name
            new_about = artist_name
            new_text = f"🎶 Сейчас играет: {track_name} — {artist_name}"
            current_state = f"{track_name}_{artist_name}"
        else:
            new_title = self.config["default_title"]
            new_about = self.config["default_about"]
            new_text = "⎯"
            current_state = "stopped"

        if self._last_state == current_state:
            return
            
        self._last_state = current_state

        try:
            channel_id_int = int(channel_id)
            message_id_int = int(message_id)
            
            # 1. Меняем название канала (теперь только трек)
            await self._client(EditTitleRequest(
                channel=channel_id_int,
                title=new_title
            ))

            # 2. Меняем описание (теперь исполнитель)
            await self._client(EditDescriptionRequest(
                channel=channel_id_int,
                description=new_about
            ))
            
            # 3. Редактируем закрепленный/выбранный пост
            await self._client.edit_message(
                entity=channel_id_int,
                message=message_id_int,
                text=new_text
            )

            # 4. Чистим за собой сервисные логи смены названия
            await self._clear_service_messages(channel_id_int)

        except Exception as e:
            logger.error(f"[LastFm] Ошибка обновления Telegram: {e}")
            self._last_state = None

    async def _clear_service_messages(self, channel_id):
        """Поиск и удаление системных сообщений о смене названия канала"""
        try:
            async for msg in self._client.iter_messages(channel_id, limit=5):
                if isinstance(msg, MessageService) and isinstance(msg.action, MessageActionChatEditTitle):
                    await self._client.delete_messages(channel_id, msg.id)
        except Exception as e:
            logger.error(f"[LastFm] Не удалось удалить сервисный лог: {e}")

    @loader.command()
    async def setfmcmd(self, message):
        """<username> <api_key> - Настроить данные Last.fm"""
        args = utils.get_args_raw(message).split()
        if len(args) != 2:
            await utils.answer(message, "<b>[LastFm]</b> Использование: <code>.setfm &lt;username&gt; &lt;api_key&gt;</code>")
            return
        self.config["lastfm_username"] = args[0]
        self.config["api_key"] = args[1]
        await utils.answer(message, f"<b>[LastFm]</b> Данные сохранены!👤: <code>{args[0]}</code>")

    @loader.command()
    async def setmusicidcmd(self, message):
        """<channel_id> <message_id> - Привязать ID канала и сообщения"""
        args = utils.get_args_raw(message).split()
        if len(args) != 2:
            await utils.answer(message, "<b>[LastFm]</b> Использование: <code>.setmusicid &lt;channel_id&gt; &lt;message_id&gt;</code>")
            return
        try:
            self.config["channel_id"] = int(args[0])
            self.config["message_id"] = int(args[1])
            await utils.answer(message, "<b>[LastFm]</b> ID успешно привязаны!")
        except ValueError:
            await utils.answer(message, "<b>[LastFm]</b> Ошибка: ID должны быть числами!")
