import logging
import asyncio
import aiohttp
from .. import loader, utils

logger = logging.getLogger(__name__)

@loader.tds
class MusicBroadcastMod(loader.Module):
    """Умная трансляция треков Apple Music с ручным и автоматическим режимом"""
    
    strings = {"name": "MusicBroadcast"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            "channel_id", 0, "ID канала",
            "message_id", 0, "ID сообщения для редактирования",
            "trigger_chat_id", 0, "ID чата (Kira), куда можно слать .settrack вручную",
            "lastfm_username", "", "Твой логин на Last.fm (для авто-режима)",
            "lastfm_api_key", "", "Твой API Key от Last.fm (для авто-режима)",
            "default_title", "Мой канал", "Название, когда ничего не играет",
            "default_about", "⎯", "Описание, когда ничего не играет",
            "enabled", True, "Включен ли модуль (True/False)"
        )
        self._last_state = None
        self._task = None

    async def client_ready(self, client, db):
        self._client = client
        # Запуск фонового облачного отслеживания
        self._task = asyncio.create_task(self._loop())

    async def on_unload(self):
        if self._task:
            self._task.cancel()

    async def _update_channel(self, args):
        """Единая умная логика форматирования и обновления канала"""
        channel_id = int(self.config["channel_id"])
        message_id = int(self.config["message_id"])
        if not channel_id or not message_id:
            return

        args_clean = args.strip()

        # Если пришел пустой шаблон, стоп или мусор от iTunes Media
        if (args_clean.lower() in ["stop", "none", "остановить"] or 
            "itunes media" in args_clean.lower() or 
            not args_clean):
            new_title = self.config["default_title"]
            new_about = self.config["default_about"]
            new_text = "⎯"
            current_state = "stopped"
        else:
            # Умный парсинг формата "Трек — Артист", "Трек - Артист" или просто "Трек"
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

        if self._last_state == current_state:
            return
        self._last_state = current_state

        try:
            from hikkatl.tl import functions
            # Обновляем название
            await self._client(functions.channels.EditTitleRequest(channel=channel_id, title=new_title))
            # Обновляем описание
            await self._client(functions.messages.EditChatDescriptionRequest(peer=channel_id, description=new_about))
            # Обновляем пост
            await self._client.edit_message(entity=channel_id, message=message_id, text=new_text)
            
            # Чистим за собой сервисные сообщения в канале
            async for msg in self._client.iter_messages(channel_id, limit=5):
                if msg.is_service:
                    await self._client.delete_messages(channel_id, msg.id)
        except Exception as e:
            logger.error(f"[Music] Ошибка при обновлении канала: {e}")

    @loader.watcher()
    async def watcher(self, message):
        """Ручной режим: парсит команду .settrack из выбранного чата"""
        if not self.config["enabled"]:
            return

        trigger_chat = self.config["trigger_chat_id"]
        if not trigger_chat or message.chat_id != int(trigger_chat):
            return

        text = message.raw_text or ""
        if not text.startswith(".settrack"):
            return

        # Извлекаем текст после команды (поддерживает как ".settrack текст", так и просто ".settrack")
        args = text.split(".settrack", 1)[1].strip()
        await self._update_channel(args)

    async def _loop(self):
        """Автоматический режим: раз в 15 секунд тихо опрашивает облако Last.fm"""
        while True:
            try:
                if (not self.config["enabled"] or 
                    not self.config["lastfm_api_key"] or 
                    not self.config["lastfm_username"]):
                    await asyncio.sleep(15)
                    continue

                url = f"https://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={self.config['lastfm_username']}&api_key={self.config['lastfm_api_key']}&format=json&limit=1"
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=5) as response:
                        if response.status != 200:
                            await asyncio.sleep(15)
                            continue
                        data = await response.json()

                tracks = data.get("recenttracks", {}).get("track", [])
                if not tracks:
                    await asyncio.sleep(15)
                    continue

                track = tracks[0] if isinstance(tracks, list) else tracks
                is_now_playing = track.get("@attr", {}).get("nowplaying") == "true"

                if is_now_playing:
                    track_name = track.get("name", "Unknown Track")
                    artist_name = track.get("artist", {}).get("#text", "Unknown Artist")
                    await self._update_channel(f"{track_name} — {artist_name}")
                else:
                    await self._update_channel("stop")

            except Exception as e:
                logger.error(f"[Music] Ошибка в облачном цикле: {e}")
            
            await asyncio.sleep(15)

    @loader.command()
    async def togglefmcmd(self, message):
        """Переключить трансляцию музыки (Вкл/Выкл)"""
        state = not self.config["enabled"]
        self.config["enabled"] = state
        status_text = "<b>включена</b> 🟢" if state else "<b>выключена</b> 🔴"
        await utils.answer(message, f"<b>[Music]</b> Трансляция {status_text}")

    @loader.command()
    async def setmusicidcmd(self, message):
        """<channel_id> <message_id> <trigger_chat_id> - Быстрая привязка ID"""
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
