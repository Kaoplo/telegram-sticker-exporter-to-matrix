# Animated sticker support

| Telegram input | Matrix output | Renderer |
| --- | --- | --- |
| Static WEBP/PNG | PNG, `image/png` | Pillow |
| Animated TGS | Animated WebP, `image/webp` | rlottie and Pillow |
| Video WEBM (VP9) | Animated WebP, `image/webp` | FFmpeg/libvpx and libwebp |

Animations loop continuously. TGS conversion keeps the source frame rate and
RGBA frames. WebM is decoded with libvpx to preserve VP9 transparency, fitted
within 512×512, and encoded at 30 fps. Animated WebP preserves full-color
transparency without GIF's palette limits. Client animation playback depends
on the Matrix client and its animation settings.

The limits match Telegram stickers: TGS input up to 512×512, 60 fps, 180 frames,
and approximately three seconds. Decompressed TGS JSON is capped at 2 MiB;
external image assets are rejected. Video output is capped at 3.1 seconds and
94 frames. Input and converted files are limited to 10 MiB.

Native renderers run in a subprocess with a 60-second deadline, keeping native
crashes or stuck conversions out of Telegram polling. FFmpeg has an inner
45-second deadline. One conversion runs at a time, and temporary files are
removed when conversion finishes. No media cache or backup volume is needed.

## Deployment

Rebuild the Docker image after upgrading: it now includes FFmpeg and rlottie.
For native installations, install FFmpeg separately and run `uv sync --locked`.
Both `ffmpeg` and its `libvpx-vp9` decoder / `libwebp_anim` encoder must be
available. The Debian FFmpeg package included in the image provides these.

`/view` returns the actual uploaded WebP as an image document. Telegram may
display it as a file instead of playing it inline; opening the document or
using the sticker in Sable shows the converted media.

## Verification

The integration suite creates a moving vector square as gzipped TGS, then
checks the output frames, duration, alpha channel, and motion. It also creates
a short VP9 WebM with FFmpeg and checks the converted animation. Invalid TGS,
excessive dimensions, and decompression limits are tested. Video tests require
FFmpeg; CI installs it so those tests are not skipped.

References: [Telegram formats](https://core.telegram.org/api/stickers),
[rlottie Python API](https://rlottie-python.readthedocs.io/en/latest/rlottie_python.html).
