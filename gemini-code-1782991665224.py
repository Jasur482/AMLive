import asyncio
import aiohttp
from telethon.tl.functions.channels import EditTitleRequest
import logging

from .. import loader, utils

logger = logging.getLogger(__name__)

@loader.tds
class LastFmBroadcastMod(loader.Module):
    """Трансляция текущего трека из Last.fm в название канала и сообщение"""
    
    strings = {"name": "LastFmBroadcast"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            "lastfm_username", "", "Имя пользователя Last.fm",
            "api_key", "", "API ключ Last.fm",
            "channel_id", 0, "ID канала (например, -1001234567890)",
            "message_id", 0, "ID сообщения для редактирования"
        )
        self._task = None
        self._last_state = None  # Хранит текущий статус для избежания FloodWait

    async def client_ready(self, client, db):
        self._client = client
        # Запускаем фоновую задачу при старте модуля
        self._task = asyncio.create_task(self._broadcast_loop())

    async def on_unload(self):
        # Останавливаем задачу при выгрузке модуля
        if self._task:
            self._task.cancel()

    async def _broadcast_loop(self):
        """Фоновый цикл проверки трека каждые 15 секунд"""
        while True:
            await self._update_track()
            await asyncio.sleep(15)

    async def _update_track(self):
        """Получение данных с Last.fm и обновление Telegram"""
        username = self.config["lastfm_username"]
        api_key = self.config["api_key"]
        channel_id = self.config["channel_id"]
        message_id = self.config["message_id"]

        # Если конфиг не настроен, пропускаем итерацию
        if not username or not api_key or not channel_id or not message_id:
            return

        url = (
            f"http://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks"
            f"&user={username}&api_key={api_key}&format=json&limit=1"
        )

        is_playing = False
        track_name = ""
        artist_name = ""

        # Безопасный запрос к API Last.fm
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        tracks = data.get("recenttracks", {}).get("track", [])
                        
                        # Last.fm может вернуть как список, так и словарь
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
            logger.error(f"[LastFm] Ошибка при запросе к API: {e}")
            return # Прерываем обновление при ошибке сети, попробуем через 15 сек

        # Формируем новые данные
        if is_playing:
            new_title = f"🎧 {track_name} — {artist_name}"
            new_text = f"🎶 Сейчас играет: {track_name} — {artist_name}"
            current_state = f"{track_name}_{artist_name}" # Уникальный стейт трека
        else:
            new_title = "Сейчас ничего не играет"
            new_text = "⎯"
            current_state = "stopped"

        # Проверка на изменение данных (Защита от FloodWait)
        if self._last_state == current_state:
            return
            
        self._last_state = current_state

        # Обновление канала и сообщения
        try:
            channel_id_int = int(channel_id)
            message_id_int = int(message_id)
            
            # Смена названия канала
            await self._client(EditTitleRequest(
                channel=channel_id_int,
                title=new_title
            ))
            
            # Редактирование сообщения
            await self._client.edit_message(
                entity=channel_id_int,
                message=message_id_int,
                text=new_text
            )
        except Exception as e:
            logger.error(f"[LastFm] Ошибка при обновлении Telegram: {e}")
            self._last_state = None # Сбрасываем стейт, чтобы попробовать снова

    @loader.command()
    async def setfmcmd(self, message):
        """<username> <api_key> - Настроить данные Last.fm"""
        args = utils.get_args_raw(message).split()
        if len(args) != 2:
            await utils.answer(message, "<b>[LastFm]</b> Использование: <code>.setfm &lt;username&gt; &lt;api_key&gt;</code>")
            return
            
        self.config["lastfm_username"] = args[0]
        self.config["api_key"] = args[1]
        
        await utils.answer(
            message, 
            f"<b>[LastFm]</b> Данные успешно сохранены!\n"
            f"👤 Username: <code>{args[0]}</code>"
        )

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
            await utils.answer(
                message, 
                "<b>[LastFm]</b> ID канала и сообщения успешно сохранены!\n"
                "<i>Трансляция начнется в течение 15 секунд (если настроен конфиг Last.fm).</i>"
            )
        except ValueError:
            await utils.answer(message, "<b>[LastFm]</b> Ошибка: ID должны быть числами!")