"""Standalone QML preview experiment. Reads a RAW; never writes its recipe or sidecars."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

# Match the source application's Windows Qt DLL setup, without launching it.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from image_triage.qml_shell.app import _configure_qt_dll_search
_configure_qt_dll_search()

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider


class Backend:
    def __init__(self, executable, log):
        self.log = open(log, 'wb')
        self.proc = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def request(self, request):
        self.proc.stdin.write((json.dumps(request) + '\n').encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline(128 * 1024)
        if not line:
            raise RuntimeError('Preview backend stopped; see backend.log')
        header = json.loads(line)
        length = header.get('bytes', 0)
        if not isinstance(length, int) or not 0 <= length <= 2400 * 2400 * 4:
            raise RuntimeError('Invalid frame size')
        data = bytearray()
        while len(data) < length:
            chunk = self.proc.stdout.read(length - len(data))
            if not chunk:
                raise RuntimeError('Incomplete preview frame')
            data.extend(chunk)
        if not header.get('ok'):
            raise RuntimeError(header.get('error', 'Preview failed'))
        if length != header['width'] * header['height'] * 4:
            raise RuntimeError('Frame dimensions disagree')
        return header, bytes(data)

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        self.log.close()


class Frames(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.image = QImage()

    def requestImage(self, _id, size, requested):
        size.setWidth(self.image.width())
        size.setHeight(self.image.height())
        return self.image


class Preview(QObject):
    changed = Signal()
    arrived = Signal(object, object, object)
    failed = Signal(str)

    def __init__(self, args, frames):
        super().__init__()
        self.args, self.frames = args, frames
        self._status = 'Opening sensor data…'
        self._src, self._histogram, self._ready = '', [], False
        self._revision = 0
        self._values = dict(exposure=0.0, shadows=0.0, backend='auto')
        self._mailbox = None
        self._stopping = False
        self._condition = threading.Condition()
        self._present = None
        self._samples = []
        self._backend = None
        self.arrived.connect(self._arrived)
        self.failed.connect(self._failed)
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self._enqueue)
        self._thread = threading.Thread(target=self._run, daemon=True, name='raw-spike')
        self._thread.start()

    source = Property(str, lambda s: s._src, notify=changed)
    status = Property(str, lambda s: s._status, notify=changed)
    histogram = Property('QVariantList', lambda s: s._histogram, notify=changed)
    ready = Property(bool, lambda s: s._ready, notify=changed)

    @Slot(float, float, str)
    def adjust(self, exposure, shadows, backend):
        self._values = dict(exposure=exposure, shadows=shadows, backend=backend)
        self._revision += 1
        self._present = None
        self._input_at = time.perf_counter()
        self._status = 'Updating preview…'
        self.changed.emit()
        # During a drag, dispatch at bounded intervals rather than waiting until
        # the pointer stops; the mailbox replaces all but the latest request.
        if not self._timer.isActive():
            self._timer.start()

    def _enqueue(self):
        with self._condition:
            self._mailbox = ({'op': 'render', 'revision': self._revision, **self._values}, self._input_at)
            self._condition.notify()

    def _run(self):
        backend = None
        try:
            backend = Backend(self.args.backend, self.args.output / 'backend.log')
            self._backend = backend
            start = time.perf_counter()
            request = dict(op='open', path=str(self.args.raw.resolve()), edge=self.args.edge, revision=0,
                           exposure=0, shadows=0, backend='auto')
            header, data = backend.request(request)
            self.arrived.emit(header, data, start)
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stopping or self._mailbox is not None)
                    if self._stopping:
                        break
                    request, start = self._mailbox
                    self._mailbox = None
                header, data = backend.request(request)
                self.arrived.emit(header, data, start)
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if backend:
                backend.close()

    @Slot(object, object, object)
    def _arrived(self, header, data, start):
        if self._stopping or header['revision'] != self._revision:
            return
        self.frames.image = QImage(data, header['width'], header['height'], header['width'] * 4,
                                   QImage.Format_RGBA8888).copy()
        self._src = f'image://frames/{self._revision}'
        self._histogram = header['histogram']
        self._ready = True
        self._present = (header, start)
        self._status = f"{header['backend'].upper()} · processing {header['workerMs']:.0f} ms · waiting for display"
        self.changed.emit()

    @Slot(int)
    def imageReady(self, revision):
        if self._present and self._present[0]['revision'] == revision:
            self._present[0]['imageReady'] = True

    @Slot()
    def presented(self):
        if not self._present or not self._present[0].get('imageReady'):
            return
        header, start = self._present
        self._present = None
        header['inputToFrameSwapMs'] = (time.perf_counter() - start) * 1000
        self._samples.append(header)
        self._status = (f"{header['backend'].upper()} · {header['inputToFrameSwapMs']:.0f} ms to screen · "
                        f"{header['width']} × {header['height']} proxy · sensor opens {header['opens']}")
        with open(self.args.output / 'presentation.jsonl', 'a', encoding='utf8') as stream:
            stream.write(json.dumps(header) + '\n')
        self.changed.emit()

    @Slot(str)
    def _failed(self, error):
        self._ready = False
        self._status = error
        self.changed.emit()

    def close(self):
        self._timer.stop()
        with self._condition:
            self._stopping = True
            self._condition.notify()
        self._thread.join(timeout=3)
        if self._thread.is_alive() and self._backend is not None:
            # A decode or failed native request must not leave a hidden process
            # behind when the experiment closes. Kill only our owned worker.
            self._backend.proc.kill()
            self._thread.join(timeout=3)
        if self._thread.is_alive():
            raise RuntimeError('Preview worker did not stop')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('raw', type=Path)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('K:/Builds/ImageTriage/raw-preview-spike/results'))
    parser.add_argument('--edge', type=int, default=1200, choices=[900, 1200, 1800, 2400])
    parser.add_argument('--smoke', action='store_true', help='Exercise the actual QML slider and save a capture')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication(sys.argv[:1])
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda warnings: print('\n'.join(w.toString() for w in warnings), file=sys.stderr, flush=True))
    frames = Frames()
    engine.addImageProvider('frames', frames)
    preview = Preview(args, frames)
    engine.rootContext().setContextProperty('preview', preview)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('Spike.qml').resolve())))
    if not engine.rootObjects():
        print('No QML root window was created', file=sys.stderr, flush=True)
        preview.close()
        return 1
    window = engine.rootObjects()[0]
    window.frameSwapped.connect(preview.presented)
    app.aboutToQuit.connect(preview.close)
    if args.smoke:
        from PySide6.QtCore import QPointF
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest
        state = {'stage': 0, 'started': time.monotonic()}
        timer = QTimer()
        def smoke():
            if time.monotonic() - state['started'] > 120:
                print(preview.status, flush=True)
                app.exit(2)
            if state['stage'] == 0 and preview._samples:
                state['stage'] = 1
                window.grabWindow().save(str(args.output / 'before.png'))
                slider = window.findChild(QQuickItem, 'exposureSlider')
                point = lambda fraction: slider.mapToScene(QPointF(slider.width()*fraction, slider.height()/2)).toPoint()
                QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point(.5))
                QTest.mouseMove(window, point(.65), 60)
                QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point(.65))
            elif state['stage'] == 1 and len(preview._samples) >= 2:
                state['stage'] = 2
                slider = window.findChild(QQuickItem, 'shadowsSlider')
                point = lambda fraction: slider.mapToScene(QPointF(slider.width()*fraction, slider.height()/2)).toPoint()
                QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point(.5))
                QTest.mouseMove(window, point(.7), 60)
                QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point(.7))
            elif state['stage'] == 2 and len(preview._samples) >= 3:
                state['stage'] = 3
                window.findChild(QQuickItem, 'processingMode').setProperty('currentIndex', 1)
                window.findChild(QQuickItem, 'shadowsSlider').setProperty('value', 40)
                for i in range(50):
                    window.findChild(QQuickItem, 'exposureSlider').setProperty('value', i/100)
                    preview.adjust(i/100, 40, 'cpu')
                state['final_revision'] = preview._revision
            elif state['stage'] == 3 and preview._samples[-1]['revision'] == state['final_revision']:
                window.grabWindow().save(str(args.output / 'after.png'))
                print(json.dumps({k:v for k,v in preview._samples[-1].items() if k != 'histogram'}), flush=True)
                app.quit()
        timer.timeout.connect(smoke)
        timer.start(100)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
