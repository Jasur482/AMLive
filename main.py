import logging
import asyncio
from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class MusicBroadcastMod(loader.Module):
    """Трансляция трека из Last.fm (now playing) в название канала + ручной ввод через .settrackfm"""

    strings = {"name": "MusicBroadcast"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            "channel_id", 0, "ID канала",
            "message_id", 0, "ID сообщения для редактирования",
            "default_title", "Мой канал", "Название, когда ничего не играет",
            "lastfm_user", "", "Юзернейм на Last.fm",
            "lastfm_api_key", "", "API key с last.fm/api/account/create",
            "poll_interval", 15, "Интервал опроса Last.fm, сек (не меньше 10)",
            "auto_poll", True, "Включить автоопрос Last.fm при старте",
        )
        self._last_state = None
        self._task = None
        self._session = None
        self._manual_override = False  # Флаг ручного ввода

    async def client_ready(self, client, db):
        self._client = client
        import aiohttp
        self._session = aiohttp.ClientSession()
        if self.config["auto_poll"]:
            self._task = asyncio.ensure_future(self._poll_loop())

    async def on_unload(self):
        if self._task:
            self._task.cancel()
        if self._session:
            await self._session.close()

    # ---------- автоопрос Last.fm ----------

    async def _poll_loop(self):
        await asyncio.sleep(3)
        while True:
            try:
                await self._check_lastfm()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[Music] Ошибка опроса Last.fm: {e}")
            # Ограничение снизу изменено до безопасных 10 секунд для защиты от бана API
            await asyncio.sleep(max(10, int(self.config["poll_interval"])))

    async def _check_lastfm(self):
        user = self.config["lastfm_user"]
        api_key = self.config["lastfm_api_key"]
        if not user or not api_key:
            return

        url = "https://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "user.getrecenttracks",
            "user": user,
            "api_key": api_key,
            "format": "json",
            "limit": 1,
        }

        try:
            async with self._session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        except Exception as e:
            logger.error(f"[Music] Last.fm недоступен: {e}")
            return

        # Безопасный парсинг ответа
        if not isinstance(data, dict) or "recenttracks" not in data:
            return

        tracks = data.get("recenttracks", {}).get("track", [])
        if not tracks:
            # Если включен ручной режим, автоопрос не сбрасывает его на "stopped"
            if not self._manual_override:
                await self._apply_stopped()
            return

        track = tracks[0]
        is_now_playing = track.get("@attr", {}).get("nowplaying") == "true"

        if not is_now_playing:
            if not self._manual_override:
                await self._apply_stopped()
            return

        track_name = (track.get("name") or "").strip()
        artist_name = (track.get("artist", {}).get("#text") or "").strip()

        if not track_name:
            if not self._manual_override:
                await self._apply_stopped()
            return

        # Сверяем входящий трек с тем, что крутится в модуле
        incoming_state = f"{track_name}_{artist_name}"
        
        if self._manual_override:
            # Если на Last.fm заиграл трек, отличный от того, что мы ввели вручную — снимаем блокировку
            if self._last_state != incoming_state:
                self._manual_override = False
                await self._apply_track(track_name, artist_name)
        else:
            await self._apply_track(track_name, artist_name)

    # ---------- общая логика применения состояния ----------

    async def _apply_stopped(self):
        current_state = "stopped"
        if self._last_state == current_state:
            return
        self._last_state = current_state
        await self._update_channel(self.config["default_title"], "⎯")

    async def _apply_track(self, track_name, artist_name):
        current_state = f"{track_name}_{artist_name}"
        if self._last_state == current_state:
            return
        self._last_state = current_state
        await self._update_channel(track_name, artist_name)

    async def _update_channel(self, title, text):
        channel_id = int(self.config["channel_id"])
        message_id = int(self.config["message_id"])

        if not channel_id or not message_id:
            logger.error("[Music] Не настроены channel_id/message_id в .config")
            return

        try:
            from hikkatl.tl import functions

            # 1. Меняем название канала
            await self._client(functions.channels.EditTitleRequest(
                channel=channel_id,
                title=title
            ))

            # 2. Редактируем пост (только артист)
            await self._client.edit_message(
                entity=channel_id,
                message=message_id,
                text=text
            )

            # Даём серверу время сгенерировать сервисную плашку переименования
            await asyncio.sleep(0.5)

            # 3. Находим и удаляем сервисный лог ("Название изменено на...")
            messages = await self._client.get_messages(channel_id, limit=1)
            if messages and messages[0].action:
                await messages[0].delete()

        except Exception as e:
            logger.error(f"[Music] Ошибка обновления канала: {e}")

    # ---------- команды ----------

    @loader.command()
    async def settrackfmcmd(self, message):
        """<текст> — вручную обновить название канала (перекрывает автоопрос до следующей смены трека)"""
        args = utils.get_args_raw(message)
        args_clean = args.strip() if args else ""

        if (args_clean.lower() in ["stop", "none", "остановить", "выкл"] or
                "itunes media" in args_clean.lower() or
                not args_clean):
            self._manual_override = False  # Сбрасываем ручной режим при явной остановке
            await self._apply_stopped()
            await message.delete()
            return

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

        self._manual_override = True  # Включаем защиту ручного ввода
        await self._apply_track(track_name, artist_name)
        await message.delete()

    @loader.command()
    async def fmpollcmd(self, message):
        """on/off — включить или выключить автоопрос Last.fm. Без аргумента — показать статус"""
        args = utils.get_args_raw(message).strip().lower()

        if args == "on":
            if self._task and not self._task.done():
                await utils.answer(message, "<b>[Music]</b> Автоопрос уже включен")
                return
            self.config["auto_poll"] = True
            self._task = asyncio.ensure_future(self._poll_loop())
            await utils.answer(message, "<b>[Music]</b> Автоопрос Last.fm включен")
        elif args == "off":
            self.config["auto_poll"] = False
            if self._task:
                self._task.cancel()
                self._task = None
            await utils.answer(message, "<b>[Music]</b> Автоопрос Last.fm выключен")
        else:
            status = "включен" if self._task and not self._task.done() else "выключен"
            await utils.answer(
                message,
                f"<b>[Music]</b> Автоопрос сейчас: {status}\nИспользуй .fmpoll on / .fmpoll off"
            )

    @loader.command()
    async def fmstatuscmd(self, message):
        """показать текущее состояние модуля"""
        status = "включен" if self._task and not self._task.done() else "выключен"
        mode = "Ручной" if self._manual_override else "Автоматический"
        await utils.answer(
            message,
            f"<b>[Music]</b>\n"
            f"Last.fm юзер: <code>{self.config['lastfm_user'] or '—'}</code>\n"
            f"Автоопрос: {status}, интервал {self.config['poll_interval']}с\n"
            f"Режим работы: <code>{mode}</code>\n"
            f"Текущее состояние: <code>{self._last_state or '—'}</code>"
        )
