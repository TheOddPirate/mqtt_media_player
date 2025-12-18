import json
import base64
import hashlib
import logging
from homeassistant.util.dt import utcnow
from homeassistant.components import media_source
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    async_process_play_media_url
)
from homeassistant.components.mqtt import (
    async_subscribe,
    async_publish,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Setup entry point."""
    player = MQTTMediaPlayer(hass, config_entry)
    async_add_entities([player])

    # Subscribe to the config topic dynamically
    if "discovery_topic" in config_entry.data:
        CONFIG_TOPIC = config_entry.data["discovery_topic"]
    else:
        CONFIG_TOPIC = "homeassistant/media_player/#"

    await async_subscribe(hass, CONFIG_TOPIC, player.handle_config)


class MQTTMediaPlayer(MediaPlayerEntity):
    """Representation of a MQTT Media Player."""

    def __init__(self, hass, config_entry):
        self._hass = hass
        self._config_entry = config_entry
        self._name = None
        self._state = None
        self._volume = 0.0
        self._media_title = None
        self._media_artist = None
        self._media_album = None
        self._album_art = None
        self._duration = None  # float seconds
        self._position = None  # float seconds
        self._available = None
        self._media_type = "music"
        self._subscribed = []
        self._attr_media_position_updated_at = None

    async def handle_config(self, message):
        """Handle incoming configuration from MQTT."""
        if not message.payload or message.payload.strip() == "":
            _LOGGER.info("Received empty config payload - device removed")
            return

        try:
            config = json.loads(message.payload)
        except json.JSONDecodeError as e:
            _LOGGER.error(f"Failed to parse config JSON: {e}")
            return

        topic_device_id = message.topic.split('/')[-2]
        if topic_device_id != self._config_entry.title:
            _LOGGER.debug(f"Ignoring config for different device: {topic_device_id}")
            return

        _LOGGER.info(f"Received configuration: {config}")
        self._name = config.get("name")

        self._availability_topics = {
            "availability_topic": config.get("availability", {}).get("topic"),
            "available": config.get("availability", {}).get("payload_available", "online"),
            "not_available": config.get("availability", {}).get("payload_not_available", "offline"),
        }

        self._state_topics = {
            "state_topic": config.get("state_state_topic"),
            "title_topic": config.get("state_title_topic"),
            "artist_topic": config.get("state_artist_topic"),
            "album_topic": config.get("state_album_topic"),
            "duration_topic": config.get("state_duration_topic"),
            "position_topic": config.get("state_position_topic"),
            "volume_topic": config.get("state_volume_topic"),
            "albumart_topic": config.get("state_albumart_topic"),
            "mediatype_topic": config.get("state_mediatype_topic"),
        }

        self._cmd_topics = {
            "volumeset_topic": config.get("command_volume_topic"),
            "play_topic": config.get("command_play_topic"),
            "play_payload": config.get("command_play_payload", "Play"),
            "pause_topic": config.get("command_pause_topic"),
            "pause_payload": config.get("command_pause_payload", "Pause"),
            "next_topic": config.get("command_next_topic"),
            "next_payload": config.get("command_next_payload", "Next"),
            "previous_topic": config.get("command_previous_topic"),
            "previous_payload": config.get("command_previous_payload", "Previous"),
            "playmedia_topic": config.get("command_playmedia_topic"),
            "media_seek": config.get("command_seek_position_topic"),
        }

        for subscription in self._subscribed:
            subscription()
        self._subscribed = []

        # Subscribe to state topics
        for key, handler in [
            ("state_topic", self.handle_state),
            ("title_topic", self.handle_title),
            ("artist_topic", self.handle_artist),
            ("album_topic", self.handle_album),
            ("duration_topic", self.handle_duration),
            ("position_topic", self.handle_position),
            ("volume_topic", self.handle_volume),
            ("albumart_topic", self.handle_albumart),
            ("mediatype_topic", self.handle_mediatype),
            ("availability_topic", self.handle_availability),
        ]:
            topic = self._state_topics.get(key) or self._availability_topics.get(key)
            if topic:
                self._subscribed.append(await async_subscribe(self._hass, topic, handler))

    @property
    def supported_features(self):
        return (
            MediaPlayerEntityFeature.PLAY |
            MediaPlayerEntityFeature.PAUSE |
            MediaPlayerEntityFeature.STOP |
            MediaPlayerEntityFeature.VOLUME_SET |
            MediaPlayerEntityFeature.VOLUME_STEP |
            MediaPlayerEntityFeature.NEXT_TRACK |
            MediaPlayerEntityFeature.PREVIOUS_TRACK |
            MediaPlayerEntityFeature.PLAY_MEDIA |
            MediaPlayerEntityFeature.BROWSE_MEDIA |
            MediaPlayerEntityFeature.SEEK
        )
    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._config_entry.title

    @property
    def state(self):
        return "unavailable" if self._available is False else self._state

    @property
    def volume_level(self):
        return self._volume

    @property
    def media_title(self):
        return self._media_title

    @property
    def media_artist(self):
        return self._media_artist

    @property
    def media_album_name(self):
        return self._media_album

    @property
    def media_content_type(self):
        return self._media_type

    @property
    def media_position(self):
        return self._position

    @property
    def media_duration(self):
        return self._duration

    @property
    def media_image_hash(self):
        if self._album_art:
            return hashlib.md5(self._album_art).hexdigest()[:5]
        return None

    async def async_get_media_image(self):
        if self._album_art:
            return (self._album_art, "image/jpeg")
        return None, None

    async def handle_availability(self, message):
        self._available = message.payload == self._availability_topics["available"]
        self.async_write_ha_state()

    async def handle_state(self, message):
        self._state = message.payload
        self.async_write_ha_state()

    async def handle_title(self, message):
        self._media_title = message.payload
        self.async_write_ha_state()

    async def handle_artist(self, message):
        self._media_artist = message.payload
        self.async_write_ha_state()

    async def handle_album(self, message):
        self._media_album = message.payload
        self.async_write_ha_state()

    async def handle_duration(self, message):
        try:
            self._duration = float(message.payload)
        except Exception as e:
            _LOGGER.warning(f"Invalid duration payload: {message.payload} ({e})")
            self._duration = None
        self.async_write_ha_state()

    async def handle_position(self, message):
        try:
            self._position = float(message.payload)
            self._attr_media_position_updated_at = utcnow()
        except Exception as e:
            _LOGGER.warning(f"Invalid position payload: {message.payload} ({e})")
            self._position = None
        self.async_write_ha_state()

    async def handle_volume(self, message):
        try:
            self._volume = float(message.payload)
        except:
            self._volume = 0.0
        self.async_write_ha_state()

    async def handle_albumart(self, message):
        try:
            self._album_art = base64.b64decode(message.payload.replace("\n",""))
        except Exception:
            self._album_art = None
        self.async_write_ha_state()

    async def handle_mediatype(self, message):
        self._media_type = message.payload
        self.async_write_ha_state()

    async def async_media_play(self):
        await async_publish(self._hass, self._cmd_topics["play_topic"], self._cmd_topics["play_payload"])

    async def async_media_pause(self):
        await async_publish(self._hass, self._cmd_topics["pause_topic"], self._cmd_topics["pause_payload"])

    async def async_media_next_track(self):
        await async_publish(self._hass, self._cmd_topics["next_topic"], self._cmd_topics["next_payload"])

    async def async_media_previous_track(self):
        await async_publish(self._hass, self._cmd_topics["previous_topic"], self._cmd_topics["previous_payload"])

    async def async_set_volume_level(self, volume):
        self._volume = round(float(volume), 2)
        await async_publish(self._hass, self._cmd_topics["volumeset_topic"], self._volume)

    async def async_media_seek(self, position):
        await async_publish(self._hass, self._cmd_topics["media_seek"], position)


    async def async_play_media(self, media_type, media_id, **kwargs):
        if media_source.is_media_source_id(media_id):
            sourced_media = await media_source.async_resolve_media(self._hass, media_id)
            media_type = sourced_media.mime_type
            media_id = async_process_play_media_url(self._hass, sourced_media.url)
        media = {"media_type": media_type, "media_id": media_id}
        await async_publish(self._hass, self._cmd_topics["playmedia_topic"], json.dumps(media))

    async def async_browse_media(self, media_content_type, media_content_id):
        return await media_source.async_browse_media(
            self._hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type,
        )
