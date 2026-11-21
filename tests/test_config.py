import unittest

from matrix_stickers.config import Settings


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "TELEGRAM_BOT_TOKEN": "secret-telegram",
            "TELEGRAM_USER_ID": "1",
            "MATRIX_HOMESERVER": "https://matrix.example.org/",
            "MATRIX_USER_ID": "@alice:example.org",
            "MATRIX_ACCESS_TOKEN": "secret-matrix",
            "MATRIX_PACK_ROOM_ID": "!roomhash",
        }

    def test_settings_strip_trailing_slash_and_hide_secrets(self):
        config = Settings.from_env(self.env)
        self.assertEqual(config.homeserver, "https://matrix.example.org")
        self.assertNotIn("secret-telegram", repr(config))
        self.assertNotIn("secret-matrix", repr(config))

    def test_missing_configuration(self):
        with self.assertRaisesRegex(ValueError, "Missing required environment variable"):
            Settings.from_env({})

    def test_invalid_user_ids(self):
        for value in ("-1", "0", "abc"):
            self.env["TELEGRAM_USER_ID"] = value
            with self.assertRaises(ValueError):
                Settings.from_env(self.env)

    def test_https_required_unless_explicitly_allowed(self):
        self.env["MATRIX_HOMESERVER"] = "http://localhost:8008"
        with self.assertRaises(ValueError):
            Settings.from_env(self.env)
        self.env["ALLOW_HTTP"] = "1"
        self.assertEqual(Settings.from_env(self.env).homeserver, "http://localhost:8008")

    def test_credentials_in_homeserver_url_rejected(self):
        self.env["MATRIX_HOMESERVER"] = "https://user:password@example.org"
        with self.assertRaises(ValueError):
            Settings.from_env(self.env)

    def test_room_mode_is_default_and_missing_room_is_an_error(self):
        config = Settings.from_env(self.env)
        self.assertEqual(config.pack_mode, "room")
        self.assertEqual(config.room_id, "!roomhash")
        del self.env["MATRIX_PACK_ROOM_ID"]
        with self.assertRaisesRegex(ValueError, "MATRIX_PACK_ROOM_ID"):
            Settings.from_env(self.env)

    def test_personal_mode_is_an_explicit_opt_in(self):
        self.env["MATRIX_PACK_MODE"] = "personal"
        config = Settings.from_env(self.env)
        self.assertEqual(config.pack_mode, "personal")
        self.assertEqual(config.room_id, "")

    def test_invalid_pack_mode_or_room_id(self):
        self.env["MATRIX_PACK_MODE"] = "unknown"
        with self.assertRaises(ValueError):
            Settings.from_env(self.env)
        self.env["MATRIX_PACK_MODE"] = "room"
        self.env["MATRIX_PACK_ROOM_ID"] = "#alias:server"
        with self.assertRaises(ValueError):
            Settings.from_env(self.env)
