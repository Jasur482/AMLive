import logging
import asyncio
from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class MusicBroadcastMod(loader.Module):
    """Real-time track broadcasting from Last.fm (Now Playing style with covers)"""

    strings = {"name": "MusicBroadcast"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            "channel_id", 0, "ID канала",
            "message_id", 0, "ID сообщения для редактирования/удаления",
            "default_title", "Not Playing", "Название канала, когда ничего не играет",
            "lastfm_user", "", "Юзернейм на Last.fm",
            "lastfm_api_key", "", "API key с last.fm/api/account/create",
            "poll_interval", 10, "Интервал опроса Last.fm, сек (не меньше 10)",
            "auto_poll", True, "Включить автоопрос Last.fm при старте",
        )
        self._last_state = None
        self._task = None
        self._session = None
        self._manual_override = False
        # Красивая дефолтная обложка Apple Music, если у трека на Last.fm нет картинки
        self._default_cover = "https://raw.githubusercontent.com/idwtext/resources/main/am_placeholder.png"

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

    async def _poll_loop(self):
        await asyncio.sleep(3)
        while True:
            try:
                await self._check_lastfm()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[Music] Last.fm poll error: {e}")
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
            logger.error(f"[Music] Last.fm unavailable: {e}")
            return

        if not isinstance(data, dict) or "recenttracks" not in data:
            return

        tracks = data.get("recenttracks", {}).get("track", [])
        if not tracks:
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
        
        # Улучшенный и более агрессивный поиск обложки альбома
        cover_url = ""
        images = track.get("image", [])
        if isinstance(images, list):
            # Ищем сначала самую большую ('extralarge' или 'large')
            for size in ["extralarge", "large", "medium", "small"]:
                for img in images:
                    if img.get("size") == size and img.get("#text"):
                        cover_url = img.get("#text").strip()
                        break
                if cover_url:
                    break
            
            # Фолбэк: если size не совпал, но хоть какая-то ссылка внутри есть
            if not cover_url:
                for img in reversed(images):
                    if img.get("#text"):
                        cover_url = img.get("#text").strip()
                        break

        if not track_name:
            if not self._manual_override:
                await self._apply_stopped()
            return

        incoming_state = f"{track_name}_{artist_name}"
        
        if self._manual_override:
            if self._last_state != incoming_state:
                self._manual_override = False
                await self._apply_track(track_name, artist_name, cover_url)
        else:
            await self._apply_track(track_name, artist_name, cover_url)

    async def _apply_stopped(self):
        current_state = "stopped"
        if self._last_state == current_state:
            return
        self._last_state = current_state
        await self._update_channel(self.config["default_title"], "⎯", cover_url=None)

    async def _apply_track(self, track_name, artist_name, cover_url=None):
        current_state = f"{track_name}_{artist_name}"
        if self._last_state == current_state:
            return
        self._last_state = current_state
        # Если обложки нет вообще — подставляем дефолтный красный значок Apple Music
        final_cover = cover_url if cover_url else self._default_cover
        await self._update_channel("Now Playing", f"🟥 {track_name} — {artist_name}", final_cover)

    async def _update_channel(self, title, text, cover_url=None):
        channel_id = int(self.config["channel_id"])
        message_id = int(self.config["message_id"])

        if not channel_id:
            return

        try:
            from hikkatl.tl import functions

            # 1. Меняем название канала
            await self._client(functions.channels.EditTitleRequest(
                channel=channel_id,
                title=title
            ))

            # 2. Обновляем пост с обложкой альбома
            if cover_url:
                # Удаляем старый пост (если он был задан), чтобы отправить медиафайл заново
                if message_id:
                    try:
                        await self._client.delete_messages(channel_id, message_id)
                    except Exception:
                        pass
                
                # Отправляем обложку как фото, а текст пишем в описание (Caption)
                new_msg = await self._client.send_file(channel_id, cover_url, caption=text)
                self.config["message_id"] = new_msg.id
            else:
                # Ветка для статуса "Stopped" (просто текст ⎯ без картинок)
                if message_id:
                    try:
                        await self._client.delete_messages(channel_id, message_id)
                    except Exception:
                        pass
                new_msg = await self._client.send_message(channel_id, text)
                self.config["message_id"] = new_msg.id

            # Очистка сервисных сообщений переименования из ленты канала
            await asyncio.sleep(0.8)
            messages = await self._client.get_messages(channel_id, limit=3)
            for msg in messages:
                if msg.action:
                    try:
                        await msg.delete()
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"[Music] Channel update error: {e}")

    # ---------- команды ----------

    @loader.command()
    async def settrackfmcmd(self, message):
        """<text> — ручной ввод трека формата Имя — Артист"""
        args = utils.get_args_raw(message)
        args_clean = args.strip() if args else ""

        if (args_clean.lower() in ["stop", "none", "stop", "off"] or
                "itunes media" in args_clean.lower() or not args_clean):
            self._manual_override = False
            await self._apply_stopped()
            await message.delete()
            return

        separator = "—" if "—" in args_clean else ("-" if "-" in args_clean else None)

        if separator:
            try:
                track_name, artist_name = [x.strip() for x in args_clean.split(separator, 1)]
            except Exception:
                track_name = args_clean
                artist_name = "Apple Music"
        else:
            track_name = args_clean
            artist_name = "Apple Music"

        self._manual_override = True
        await self._apply_track(track_name, artist_name, cover_url=None)
        await message.delete()
