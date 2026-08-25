import json
from PySide6.QtCore import QObject, Signal, QSettings


class FavoritesManager(QObject):
    changed = Signal()

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings or QSettings("Karonline", "KaronlineKJ")
        self.solo = []
        self.group = []
        self.load()

    def load(self):
        self.solo = self._load_list("favorites/solo")
        self.group = self._load_list("favorites/group")

    def _load_list(self, key):
        raw = self.settings.value(key, "[]")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            return data if isinstance(data, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    def _save(self):
        self.settings.setValue("favorites/solo", json.dumps(self.solo, ensure_ascii=False))
        self.settings.setValue("favorites/group", json.dumps(self.group, ensure_ascii=False))
        self.settings.sync()
        self.changed.emit()

    def add_solo(self, title, artist=""):
        item = {"title": str(title), "artist": str(artist)}
        if not any(x.get("title", "").casefold() == item["title"].casefold() for x in self.solo):
            self.solo.append(item)
            self._save()

    def add_group(self, singer, title, artist=""):
        item = {"singer": str(singer), "title": str(title), "artist": str(artist)}
        if not any(
            x.get("singer", "").casefold() == item["singer"].casefold()
            and x.get("title", "").casefold() == item["title"].casefold()
            for x in self.group
        ):
            self.group.append(item)
            self._save()

    def remove_solo(self, index):
        if 0 <= index < len(self.solo):
            self.solo.pop(index)
            self._save()

    def remove_group(self, index):
        if 0 <= index < len(self.group):
            self.group.pop(index)
            self._save()

    def clear(self):
        self.solo.clear()
        self.group.clear()
        self._save()
