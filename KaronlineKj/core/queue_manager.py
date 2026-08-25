from PySide6.QtCore import QObject, Signal
from .models import Song


class QueueManager(QObject):
    changed = Signal()
    current_changed = Signal(object)
    next_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.items = []
        self.current = None

    @property
    def queue(self):
        """Compatibility alias used by the UI."""
        return self.items

    @property
    def next_song(self):
        return self.items[0] if self.items else None

    def set_demo(self, songs):
        self.items = list(songs)
        self.advance()

    def add(self, song, index=None):
        if index is None or index < 0 or index > len(self.items):
            self.items.append(song)
        else:
            self.items.insert(index, song)
        self.emit_all()

    def advance(self):
        self.current = self.items.pop(0) if self.items else None
        self.emit_all()
        return self.current

    def play_now(self, index):
        """Move a queued item to current and return it."""
        if not (0 <= index < len(self.items)):
            return None
        self.current = self.items.pop(index)
        self.emit_all()
        return self.current

    def move(self, index, delta):
        j = index + delta
        if 0 <= index < len(self.items) and 0 <= j < len(self.items):
            self.items[index], self.items[j] = self.items[j], self.items[index]
            self.emit_all()

    def remove(self, index):
        if 0 <= index < len(self.items):
            self.items.pop(index)
            self.emit_all()

    def emit_all(self):
        self.changed.emit()
        self.current_changed.emit(self.current)
        self.next_changed.emit(self.next_song)
