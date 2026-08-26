from dataclasses import dataclass

@dataclass
class Song:
    singer: str
    artist: str
    title: str
    key: int = 0
