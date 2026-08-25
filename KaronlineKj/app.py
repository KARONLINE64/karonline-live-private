import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

requested_file = None
if "--file" in sys.argv:
	file_index = sys.argv.index("--file")
	if file_index + 1 >= len(sys.argv):
		raise SystemExit("--file requires an MP4 path")
	requested_file = Path(sys.argv[file_index + 1]).expanduser().resolve()

app = QApplication(sys.argv)
app.setApplicationName('Karonline KJ')
win = MainWindow()
win.show()
if requested_file:
	QTimer.singleShot(0, lambda: win.play_local_file(requested_file))
sys.exit(app.exec())
