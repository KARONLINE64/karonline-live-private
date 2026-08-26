from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer


class GStreamerError(RuntimeError):
    pass


class _GError(ctypes.Structure):
    _fields_ = [
        ("domain", ctypes.c_uint32),
        ("code", ctypes.c_int),
        ("message", ctypes.c_char_p),
    ]


class GStreamerPlayer:
    """
    Karonline GStreamer backend.

    One decode pipeline:
      uridecodebin
        video -> tee -> main d3d11videosink
                       public d3d11videosink
        audio -> volume -> WASAPI sink

    The video is decoded once; the two windows receive the same decoded stream.
    """

    GST_STATE_NULL = 1
    GST_STATE_PAUSED = 3
    GST_STATE_PLAYING = 4

    GST_FORMAT_TIME = 3
    GST_SEEK_FLAG_FLUSH = 1
    GST_SEEK_FLAG_KEY_UNIT = 2

    GST_MESSAGE_EOS = 1 << 4
    GST_MESSAGE_ERROR = 1 << 3

    def __init__(
        self,
        video_widget,
        public_widget=None,
        on_eos: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.video_widget = video_widget
        self.public_widget = public_widget
        self.on_eos = on_eos
        self.on_error = on_error
        self.on_audio_eos = None
        self.on_audio_error = None

        self.pipeline = None
        self.bus = None
        self.audio_pipeline = None
        self.audio_bus = None
        self.audio_next_pipeline = None
        self.audio_next_bus = None
        self._gst = None
        self._gst_video = None
        self._gst_audio = None
        self._dll_handles = []
        self._pending_pitch_semitones = 0

        self._load_runtime()
        self._init_gstreamer()

        self.video_widget.setAttribute(Qt.WA_NativeWindow, True)
        self.video_widget.winId()

    @staticmethod
    def _find_bundled_gstreamer_root() -> Path:
        import sys

        # bundle allege livre a cote de l'exe (voir tools/build_gstreamer_bundle.ps1)
        app_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
        bundled = app_dir / "gstreamer_runtime"
        if (bundled / "bin").is_dir() and (bundled / "lib" / "gstreamer-1.0").is_dir():
            return bundled
        return Path(r"C:\Program Files\gstreamer\1.0\msvc_x86_64")

    def _load_runtime(self):
        root = Path(os.environ.get("KARONLINE_GSTREAMER_ROOT", "")) if os.environ.get(
            "KARONLINE_GSTREAMER_ROOT"
        ) else self._find_bundled_gstreamer_root()

        bin_dir = root / "bin"
        lib_dir = root / "lib"
        plugin_dir = lib_dir / "gstreamer-1.0"
        scanner = root / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner.exe"

        # ============================================================
        # DIAGNOSTIC ENVIRONNEMENT GStreamer — AUCUNE MODIFICATION
        # ============================================================
        try:
            import sys

            if getattr(sys, "frozen", False):
                env_diag_path = (
                    Path(sys.executable).resolve().parent
                    / "BREAK_DIAG.txt"
                )
            else:
                env_diag_path = Path.cwd() / "BREAK_DIAG.txt"

            env_lines = [
                "",
                "===== GSTREAMER ENVIRONMENT DIAGNOSTIC =====",
                f"frozen                    = {getattr(sys, 'frozen', False)!r}",
                f"sys.executable            = {sys.executable!r}",
                f"cwd                       = {Path.cwd()!r}",
                f"GStreamer root            = {root!r}",
                f"GStreamer root exists     = {root.is_dir()!r}",
                f"bin_dir                   = {bin_dir!r}",
                f"bin_dir exists            = {bin_dir.is_dir()!r}",
                f"lib_dir                   = {lib_dir!r}",
                f"lib_dir exists            = {lib_dir.is_dir()!r}",
                f"plugin_dir                = {plugin_dir!r}",
                f"plugin_dir exists         = {plugin_dir.is_dir()!r}",
                f"scanner                   = {scanner!r}",
                f"scanner exists            = {scanner.is_file()!r}",
                f"PATH                      = {os.environ.get('PATH', '')!r}",
                f"GST_PLUGIN_PATH          = {os.environ.get('GST_PLUGIN_PATH', '')!r}",
                f"GST_PLUGIN_SYSTEM_PATH   = {os.environ.get('GST_PLUGIN_SYSTEM_PATH', '')!r}",
                f"GST_PLUGIN_PATH_1_0      = {os.environ.get('GST_PLUGIN_PATH_1_0', '')!r}",
                f"GST_PLUGIN_SYSTEM_PATH_1_0 = {os.environ.get('GST_PLUGIN_SYSTEM_PATH_1_0', '')!r}",
            ]

            with env_diag_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(env_lines) + "\n")

        except Exception as exc:
            try:
                with env_diag_path.open("a", encoding="utf-8") as f:
                    f.write(
                        f"GSTREAMER ENV DIAGNOSTIC ERROR = {exc!r}\n"
                    )
            except Exception:
                pass

        if not bin_dir.is_dir() or not plugin_dir.is_dir():
            raise GStreamerError(
                f"Installation GStreamer incomplÃ¨te : {root}"
            )

        self._dll_handles.append(os.add_dll_directory(str(bin_dir)))
        if lib_dir.is_dir():
            self._dll_handles.append(os.add_dll_directory(str(lib_dir)))

        path_parts = [str(bin_dir), str(lib_dir)]
        old_path = os.environ.get("PATH", "")
        if old_path:
            path_parts.append(old_path)
        os.environ["PATH"] = os.pathsep.join(path_parts)

        os.environ["GST_PLUGIN_PATH_1_0"] = str(plugin_dir)
        os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] = str(plugin_dir)
        os.environ["GST_PLUGIN_PATH"] = str(plugin_dir)
        os.environ["GST_PLUGIN_SYSTEM_PATH"] = str(plugin_dir)
        if scanner.exists():
            os.environ["GST_PLUGIN_SCANNER"] = str(scanner)

        try:
            self._gst = ctypes.CDLL(str(bin_dir / "gstreamer-1.0-0.dll"))
            self._gst_video = ctypes.CDLL(str(bin_dir / "gstvideo-1.0-0.dll"))
            self._gst_audio = ctypes.CDLL(str(bin_dir / "gstaudio-1.0-0.dll"))
        except OSError as exc:
            raise GStreamerError(f"Impossible de charger GStreamer : {exc}") from exc

    def _init_gstreamer(self):
        gst = self._gst

        gst.gst_init.argtypes = [
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        gst.gst_init.restype = None

        argc = ctypes.c_int(0)
        argv = ctypes.c_void_p()
        gst.gst_init(ctypes.byref(argc), ctypes.byref(argv))

        gst.gst_parse_launch.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        gst.gst_parse_launch.restype = ctypes.c_void_p

        gst.gst_element_set_state.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gst.gst_element_set_state.restype = ctypes.c_int

        gst.gst_element_get_bus.argtypes = [ctypes.c_void_p]
        gst.gst_element_get_bus.restype = ctypes.c_void_p

        gst.gst_element_query_position.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int64)
        ]
        gst.gst_element_query_position.restype = ctypes.c_int

        gst.gst_element_query_duration.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int64)
        ]
        gst.gst_element_query_duration.restype = ctypes.c_int

        gst.gst_element_seek_simple.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int64
        ]
        gst.gst_element_seek_simple.restype = ctypes.c_int

        gst.gst_bus_timed_pop_filtered.argtypes = [
            ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32
        ]
        gst.gst_bus_timed_pop_filtered.restype = ctypes.c_void_p

        gst.gst_message_parse_error.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_char_p)
        ]
        gst.gst_message_parse_error.restype = None

        gst.gst_message_unref.argtypes = [ctypes.c_void_p]
        gst.gst_message_unref.restype = None

        gst.gst_object_unref.argtypes = [ctypes.c_void_p]
        gst.gst_object_unref.restype = None

        # GStreamer helper for setting object properties from text.
        gst.gst_util_set_object_arg.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        gst.gst_util_set_object_arg.restype = None

        # Generic GObject property API. This lets us control volume without
        # relying on Qt Multimedia.
        # GValue and g_object_set_property belong to GObject, not GLib.
        gobject = ctypes.CDLL("gobject-2.0-0.dll")
        gobject.g_value_init.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        gobject.g_value_init.restype = ctypes.c_void_p
        gobject.g_value_set_double.argtypes = [ctypes.c_void_p, ctypes.c_double]
        gobject.g_value_set_double.restype = None
        gobject.g_value_set_float.argtypes = [ctypes.c_void_p, ctypes.c_float]
        gobject.g_value_set_float.restype = None
        gobject.g_value_unset.argtypes = [ctypes.c_void_p]
        gobject.g_value_unset.restype = None
        gobject.g_object_set_property.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p
        ]
        gobject.g_object_set_property.restype = None
        self._gobject = gobject

        # gst_bin_get_by_name is exported by GStreamer.
        gst.gst_bin_get_by_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        gst.gst_bin_get_by_name.restype = ctypes.c_void_p

        self._gst_video.gst_video_overlay_set_window_handle.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t
        ]
        self._gst_video.gst_video_overlay_set_window_handle.restype = None

    def _set_double_property(self, obj, name: str, value: float):
        # GValue layout for a double on Win64 GLib:
        # guint64 g_type + union holding gdouble. We initialize via GLib API
        # to avoid guessing the ABI in Python.
        gobject = self._gobject
        gvalue_init = gobject.g_value_init
        gvalue_set_double = gobject.g_value_set_double

        # G_TYPE_DOUBLE = G_TYPE_FUNDAMENTAL_SHIFT << 2 + DOUBLE fundamental.
        # GLib fundamental DOUBLE is 15; GType = (15 << 2) = 60.
        G_TYPE_DOUBLE = 60

        class GValue(ctypes.Structure):
            _fields_ = [
                ("g_type", ctypes.c_size_t),
                ("data", ctypes.c_uint64 * 2),
            ]

        gv = GValue()
        gvalue_init(ctypes.byref(gv), G_TYPE_DOUBLE)
        gvalue_set_double(ctypes.byref(gv), ctypes.c_double(value))

        gobject.g_object_set_property(
            obj, name.encode("utf-8"), ctypes.byref(gv)
        )

        gobject.g_value_unset(ctypes.byref(gv))

    def _set_float_property(self, obj, name: str, value: float):
        # Use GStreamer's own property conversion instead of constructing
        # a GValue manually through ctypes.
        self._gst.gst_util_set_object_arg(
            obj,
            name.encode("utf-8"),
            format(value, ".10g").encode("ascii"),
        )

    @staticmethod
    def _uri(filename: str) -> str:
        return Path(filename).resolve().as_uri()

    def _make_pipeline(self, filename: str):
        uri = self._uri(filename).replace("\\", "\\\\").replace('"', '\\"')

        # uridecodebin exposes dynamic audio/video pads. Video is tee'd after
        # decoding, so there is ONE H.264 decoder for both displays.
        description = (
            f'uridecodebin uri="{uri}" name=src '
            'src. ! queue name=video_decode_queue ! tee name=vtee '
            'vtee. ! queue name=main_video_queue ! videoconvert ! videoscale ! '
            'd3d11videosink name=mainvideo '
            'vtee. ! queue name=public_video_queue ! videoconvert ! videoscale ! '
            'd3d11videosink name=publicvideo '
            'src. ! queue name=audio_decode_queue max-size-buffers=0 max-size-bytes=0 max-size-time=2000000000 '
            '! audioconvert ! audioresample '
            '! audio/x-raw,format=F32LE,layout=interleaved '
            '! pitch name=pitcher pitch=1.0 tempo=1.0 rate=1.0 '
            '! queue name=audio_output_queue '
            '! volume name=mastervolume volume=1.0 '
            '! wasapisink name=audiosink sync=true'
        ).encode("utf-8")

        parse_error = ctypes.c_void_p()
        pipeline = self._gst.gst_parse_launch(
            description, ctypes.byref(parse_error)
        )
        if not pipeline:
            detail = "erreur inconnue"
            if parse_error.value:
                err = ctypes.cast(
                    parse_error, ctypes.POINTER(_GError)
                ).contents
                if err.message:
                    detail = err.message.decode("utf-8", "replace")
            raise GStreamerError(
                f"Impossible de crÃ©er le pipeline : {detail}"
            )
        return pipeline

    def _stop_video_pipeline(self):
        """Stop only the karaoke video pipeline; keep Break Music alive."""
        if self.pipeline:
            self._gst.gst_element_set_state(
                self.pipeline, self.GST_STATE_NULL
            )
            if self.bus:
                self._gst.gst_object_unref(self.bus)
            self._gst.gst_object_unref(self.pipeline)
        self.pipeline = None
        self.bus = None

    def load(self, filename: str):
        self._stop_video_pipeline()
        self.pipeline = self._make_pipeline(filename)
        self.bus = self._gst.gst_element_get_bus(self.pipeline)

        main = self._gst.gst_bin_get_by_name(
            self.pipeline, b"mainvideo"
        )
        public = self._gst.gst_bin_get_by_name(
            self.pipeline, b"publicvideo"
        )

        if not main:
            self.stop()
            raise GStreamerError("Sink vidÃ©o principal introuvable.")
        if not public:
            self.stop()
            raise GStreamerError("Sink vidÃ©o public introuvable.")

        self._gst_video.gst_video_overlay_set_window_handle(
            main, int(self.video_widget.winId())
        )

        if self.public_widget is not None:
            self.public_widget.setAttribute(Qt.WA_NativeWindow, True)
            self.public_widget.winId()
            self._gst_video.gst_video_overlay_set_window_handle(
                public, int(self.public_widget.winId())
            )
        # IMPORTANT: never attach the public sink to the main HWND.
        # Two d3d11videosinks must never subclass the same external window.
        # The public sink receives its own HWND only when the public window
        # is actually created.

        self._gst.gst_object_unref(main)
        self._gst.gst_object_unref(public)

        result = self._gst.gst_element_set_state(
            self.pipeline, self.GST_STATE_PAUSED
        )
        if result == 0:
            self.stop()
            raise GStreamerError("GStreamer n'a pas pu passer en PAUSED.")

    def set_public_video_enabled(self, enabled: bool):
        if not self.pipeline:
            return
        public = self._gst.gst_bin_get_by_name(
            self.pipeline, b"publicvideo"
        )
        if public:
            state = self.GST_STATE_PLAYING if enabled else self.GST_STATE_NULL
            self._gst.gst_element_set_state(public, state)
            self._gst.gst_object_unref(public)

    def set_public_widget(self, widget):
        if widget is None:
            return

        self.public_widget = widget
        widget.setAttribute(Qt.WA_NativeWindow, True)
        hwnd = int(widget.winId())

        if not self.pipeline:
            return

        public = self._gst.gst_bin_get_by_name(
            self.pipeline, b"publicvideo"
        )
        if public:
            # Reusing the same stable HWND prevents D3D11 from creating
            # an independent "Direct3D11 render" window.
            self._gst_video.gst_video_overlay_set_window_handle(
                public, hwnd
            )
            self._gst.gst_object_unref(public)


    def _make_audio_pipeline(self, filename: str):
        uri = self._uri(filename).replace("\\", "\\\\").replace('"', '\\"')
        description = (
            f'uridecodebin uri="{uri}" name=asrc '
            'asrc. ! queue ! audioconvert ! audioresample '
            '! equalizer-10bands name=break_equalizer '
            '! volume name=breakvolume volume=0.0 '
            '! autoaudiosink name=breaksink sync=true'
        ).encode("utf-8")

        parse_error = ctypes.c_void_p()
        pipeline = self._gst.gst_parse_launch(
            description, ctypes.byref(parse_error)
        )
        if not pipeline:
            detail = "erreur inconnue"
            if parse_error.value:
                err = ctypes.cast(
                    parse_error, ctypes.POINTER(_GError)
                ).contents
                if err.message:
                    detail = err.message.decode("utf-8", "replace")
            raise GStreamerError(
                f"Impossible de crÃ©er le pipeline audio : {detail}"
            )
        return pipeline

    def set_break_eq(self, enabled, bands):
        """Ã‰galiseur dÃ©diÃ© Ã  la MUSIQUE DU BREAK uniquement."""
        self._break_eq_enabled = bool(enabled)
        self._break_eq_bands = list(bands or [0, 0, 0, 0, 0])

        if not self.audio_pipeline:
            return

        eq = self._gst.gst_bin_get_by_name(
            self.audio_pipeline, b"break_equalizer"
        )
        if not eq:
            return

        # GstIirEqualizer10Bands exposes band0 ... band9.
        # The five UI controls are mapped to the five lowest/mid bands.
        # Karaoke audio is on a separate pipeline and is untouched.
        for index in range(10):
            value = 0.0
            if self._break_eq_enabled and index < 5:
                value = float(self._break_eq_bands[index])
            try:
                self._set_double_property(eq, f"band{index}", value)
            except Exception:
                pass

        self._gst.gst_object_unref(eq)


    def seek_audio(self, position_ms: int):
        if not self.audio_pipeline:
            return False
        flags = self.GST_SEEK_FLAG_FLUSH | self.GST_SEEK_FLAG_KEY_UNIT
        result = self._gst.gst_element_seek_simple(
            self.audio_pipeline,
            self.GST_FORMAT_TIME,
            flags,
            max(0, int(position_ms)) * 1_000_000,
        )
        return bool(result)

    def _write_break_audio_diag(self, lines):
        try:
            import sys
            from pathlib import Path

            if getattr(sys, "frozen", False):
                diag_path = Path(sys.executable).resolve().parent / "BREAK_DIAG.txt"
            else:
                diag_path = Path.cwd() / "BREAK_DIAG.txt"

            with diag_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(str(x) for x in lines) + "\n")
        except Exception:
            pass
    def load_audio(self, filename: str, volume: float = 0.0):
        """Load and start a separate audio-only Break Music pipeline."""

        diag = [
            "===== AUDIO PIPELINE DIAGNOSTIC =====",
            f"filename              = {filename!r}",
            f"volume_request        = {volume!r}",
            f"audio_pipeline_before = {self.audio_pipeline!r}",
            f"audio_bus_before      = {self.audio_bus!r}",
        ]

        try:
            self.stop_audio()

            self.audio_pipeline = self._make_audio_pipeline(filename)

            diag.append(
                f"audio_pipeline_created = {self.audio_pipeline!r}"
            )

            self.audio_bus = self._gst.gst_element_get_bus(
                self.audio_pipeline
            )

            # Diagnostic uniquement : inspection du sink audio réel.
            try:
                import sys
                from pathlib import Path

                if getattr(sys, "frozen", False):
                    sink_diag = (
                        Path(sys.executable).resolve().parent
                        / "BREAK_DIAG.txt"
                    )
                else:
                    sink_diag = Path.cwd() / "BREAK_DIAG.txt"

                sink_lines = [
                    "",
                    "===== BREAK SINK DIAGNOSTIC =====",
                    f"audio_pipeline = {self.audio_pipeline!r}",
                    f"audio_bus      = {self.audio_bus!r}",
                ]

                sink = self._gst.gst_bin_get_by_name(
                    self.audio_pipeline,
                    b"breaksink"
                )

                sink_lines.append(
                    f"breaksink      = {sink!r}"
                )

                if sink:
                    try:
                        state = self._gst.gst_element_get_state(sink)
                        sink_lines.append(
                            f"sink_state     = {state!r}"
                        )
                    except Exception as exc:
                        sink_lines.append(
                            f"sink_state_error = {exc!r}"
                        )

                    try:
                        with sink_diag.open("a", encoding="utf-8") as f:
                            f.write(
                                "\n".join(sink_lines)
                                + "\n"
                            )
                    finally:
                        self._gst.gst_object_unref(sink)
                else:
                    sink_lines.append(
                        "RESULT = BREAKSINK NOT FOUND"
                    )
                    with sink_diag.open("a", encoding="utf-8") as f:
                        f.write(
                            "\n".join(sink_lines)
                            + "\n"
                        )

            except Exception as exc:
                try:
                    with sink_diag.open("a", encoding="utf-8") as f:
                        f.write(
                            f"BREAK SINK DIAGNOSTIC EXCEPTION = {exc!r}\n"
                        )
                except Exception:
                    pass

            diag.append(
                f"audio_bus_created = {self.audio_bus!r}"
            )

            result = self._gst.gst_element_set_state(
                self.audio_pipeline,
                self.GST_STATE_PLAYING
            )

            diag.append(
                f"set_state_PLAYING = {result!r}"
            )

            if result == 0:
                diag.append(
                    "RESULT = GST_STATE_PLAYING FAILED"
                )
                self._write_break_audio_diag(diag)

                self.stop_audio()

                raise GStreamerError(
                    "GStreamer n'a pas pu démarrer la musique."
                )

            self._set_pipeline_volume(
                self.audio_pipeline,
                1.0
            )

            diag.append(
                "volume_applied = True"
            )

            if hasattr(self, "_break_eq_enabled"):
                self.set_break_eq(
                    self._break_eq_enabled,
                    getattr(
                        self,
                        "_break_eq_bands",
                        [0, 0, 0, 0, 0]
                    )
                )
                diag.append("break_eq_applied = True")
            else:
                diag.append("break_eq_applied = False")

            diag.extend([
                f"audio_pipeline_after = {self.audio_pipeline!r}",
                f"audio_bus_after      = {self.audio_bus!r}",
                "RESULT = AUDIO PIPELINE STARTED",
                "===== END AUDIO PIPELINE DIAGNOSTIC =====",
            ])

            self._write_break_audio_diag(diag)

        except Exception as exc:
            diag.extend([
                f"EXCEPTION_TYPE = {type(exc).__name__}",
                f"EXCEPTION       = {exc}",
                "===== END AUDIO PIPELINE DIAGNOSTIC =====",
            ])

            self._write_break_audio_diag(diag)
            raise
    def _set_pipeline_volume(self, pipeline, level):
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            value = 1.0
        if not pipeline:
            return
        volume = self._gst.gst_bin_get_by_name(pipeline, b"breakvolume")
        if volume:
            try:
                self._set_double_property(volume, "volume", value)
            finally:
                self._gst.gst_object_unref(volume)

    def start_audio_crossfade(self, filename: str):
        """Start the next Break Music track silently for an 8 s crossfade."""
        self.stop_audio_crossfade()
        self.audio_next_pipeline = self._make_audio_pipeline(filename)
        self.audio_next_bus = self._gst.gst_element_get_bus(
            self.audio_next_pipeline
        )
        result = self._gst.gst_element_set_state(
            self.audio_next_pipeline, self.GST_STATE_PLAYING
        )
        if result == 0:
            self.stop_audio_crossfade()
            raise GStreamerError("GStreamer n'a pas pu dÃ©marrer le morceau suivant.")
        self._set_pipeline_volume(self.audio_next_pipeline, 0.0)
        if hasattr(self, "_break_eq_enabled"):
            eq = self._break_eq_enabled
            bands = getattr(self, "_break_eq_bands", [0, 0, 0, 0, 0])
            # Temporarily point the helper at the next pipeline.
            old = self.audio_pipeline
            self.audio_pipeline = self.audio_next_pipeline
            try:
                self.set_break_eq(eq, bands)
            finally:
                self.audio_pipeline = old
        return True

    def set_audio_crossfade_volumes(self, current_level, next_level):
        self._set_pipeline_volume(self.audio_pipeline, current_level)
        self._set_pipeline_volume(self.audio_next_pipeline, next_level)

    def finish_audio_crossfade(self, final_volume):
        """Promote the next pipeline to current after the 8 s crossover."""
        if not self.audio_next_pipeline:
            return False
        old_pipeline = self.audio_pipeline
        old_bus = self.audio_bus

        self.audio_pipeline = self.audio_next_pipeline
        self.audio_bus = self.audio_next_bus
        self.audio_next_pipeline = None
        self.audio_next_bus = None

        if old_pipeline:
            self._gst.gst_element_set_state(old_pipeline, self.GST_STATE_NULL)
            if old_bus:
                self._gst.gst_object_unref(old_bus)
            self._gst.gst_object_unref(old_pipeline)

        self._set_pipeline_volume(self.audio_pipeline, final_volume)
        return True

    def stop_audio_crossfade(self):
        if self.audio_next_pipeline:
            self._gst.gst_element_set_state(
                self.audio_next_pipeline, self.GST_STATE_NULL
            )
            if self.audio_next_bus:
                self._gst.gst_object_unref(self.audio_next_bus)
            self._gst.gst_object_unref(self.audio_next_pipeline)
        self.audio_next_pipeline = None
        self.audio_next_bus = None

    def audio_position_ms(self) -> int:
        if not self.audio_pipeline:
            return 0
        value = ctypes.c_int64()
        ok = self._gst.gst_element_query_position(
            self.audio_pipeline, self.GST_FORMAT_TIME, ctypes.byref(value)
        )
        return int(value.value // 1_000_000) if ok else 0

    def audio_duration_ms(self) -> int:
        if not self.audio_pipeline:
            return 0
        value = ctypes.c_int64()
        ok = self._gst.gst_element_query_duration(
            self.audio_pipeline, self.GST_FORMAT_TIME, ctypes.byref(value)
        )
        return int(value.value // 1_000_000) if ok else 0

    def set_audio_volume(self, percent):
        """Volume 0â€“100 % de la MUSIQUE DU BREAK uniquement."""
        try:
            value = max(0.0, min(1.0, float(percent) / 100.0))
        except (TypeError, ValueError):
            value = 1.0
        self._break_volume_percent = float(percent)
        self._set_break_volume_level(value)


    def _set_break_volume_level(self, level):
        """Niveau interne 0.0â€“1.0 utilisÃ© par le fade fixe de 3 secondes."""
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            value = 1.0
        if not self.audio_pipeline:
            return
        volume = self._gst.gst_bin_get_by_name(
            self.audio_pipeline, b"breakvolume"
        )
        if volume:
            try:
                self._set_double_property(volume, "volume", value)
            finally:
                self._gst.gst_object_unref(volume)


    def stop_audio(self):
        self.stop_audio_crossfade()
        if self.audio_pipeline:
            self._gst.gst_element_set_state(
                self.audio_pipeline, self.GST_STATE_NULL
            )
            if self.audio_bus:
                self._gst.gst_object_unref(self.audio_bus)
            self._gst.gst_object_unref(self.audio_pipeline)
        self.audio_pipeline = None
        self.audio_bus = None

    def poll_audio_bus(self):
        if not self.audio_bus:
            return

        # Poll ERROR and EOS separately.  The previous implementation tried
        # to identify the GstMessage type by reading a hard-coded memory
        # offset.  That is ABI-dependent and can fail silently, which meant
        # the EOS of a Break Music track was not reliably detected.
        #
        # Using the bus filter itself to distinguish the two message types is
        # reliable on the installed GStreamer ABI and requires no structure
        # layout assumptions.

        # Drain pending audio errors first.
        while True:
            message = self._gst.gst_bus_timed_pop_filtered(
                self.audio_bus, 0, self.GST_MESSAGE_ERROR
            )
            if not message:
                break
            try:
                errp = ctypes.c_void_p()
                debugp = ctypes.c_char_p()
                self._gst.gst_message_parse_error(
                    message, ctypes.byref(errp), ctypes.byref(debugp)
                )
                if errp.value:
                    err = ctypes.cast(
                        errp, ctypes.POINTER(_GError)
                    ).contents
                    msg = (
                        err.message.decode("utf-8", "replace")
                        if err.message else "Erreur GStreamer audio"
                    )
                    if self.on_audio_error:
                        self.on_audio_error(msg)
            finally:
                self._gst.gst_message_unref(message)

        # Then consume every pending EOS.  Each EOS represents the end of the
        # current Break Music file; the UI schedules the next track safely on
        # the Qt event loop.
        while True:
            message = self._gst.gst_bus_timed_pop_filtered(
                self.audio_bus, 0, self.GST_MESSAGE_EOS
            )
            if not message:
                break
            try:
                if self.on_audio_eos:
                    self.on_audio_eos()
            finally:
                self._gst.gst_message_unref(message)

    def _apply_pitch(self):
        """Apply the requested pitch to the active karaoke pipeline."""
        if not self.pipeline:
            return

        semitones = self._pending_pitch_semitones
        ratio = math.pow(2.0, semitones / 12.0)

        pitcher = self._gst.gst_bin_get_by_name(
            self.pipeline, b"pitcher"
        )
        if pitcher:
            self._set_float_property(
                pitcher, "pitch", ratio
            )
            self._set_float_property(
                pitcher, "tempo", 1.0
            )
            self._set_float_property(
                pitcher, "rate", 1.0
            )
            self._gst.gst_object_unref(pitcher)

    def set_pitch(self, semitones: int):
        """Transpose pitch by semitones without changing tempo."""
        self._pending_pitch_semitones = max(-6, min(6, int(semitones)))
        self._apply_pitch()

    def set_volume(self, percent: int):
        if not self.pipeline:
            return
        volume = self._gst.gst_bin_get_by_name(
            self.pipeline, b"mastervolume"
        )
        if volume:
            self._set_double_property(
                volume, "volume", max(0.0, min(1.0, percent / 100.0))
            )
            self._gst.gst_object_unref(volume)

    def play(self):
        if self.pipeline:
            self._gst.gst_element_set_state(
                self.pipeline, self.GST_STATE_PLAYING
            )
            # Reapply after the pipeline enters PLAYING so SoundTouch
            # receives the pitch on the active audio chain.
            QTimer.singleShot(100, self._apply_pitch)

    def pause(self):
        if self.pipeline:
            self._gst.gst_element_set_state(
                self.pipeline, self.GST_STATE_PAUSED
            )

    def stop(self):
        if self.pipeline:
            self._gst.gst_element_set_state(
                self.pipeline, self.GST_STATE_NULL
            )
            if self.bus:
                self._gst.gst_object_unref(self.bus)
            self._gst.gst_object_unref(self.pipeline)
        self.pipeline = None
        self.bus = None
        # Video STOP must also stop any active Break Music pipeline.
        self.stop_audio()

    def position_ms(self) -> int:
        if not self.pipeline:
            return 0
        value = ctypes.c_int64()
        ok = self._gst.gst_element_query_position(
            self.pipeline, self.GST_FORMAT_TIME, ctypes.byref(value)
        )
        return int(value.value // 1_000_000) if ok else 0

    def duration_ms(self) -> int:
        if not self.pipeline:
            return 0
        value = ctypes.c_int64()
        ok = self._gst.gst_element_query_duration(
            self.pipeline, self.GST_FORMAT_TIME, ctypes.byref(value)
        )
        return int(value.value // 1_000_000) if ok else 0

    def seek_ms(self, position_ms: int):
        if not self.pipeline:
            return
        self._gst.gst_element_seek_simple(
            self.pipeline,
            self.GST_FORMAT_TIME,
            self.GST_SEEK_FLAG_FLUSH | self.GST_SEEK_FLAG_KEY_UNIT,
            ctypes.c_int64(max(0, int(position_ms)) * 1_000_000),
        )

    def poll_bus(self):
        if not self.bus:
            return
        while True:
            message = self._gst.gst_bus_timed_pop_filtered(
                self.bus, 0, self.GST_MESSAGE_ERROR | self.GST_MESSAGE_EOS
            )
            if not message:
                break
            try:
                # GstMessage's GstMiniObject type field is at offset 56 on the
                # 64-bit ABI used by the Windows x86_64 GStreamer build.
                message_type = ctypes.c_uint32.from_address(
                    int(message) + 56
                ).value
                if message_type == self.GST_MESSAGE_ERROR:
                    errp = ctypes.c_void_p()
                    debugp = ctypes.c_char_p()
                    self._gst.gst_message_parse_error(
                        message, ctypes.byref(errp), ctypes.byref(debugp)
                    )
                    if errp.value:
                        err = ctypes.cast(
                            errp, ctypes.POINTER(_GError)
                        ).contents
                        msg = (
                            err.message.decode("utf-8", "replace")
                            if err.message else "Erreur GStreamer"
                        )
                        if self.on_error:
                            self.on_error(msg)
                elif message_type == self.GST_MESSAGE_EOS:
                    if self.on_eos:
                        self.on_eos()
            finally:
                self._gst.gst_message_unref(message)













