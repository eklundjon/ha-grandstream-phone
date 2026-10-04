"""Constants for the Grandstream Phone integration."""

from datetime import timedelta

DOMAIN = "grandstream_phone"
MANUFACTURER = "Grandstream"

CONF_MODEL = "model"

# The handset's least-privileged web account. Its name is fixed by firmware.
DEFAULT_USERNAME = "user"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

# Models tested end to end, so the config flow doesn't ask for confirmation.
# Add a model here only with a contributor's report (see README).
VERIFIED_MODELS = frozenset({"WP826"})

# Setting keys, as the handset's config API names them: P-numbers without the
# "P", runtime values with a leading colon. Verified on WP826 fw 1.0.3.35.
KEY_LCD_BRIGHTNESS = "334"  # 10-100, steps of 10
KEY_BACKLIGHT_TIMEOUT = "1413"  # seconds; 0 = never
KEY_KEYPAD_BACKLIGHT = "22234"  # 0 off, 1 on, 2 auto
KEY_RING_VOLUME = "8352"  # 0-10
KEY_DND = ":dnd"  # 0/1, runtime state
KEY_UNREAD_VOICEMAIL = ":unread_vm_1"  # account 1
KEY_ACCOUNT_REGISTERED = "AccountRegistered1"  # 0/1, account 1

POLLED_KEYS = (
    KEY_LCD_BRIGHTNESS,
    KEY_BACKLIGHT_TIMEOUT,
    KEY_KEYPAD_BACKLIGHT,
    KEY_RING_VOLUME,
    KEY_DND,
    KEY_UNREAD_VOICEMAIL,
    KEY_ACCOUNT_REGISTERED,
)

# Where to report a model that isn't in VERIFIED_MODELS.
DEVICE_SUPPORT_URL = (
    "https://github.com/eklundjon/ha-grandstream-phone/issues/new?template=device_support.yml"
)
