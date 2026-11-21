import gzip
import json


def moving_tgs():
    document = {
        "v": "5.7.4",
        "fr": 10,
        "ip": 0,
        "op": 10,
        "w": 64,
        "h": 64,
        "nm": "Moving square",
        "ddd": 0,
        "assets": [],
        "layers": [
            {
                "ddd": 0,
                "ind": 1,
                "ty": 1,
                "sr": 1,
                "sw": 16,
                "sh": 16,
                "sc": "#ff0000",
                "ip": 0,
                "op": 10,
                "st": 0,
                "bm": 0,
                "ks": {
                    "o": {"a": 0, "k": 100},
                    "r": {"a": 0, "k": 0},
                    "a": {"a": 0, "k": [0, 0, 0]},
                    "s": {"a": 0, "k": [100, 100, 100]},
                    "p": {
                        "a": 1,
                        "k": [
                            {
                                "t": 0,
                                "s": [4, 24, 0],
                                "e": [44, 24, 0],
                                "i": {"x": 1, "y": 1},
                                "o": {"x": 0, "y": 0},
                            },
                            {"t": 10, "s": [44, 24, 0]},
                        ],
                    },
                },
            }
        ],
    }
    return gzip.compress(json.dumps(document).encode())
