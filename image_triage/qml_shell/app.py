"""Bootstrap for the QML shell: engine, context objects, thumbnail service, and a headless grab mode for checking the layout."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

QML_DIR = Path(__file__).resolve().parent / "qml"
_QT_DLL_DIRECTORY_HANDLES: list[object] = []


def _configure_qt_dll_search() -> None:
    """Keep Qt's dynamically loaded QML dependencies discoverable on Windows."""
    if sys.platform != "win32" or _QT_DLL_DIRECTORY_HANDLES:
        return
    import PySide6

    # QML plugins load after Python's extension import has finished. Its temporary
    # DLL search path is no longer active, so retain our own for the app lifetime.
    _QT_DLL_DIRECTORY_HANDLES.append(
        os.add_dll_directory(str(Path(PySide6.__file__).resolve().parent))
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m image_triage.qml_shell", description=__doc__)
    parser.add_argument("--folder", help="photo folder to open")
    parser.add_argument("--select", default="", help="a photo of that folder to put the grid on once it has been read (a photo opened from the file manager)")
    parser.add_argument("--mock", metavar="DIR", help="open p01..p12.jpg from DIR with the HTML mockup's exact data (for pixel comparison)")
    parser.add_argument("--light", action="store_true", help="start in the light theme")
    parser.add_argument("--size", default="", help="window size in device-independent pixels (default: 1440x900, or 92%% of the screen if that is smaller)")
    parser.add_argument("--grab", metavar="PNG", help="render the window to PNG once thumbnails settle, then exit (for checks)")
    parser.add_argument("--grab-wait", type=float, default=4.0, help="seconds to wait before grabbing (default 4)")
    parser.add_argument("--set", default="", help="setting values applied after startup, 'id=value;id=value' (for headless checks of the settings window)")
    parser.add_argument("--hover", default="", help="park the pointer at 'x,y' (window coordinates) instead of off the grid, to check hover states headlessly")
    parser.add_argument("--click", default="", help="mouse clicks before the grab, 'x,y;x,y' in window coordinates (for headless checks)")
    parser.add_argument("--ui", default="", help="UiState settings applied at startup, e.g. 'tab=faces;insp=false;inspTab=2' (for headless checks)")
    return parser


# The --ui keys that play a scripted scene or fill a window with sample data. They exist for the sample page and its pixel checks; on a
# real folder some of them write (the cull's "done" scene saves reject decisions), so only --mock may use them.
_SCENE_KEYS = ("cullScene", "ctxScene", "refScene", "vwScene", "hoRecipe")
_SCENE_PREFIXES = ("pd", "tx")


def startup_settings(ui: str, *, mock: bool, grab: bool) -> str:
    """What `--ui` may set in this launch: everything on the sample page, the plain interface settings in a headless render of a real
    folder, and nothing in a normal launch (the flag is a hook for checks, not a way to start the app)."""
    if mock:
        return ui
    if not grab:
        return ""
    kept = [pair for pair in ui.split(";")
            if pair.split("=", 1)[0] not in _SCENE_KEYS and not pair.startswith(_SCENE_PREFIXES)]
    return ";".join(kept)


def saved_window_place(saved, screens) -> tuple[str, tuple[int, int, int, int], bool] | None:
    """Where the window was when the app was last closed, if it can be put back there: (screen name, (x, y, width, height), maximised).

    `saved` is what `window_place` stored, `screens` the monitors there are now as (name, (x, y, width, height)) of their usable area.
    None when nothing usable was saved, the monitor is gone, or the window would not be on it (its layout changed): the app then
    opens the way it always did."""
    if not isinstance(saved, dict):
        return None
    try:
        x, y, width, height = (int(saved[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    area = next((rect for name, rect in screens if name == saved.get("screen")), None)
    if area is None or width < 640 or height < 400:
        return None
    ax, ay, aw, ah = area
    shown_w = min(x + width, ax + aw) - max(x, ax)
    shown_h = min(y + height, ay + ah) - max(y, ay)
    if shown_w < 200 or shown_h < 120 or y < ay - 8:              # enough of it on the monitor to find, and its top bar within reach
        return None
    return str(saved["screen"]), (x, y, width, height), bool(saved.get("maximized", True))


def window_place(screen: str, rect: tuple[int, int, int, int] | None, maximized: bool) -> dict | None:
    """What is stored for `saved_window_place`: the monitor, the window's own (not maximised) rectangle and whether it was maximised."""
    if rect is None or not screen:
        return None
    x, y, width, height = rect
    return {"screen": screen, "x": int(x), "y": int(y), "width": int(width), "height": int(height), "maximized": bool(maximized)}


def sweep_viewer_temp(root: str | None = None, older_than_s: float = 600.0) -> list[str]:
    """Remove the viewer's picture folders that an earlier run left in the temporary folder (they are only removed on a clean exit, so a
    crash or a kill left one behind every time). Only called by the one running app, and only folders that have not been touched for a
    while are taken, so a headless check running beside it keeps its own. Returns what was removed."""
    removed = []
    base = Path(root or tempfile.gettempdir())
    try:
        candidates = [entry for entry in base.iterdir() if entry.name.startswith("imagetriage-viewer-")]
    except OSError:
        return removed
    now = time.time()
    for entry in candidates:
        try:
            if entry.is_symlink() or not entry.is_dir() or now - entry.stat().st_mtime < older_than_s:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(str(entry))
    return removed


def stop_background(runner, pools, services, wait_ms: int = 2500) -> None:
    """Quitting: stop what runs behind the window, so the process ends with it.

    A copy or move stops between photos, everything still queued is dropped, and what is already running gets a bounded moment to
    end. Before, nothing was told to stop and the process stayed until every queued thumbnail and job had been worked through."""
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool

    deadline = time.monotonic() + wait_ms / 1000.0
    left = lambda: max(0, int((deadline - time.monotonic()) * 1000))
    transfer = bool(getattr(runner, "running", False))
    try:
        runner.cancel()
    except Exception:
        pass
    for service in services:
        try:
            service.shutdown(min(left(), 500))
        except Exception:
            pass
    for pool in (*pools, QThreadPool.globalInstance()):
        pool.clear()
    for pool in (*pools, QThreadPool.globalInstance()):
        pool.waitForDone(left())
    if transfer:
        # The copy or move that was stopped reports what it had done on a queued signal; hand that over now, while there is still someone
        # to take the moved photos' decisions with them.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    headless = bool(args.grab or args.mock)
    if not args.grab:                                  # headless checks have their own output; a real session keeps a log (it has no console to print to)
        from . import diagnostics

        diagnostics.install()                          # before the application object: a failure while Qt starts is then in the log too
    state: dict = {}
    try:
        return _run(args, state)
    except Exception:
        from . import diagnostics

        hide = state.get("hide_splash")
        if hide is not None:
            try:
                hide()                                 # the message must not sit behind a splash that will never go away
            except Exception:
                pass
        diagnostics.report_fatal("Image Triage could not start.", traceback.format_exc(), dialog=not headless)
        return 1


def _run(args, state: dict) -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    _configure_qt_dll_search()

    from PySide6.QtCore import Q_ARG, QCoreApplication, QMetaObject, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6 import QtQuick as _QtQuick  # noqa: F401 - registers QQuickWindow so the root object is wrapped with grabWindow()

    from .. import memtrace
    from .batch_model import BatchModel
    from .bestof_model import BestOfModel
    from .cull_model import CullModel
    from .file_actions import FileActions
    from .menu_model import MenuModel, ToastModel
    from .collections_model import CollectionsModel
    from .ai_scores import AiScores
    from .search_model import SearchModel
    from .capture import CaptureService
    from .filter_match import FilterMatcher
    from .ui_memory import UiMemory
    from .histograms import HistogramService
    from .inspection import InspectionService
    from .library_nav import LibraryNavModel
    from .people_model import PeopleModel
    from .editor_host import EditorHost
    from .viewer_host import ViewerHost
    from .filters_model import FiltersModel
    from .palette_model import PaletteModel
    from .photos import GridModel, PhotoModel
    from .reference_model import ReferenceModel
    from .handoff_model import HandoffModel
    from .pocketdrop_model import PocketDropModel
    from .transfer_model import TransferModel
    from .settings_model import ACCENTS, SettingsModel
    from .theme import Theme
    from .thumbs import ThumbService

    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    from PySide6.QtCore import Qt

    # fractional scales (125%, 150%, 175%) are used as they are, not rounded to whole numbers, so the layout is the same size in
    # device-independent pixels on every monitor; Qt 6 turns high-DPI scaling on by itself
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QGuiApplication(sys.argv[:1])
    if not args.grab:
        from . import diagnostics

        diagnostics.attach(app)
    # One app per person: a normal launch hands what it was asked to open to the app that is already running and ends. The headless
    # and sample launches (and anything driven by the check flags) are separate on purpose.
    instance = None
    if not (args.grab or args.mock or args.size or args.ui or args.set or args.click or args.hover):
        from .single_instance import InstanceServer, send_to_running

        if send_to_running({"folder": args.folder or "", "select": args.select or ""}):
            return 0
        instance = InstanceServer(parent=app)
        if not instance.listen():
            instance = None
    from PySide6.QtGui import QIcon

    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1] / "assets" / "app_icon-v5.ico")))      # the taskbar and every window
    from PySide6.QtGui import QFont

    base_font = QFont("Inter")
    base_font.setFamilies(["Inter", "Segoe UI", "Helvetica Neue", "Arial"])
    base_font.setPixelSize(13)
    base_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)   # closer to the mockup's browser rendering
    if hasattr(base_font, "setFeature"):
        from PySide6.QtGui import QFont as _F
        base_font.setFeature(_F.Tag("tnum"), 1)   # font-variant-numeric: tabular-nums, as in the mockup
    app.setFont(base_font)
    memtrace.start(app)

    theme = Theme(light=args.light, parent=app)
    photos = PhotoModel(parent=app)
    toasts = ToastModel(parent=app)
    settings = SettingsModel(memory=bool(args.mock), parent=app)
    palette = PaletteModel(memory=bool(args.mock), parent=app)
    adv_filters = FiltersModel(photos, memory=bool(args.mock), parent=app)
    handoff = HandoffModel(photos, memory=bool(args.mock), parent=app)
    from .workflow_runner import WorkflowRunner

    runner = WorkflowRunner(parent=app)
    transfers = TransferModel(photos, memory=bool(args.mock), parent=app, toasts=toasts, runner=runner)
    handoff.jobPrepared.connect(transfers.setJob)          # before the cards are asked for: what a Start will run
    handoff.runRequested.connect(transfers.handoff)
    collections = CollectionsModel(photos, memory=bool(args.mock), parent=app)
    people = PeopleModel(photos, memory=bool(args.mock), parent=app)
    best_of = BestOfModel(photos, parent=app)
    batch = BatchModel(photos, memory=bool(args.mock), parent=app, toasts=toasts)
    from .file_jobs import FileJobs

    file_jobs = FileJobs(parent=app)                   # delete, move, copy and the Safe Trash window: one at a time, off the UI thread
    files = FileActions(photos, toasts, memory=bool(args.mock), parent=app, jobs=file_jobs)
    transfers.follow(file_jobs)                        # their progress shows in the transfer card
    # Before the decisions are flushed below: an operation that is stopped here still hands over the photos it had moved, so their
    # decisions and collections follow them. It stops between photos, so no bundle is left half moved.
    app.aboutToQuit.connect(files.shutdown)
    from .safe_trash_model import SafeTrashModel

    safe_trash = SafeTrashModel(photos, toasts, file_jobs, memory=bool(args.mock), parent=app)
    cull = CullModel(photos, memory=bool(args.mock), parent=app, toasts=toasts, files=files)
    cull.runFinished.connect(people.reload)            # an analysis has just written the face index: the People page reads it again
    app.aboutToQuit.connect(cull.shutdown)
    # Connected here, before the decisions are flushed below: a move that is stopped still hands over the photos it had moved. The
    # services it stops are made further down and added to `stoppable` then.
    stoppable: list = []
    app.aboutToQuit.connect(lambda: stop_background(runner, (), stoppable))
    menu = MenuModel(photos, collections, cull, toasts, memory=bool(args.mock), parent=app, files=files)
    pocket_drop = PocketDropModel(photos, memory=bool(args.mock), parent=app, collections=collections)
    reference = ReferenceModel(photos, toasts, memory=bool(args.mock), parent=app)
    if not args.mock:
        from .decision_bridge import DecisionBridge

        decisions = DecisionBridge(photos, toasts=toasts, parent=app)
        app.aboutToQuit.connect(decisions.flush)
    app.aboutToQuit.connect(batch.cleanup)
    app.aboutToQuit.connect(pocket_drop.shutdown)

    def apply_theme() -> None:
        index = settings.accentIndex
        system_dark = app.styleHints().colorScheme() != Qt.ColorScheme.Light
        theme.applyMode(settings.themeMode, ACCENTS[index][1] if 0 <= index < len(ACCENTS) else -1, settings.contrast, system_dark, settings.darkTone)

    settings.previewChanged.connect(apply_theme)
    app.styleHints().colorSchemeChanged.connect(lambda *_: apply_theme())      # Auto follows the system's light or dark when it changes
    apply_theme()
    if not args.mock:
        settings.apply_ai_scoring()                              # the cull sliders and the base score weight, as the Widgets app pushes them at start
    if not args.mock and settings.value("perfLog") is True:
        from ..perf import perf_logger

        perf_logger().set_enabled(True, reason="startup")       # as the backend does when the setting was left on
    if args.light:
        theme.light = True
    engine = QQmlApplicationEngine()
    from .raw_preview import install_images
    install_images(engine)
    thumbs = ThumbService(parent=app)
    photos.set_after_scan(thumbs.prime_folder)
    context = engine.rootContext()
    from .scroll_cursor import ScrollCursor
    context.setContextProperty("nativeScrollCursor", ScrollCursor(parent=app))
    context.setContextProperty("theme", theme)
    context.setContextProperty("photos", photos)
    gridModel = GridModel(photos, parent=app)
    context.setContextProperty("gridModel", gridModel)
    context.setContextProperty("settings", settings)
    context.setContextProperty("cmdPalette", palette)
    context.setContextProperty("advFilters", adv_filters)
    context.setContextProperty("batch", batch)
    context.setContextProperty("pocketDrop", pocket_drop)
    context.setContextProperty("collections", collections)
    context.setContextProperty("people", people)
    context.setContextProperty("bestOf", best_of)
    context.setContextProperty("cull", cull)
    context.setContextProperty("toasts", toasts)
    context.setContextProperty("ctxMenu", menu)
    context.setContextProperty("fileActions", files)
    context.setContextProperty("safeTrash", safe_trash)
    context.setContextProperty("reference", reference)
    context.setContextProperty("handoff", handoff)
    context.setContextProperty("transfers", transfers)
    context.setContextProperty("thumbs", thumbs)
    photo_services = (HistogramService(parent=app), InspectionService(parent=app), CaptureService(parent=app))
    context.setContextProperty("histograms", photo_services[0])
    context.setContextProperty("inspection", photo_services[1])
    context.setContextProperty("captureData", photo_services[2])
    stoppable.extend((thumbs, *photo_services))
    ai_scores = AiScores(photos, memory=bool(args.mock), parent=app)
    photo_search = SearchModel(photos, parent=app)
    ai_scores.revisionChanged.connect(photo_search.refresh)
    app.aboutToQuit.connect(photo_search.shutdown)
    ai_scores.indexWarning.connect(lambda message: toasts.push("error", "Search index could not be imported", message))
    photos.set_matcher(FilterMatcher(ai_result=ai_scores.result_for_row, parent=app))          # what the Advanced filters ask beyond decision, rating and RAW or JPEG
    settings.aiScoringChanged.connect(ai_scores.reload)           # a saved report is classified as it is read: read it again with the new sliders
    cull.runFinished.connect(ai_scores.reload)          # an analysis has just written its report: the inspector reads it
    context.setContextProperty("aiScores", ai_scores)
    ui_memory = UiMemory(memory=bool(args.mock or args.grab), parent=app)     # the interface as it was left; headless checks start from the defaults
    context.setContextProperty("uiMemory", ui_memory)
    library_nav = LibraryNavModel(photos, memory=bool(args.mock), persist=not args.grab, parent=app)
    context.setContextProperty("libraryNav", library_nav)
    popout = ViewerHost(photos, thumbs, collections, files=files, cull=cull, toasts=toasts, memory=bool(args.mock or args.grab), parent=app)
    app.aboutToQuit.connect(popout.shutdown)
    context.setContextProperty("popout", popout)
    editor = EditorHost(memory=bool(args.mock or args.grab), parent=app)
    editor.editSaved.connect(thumbs.refresh_edit)
    if not args.mock and not args.grab:
        editor.setBrowseSource(photos, thumbs, popout)
    app.aboutToQuit.connect(editor.shutdown)
    context.setContextProperty("editor", editor)
    context.setContextProperty("startup", startup_settings(args.ui, mock=bool(args.mock), grab=bool(args.grab)))
    engine.addImportPath(str(QML_DIR))
    if args.size:
        width, height = (int(part) for part in args.size.lower().split("x"))
    else:
        # sizes are in device-independent pixels, so the same on a 1080p, 1440p or 4K screen at any scale; only a screen smaller than
        # the design size (e.g. 1280x720 at 100%, or 4K at 300%) shrinks the window
        area = app.primaryScreen().availableGeometry()
        width, height = min(1440, int(area.width() * 0.92)), min(900, int(area.height() * 0.92))
    # the launch splash (its own frameless window) is up while the main window is built; not for headless checks, which have their own scenes
    splash = not (args.grab or args.mock)
    qml_problems: list[str] = []
    engine.warnings.connect(lambda problems: qml_problems.extend(problem.toString() for problem in problems))
    if splash:
        state["hide_splash"] = reference.splashHide
        engine.load(QUrl.fromLocalFile(str(QML_DIR / "SplashWindow.qml")))
        reference.splashShow("Starting\u2026", 8)
        app.processEvents()
        reference.splashShow("Loading the interface\u2026", 35)
        app.processEvents()
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    roots = engine.rootObjects()
    if not roots or (splash and len(roots) < 2):
        # The interface did not load. Without this the app ended (or sat behind its splash) with nothing said.
        from . import diagnostics

        if splash:
            reference.splashHide()
            app.processEvents()
        diagnostics.report_fatal("Image Triage could not load its interface.", "\n".join(qml_problems), dialog=not (args.grab or args.mock))
        return 1
    window = roots[-1]
    pocket_drop.setWindowHandle(int(window.winId()))
    popout.setWindow(window)
    editor.setWindow(window)
    window.setWidth(width)
    window.setHeight(height)
    if not args.size:
        window.setPosition(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2)
    real_window = not (args.size or args.grab or args.mock)
    place = None
    if real_window:
        # Where the window was left: its monitor, its size there and whether it was maximised. When that monitor is not there any more
        # (or nothing was saved) it opens as it always did, maximised on the main one.
        place = saved_window_place(ui_memory.get("window", None), [(s.name(), s.availableGeometry().getRect()) for s in app.screens()])
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", place[1][2] if place and not place[2] else width))
    if real_window:
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QWindow

        if splash:
            window.setOpacity(0.0)                        # it builds out of sight behind the splash and is shown whole (see `reveal`)
        if place is not None:
            window.setScreen(next(s for s in app.screens() if s.name() == place[0]))
            window.setGeometry(QRect(*place[1]))
        if place is None or place[2]:
            window.showMaximized()                        # the real app opens maximised; headless checks keep their exact size
        else:
            window.show()
        own_rect = [place[1] if place else None]          # the window's rectangle when it is not maximised
        was_maximized = [place[2] if place else True]
        settle = QTimer(window)
        settle.setSingleShot(True)
        settle.setInterval(300)                           # after a maximise or restore has finished changing the geometry

        def note_place() -> None:
            visibility = window.visibility()
            if visibility == QWindow.Visibility.Windowed:
                own_rect[0], was_maximized[0] = window.geometry().getRect(), False
            elif visibility in (QWindow.Visibility.Maximized, QWindow.Visibility.FullScreen):
                was_maximized[0] = True

        def save_place() -> None:
            if settle.isActive():
                note_place()
            stored = window_place(window.screen().name() if window.screen() else "", own_rect[0], was_maximized[0])
            if stored is not None:
                ui_memory.set("window", stored)

        settle.timeout.connect(note_place)
        for changed in (window.xChanged, window.yChanged, window.widthChanged, window.heightChanged, window.visibilityChanged):
            changed.connect(lambda *_: settle.start())
        app.aboutToQuit.connect(save_place)
    for pair in filter(None, args.set.split(";")):
        key, _, raw = pair.partition("=")
        settings.set(key, True if raw == "true" else False if raw == "false" else (float(raw) if "." in raw else int(raw)) if raw.lstrip("-").replace(".", "", 1).isdigit() else raw)
    if args.mock:
        photos.loadMock(args.mock)
    elif args.folder and args.select:
        library_nav.open_file(args.select)                 # a photo opened from the file manager: its folder, with the grid on that photo
    elif args.folder:
        library_nav.openFolder(args.folder)                # loads it, and puts the Library page's tree on its drive
    else:
        library_nav.restoreLastFolder()                    # no folder asked for: the one that was open when the app was closed, on the photo that was current
    if instance is not None:
        from PySide6.QtGui import QWindow as _Window

        def another_launch(message: dict) -> None:
            """A later launch handed over what it was asked to open: open it here and come to the front."""
            folder, select = str(message.get("folder") or ""), str(message.get("select") or "")
            if select:
                library_nav.open_file(select)
            elif folder:
                library_nav.openFolder(folder)
            if window.visibility() == _Window.Visibility.Minimized:
                window.showMaximized() if was_maximized[0] else window.showNormal()
            window.raise_()
            window.requestActivate()

        instance.received.connect(another_launch)
        app.aboutToQuit.connect(instance.close)
        # Only the one running app clears what earlier runs left behind, off the interface thread.
        threading.Thread(target=sweep_viewer_temp, name="qml-temp-sweep", daemon=True).start()

    def reveal() -> None:
        window.setOpacity(1.0)
        reference.splashHide()

    if splash:
        # The splash stays until the first folder's photos are in the grid, so the window appears whole instead of an empty grid filling in later.
        if library_nav.openPath and not args.mock:
            reference.splashShow("Reading your folder…", 70)

            def folder_done() -> None:
                if photos.loading:
                    return
                photos.loadingChanged.disconnect(folder_done)
                reference.splashShow("Ready", 100)
                # The photos are in the model, but the cards still have to be built and their pictures loaded: hold the splash until the first
                # screenful of pictures is up (or a moment passes), so the grid is there when the splash goes.
                need = [min(photos.shown, 8)]
                hidden = []

                def hide() -> None:
                    if not hidden:
                        hidden.append(True)
                        thumbs.painted.disconnect(painted)
                        reveal()

                def painted() -> None:
                    need[0] -= 1
                    if need[0] <= 0:
                        QTimer.singleShot(120, hide)

                if need[0] > 0:
                    thumbs.painted.connect(painted)
                    QTimer.singleShot(2500, hide)
                else:
                    QTimer.singleShot(250, reveal)

            photos.loadingChanged.connect(folder_done)
            QTimer.singleShot(15000, reveal)         # never leave it up if the folder never answers
        else:
            reference.splashShow("Ready", 100)
            QTimer.singleShot(250, reveal)

    if args.grab:
        def grab() -> None:
            image = window.grabWindow()
            image.save(args.grab)
            print(f"grabbed {image.width()}x{image.height()} -> {args.grab}; {photos.shown} photos")
            app.quit()

        from PySide6.QtCore import QPoint
        hx, hy = (int(v) for v in args.hover.split(",")) if args.hover else (3, 3)
        if args.hover or args.click:
            from PySide6.QtTest import QTest
            QTimer.singleShot(300, lambda: QTest.mouseMove(window, QPoint(hx, hy)))
        else:
            from PySide6.QtGui import QCursor
            QTimer.singleShot(300, lambda: QCursor.setPos(window.mapToGlobal(QPoint(3, 3))))
        QTimer.singleShot(int(args.grab_wait * 1000), grab)
        if args.click:
            from PySide6.QtCore import Qt

            points = [tuple(int(v) for v in part.split(",")) for part in args.click.split(";") if part.strip()]

            def press(index: int = 0) -> None:
                if index >= len(points):
                    return
                x, y = points[index]
                QTest.mouseMove(window, QPoint(x, y))
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
                QTimer.singleShot(700, lambda: press(index + 1))

            QTimer.singleShot(int(args.grab_wait * 1000) - 2500, press)
    code = app.exec()
    memtrace.stop()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
