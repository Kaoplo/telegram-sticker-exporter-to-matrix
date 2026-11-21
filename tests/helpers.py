from matrix_stickers.config import Settings


def settings(room_id: str = "") -> Settings:
    return Settings(
        "test-token",
        1,
        "https://matrix.example.org",
        "@test:example.org",
        "matrix-test-token",
        room_id,
        "room" if room_id else "personal",
    )
