"""The QML shell's PocketDrop page: the model around PocketDrop's engine, the Library groups, and the engine itself.

The model tests run on any machine (sample mode). The engine tests build the native module for the machine the tests run on
(native/pocketdrop is portable C++20; Windows gets pocketdrop.dll from build_windows.bat) and skip when no compiler or headers exist.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell import pocketdrop_model as pdm
from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.palette_model import PaletteModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from qml_support import collection_models
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.transfer_model import TransferModel
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme
from image_triage.qml_shell.thumbs import ThumbService

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QGuiApplication as QApplication
    return QCoreApplication.instance() or QApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def photo_rows(folder: Path):
    rows = []
    for index, (name, decision, rating) in enumerate((("A1.NEF", "win", 5), ("A2.NEF", "keep", 3), ("B1.jpg", "win", 4), ("B2.jpg", "", 0))):
        path = folder / name
        path.write_bytes(b"x" * (1000 + index))
        companion = folder / (name.rsplit(".", 1)[0] + ".JPG")
        companions = ()
        if name.endswith("NEF"):
            companion.write_bytes(b"j" * 500)
            companions = (str(companion),)
        rows.append({"path": str(path), "name": name, "ext": name.split(".")[1].upper(), "raw": name.endswith("NEF"), "decision": decision,
                     "rating": rating, "ai": 70, "companions": companions})
    return rows


@pytest.fixture
def photos(qapp, tmp_path):
    model = PhotoModel(parent=qapp)
    model.setRows(photo_rows(tmp_path), str(tmp_path))
    return model


# ---- sample mode ------------------------------------------------------------------------------------------------------------

def test_sample_state_has_every_field_the_panel_reads(qapp, photos):
    model = PocketDropModel(photos, memory=True, parent=qapp)
    model.start()
    st = model.st
    for key in ("mode", "status", "tone", "card", "cardMsg", "pct", "badges", "qrSize", "qrPath", "hasLink", "canOpen", "linkText", "linkEmpty",
                "copied", "copyLabel", "info", "rows", "menu", "offline"):
        assert key in st, key
    assert st["card"] == "qr" and st["qrSize"] == 29 and st["qrPath"].startswith("M0 0h7v1h-7zM8 0h1v1h-1z")   # the mockup's QR, bit for bit
    assert set(st["menu"]) >= {"burn", "auto", "warn", "topmost", "tunnelOpen", "inbox", "addrs"}


def test_sample_modes_and_states(qapp, photos):
    model = PocketDropModel(photos, memory=True, parent=qapp)
    model.setSample("anywhere", "missing", "none", "empty")
    assert (model.st["mode"], model.st["card"], model.st["badges"][1]) == (1, "missing", "warn")
    model.command("card")                                   # Enable
    assert model.st["card"] == "qr" and model.st["info"]["kind"] == "code"
    model.setSample("offline", "bluetooth", "none", "shared")
    assert (model.st["card"], model.st["tone"]) == ("bluetooth", "warn")
    model.setSample("wifi", "ready", "sending", "shared")
    assert (model.st["tone"], model.st["info"]["kind"]) == ("send", "active")


def test_risk_prompt_comes_before_anywhere_unless_dismissed(qapp, photos):
    model = PocketDropModel(photos, memory=True, parent=qapp)
    model.start()
    model.setMode(1)
    assert model.riskPending and model.st["mode"] == 0
    model.confirmRisk(False, False)
    assert not model.riskPending and model.st["mode"] == 0
    model.setMode(1)
    model.confirmRisk(True, True)                           # continue, and don't warn again
    assert model.st["mode"] == 1 and model.st["menu"]["warn"] is False
    model.setMode(0)
    model.setMode(1)
    assert not model.riskPending and model.st["mode"] == 1


def test_library_groups_are_one_row_with_their_files_behind_them(qapp, photos):
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    cid = collections.save("", "Shortlist", "#4c8dff", "", False)
    collections.addVisible(cid)
    key = f"col:{cid}"
    model = PocketDropModel(photos, memory=True, parent=qapp, collections=collections)
    model.refreshLibrary(0)
    entries = {e["key"]: e for bucket in model.library.values() for e in bucket}
    assert set(entries) == {"sel", "win", "keep"} | {f"col:{c['id']}" for c in collections.rows}
    model.addLibrary(key)
    row = model.rows[0]
    assert (row["kind"], row["title"], row["asLabel"]) == ("coll", "Shortlist", "Originals")
    assert len(row["thumbs"]) == 3
    model.addLibrary(key)                                   # adding twice changes nothing
    assert [r["kind"] for r in model.rows].count("coll") == 1
    before = dict(model.rows[0])
    model.toggleSendAs(row["key"])                          # the files are sent as they are: nothing may say otherwise
    assert model.rows[0] == before and "as JPEG" not in model.rows[0]["sub"]
    model.removeRow(row["key"])
    assert not [r for r in model.rows if r["kind"] == "coll"]


def test_collection_membership_uses_the_real_decisions(qapp, photos):
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    cid = collections.save("", "Shortlist", "", "", False)
    collections.addSelection(cid, 0)
    model = PocketDropModel(photos, parent=qapp, memory=False, collections=collections)       # no native module needed to list what would be shared
    model.refreshLibrary(0)
    entries = {e["key"]: e for bucket in model.library.values() for e in bucket}
    assert entries["win"]["count"] == 2 and entries["keep"]["count"] == 1
    assert entries[f"col:{cid}"]["count"] == 1 and entries["col:c5"]["empty"]       # the sample "Client proofs" has nobody in it
    assert entries["sel"]["count"] == 1 and entries["sel"]["paths"][0].endswith("A1.NEF")
    assert len(entries["sel"]["paths"]) == 2                          # the RAW's JPG goes with it
    assert entries["win"]["sub"] == "2 photos"                         # the count at once; the size when the files have been measured, off the interface thread
    deadline = time.monotonic() + 5
    while "size" not in entries["win"] and time.monotonic() < deadline:
        pump(qapp, 0.02)
    assert entries["win"]["sub"].startswith("2 photos · ") and entries["win"]["size"] > 0


def test_js_random_numbers_match_the_mockup():
    # the mockup's first draws for seed 7, from node: 0.5970560557195247 0.29926478876698054 0.5422919991157447
    step = pdm._js_lcg(7)
    assert [step(), step(), step()] == [0.5970560557195247, 0.29926478876698054, 0.5422919991157447]


# ---- the page loads in the real window ----------------------------------------------------------------------------------------

def test_page_loads_without_qml_errors(qapp, tmp_path):
    from PySide6.QtQml import QQmlApplicationEngine

    theme = Theme(parent=qapp)
    model = PhotoModel(parent=qapp)
    model.setRows(photo_rows(tmp_path), str(tmp_path))
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    context = engine.rootContext()
    palette = PaletteModel(memory=True, parent=qapp)
    for name, obj in (("theme", theme), ("photos", model), ("settings", SettingsModel(memory=True, parent=qapp)), ("cmdPalette", palette),
                      ("advFilters", FiltersModel(model, memory=True, parent=qapp)), ("batch", BatchModel(model, memory=True, parent=qapp)),
                      *collection_models(model, qapp), ("handoff", HandoffModel(model, memory=True, parent=qapp)), ("transfers", TransferModel(model, memory=True, parent=qapp)), ("thumbs", ThumbService(parent=qapp)),
                      ("startup", "tab=drop;pdMode=anywhere;pdState=missing;pdList=mixed")):
        context.setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    from PySide6.QtCore import Q_ARG, QMetaObject

    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    pump(qapp, 0.8)
    assert not [w for w in warnings if any(k in w for k in ("TypeError", "ReferenceError", "is not a type", "Unable to assign", "non-existent"))], warnings
    window.close()


# ---- the engine ------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="session")
def native_lib(tmp_path_factory):
    if sys.platform == "win32":
        staged = os.environ.get("IMAGE_TRIAGE_POCKETDROP_LIB")
        if staged:
            assert Path(staged).is_file(), "staged PocketDrop DLL is missing"
            return Path(staged)
        pytest.skip("Build a staged DLL and set IMAGE_TRIAGE_POCKETDROP_LIB to validate it")
    gxx = shutil.which("g++") or shutil.which("clang++")
    pkg = shutil.which("pkg-config")
    if not gxx or not pkg or subprocess.run([pkg, "--exists", "libcurl", "gdk-pixbuf-2.0"]).returncode != 0:
        pytest.skip("needs g++, libcurl and gdk-pixbuf headers to build the native module")
    out = tmp_path_factory.mktemp("pd") / "libpocketdrop.so"
    base = ROOT / "native" / "pocketdrop"
    sources = [f"src/core/{n}.cpp" for n in ("qr", "deflate", "bundle", "http", "tunnel", "nearby", "pc_transfer", "hotspot_stub")] + [
        "src/ui/ui.cpp", "src/ui/icons.cpp", "src/linux/platform_linux.cpp", "src/linux/hotspot_linux.cpp", "capi/pocketdrop_capi.cpp"]
    flags = subprocess.run([pkg, "--cflags", "--libs", "libcurl", "gdk-pixbuf-2.0"], capture_output=True, text=True, check=True).stdout.split()
    result = subprocess.run([gxx, "-std=c++20", "-O1", "-fPIC", "-shared", "-fvisibility=hidden", "-pthread", *sources, "-o", str(out), *flags],
                            cwd=base, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    return out


@pytest.fixture
def engine_model(qapp, photos, native_lib, tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_POCKETDROP_LIB", str(native_lib))
    settings = QSettings(str(tmp_path / "pd.ini"), QSettings.Format.IniFormat)
    model = PocketDropModel(photos, parent=qapp, settings=settings)
    assert model.available and not model.sampleMode, model.reason
    model.start()
    pump(qapp, 0.4)
    yield model
    model.shutdown()


def test_engine_state_has_the_same_fields_as_the_sample(qapp, photos, engine_model):
    sample = PocketDropModel(photos, memory=True, parent=qapp)
    sample.start()
    assert set(engine_model.st) == set(sample.st)
    assert set(engine_model.st["info"]) == set(sample.st["info"]) and set(engine_model.st["menu"]) == set(sample.st["menu"])


def test_engine_shows_a_real_qr_and_shares_files(qapp, engine_model, photos, tmp_path):
    st = engine_model.st
    assert st["mode"] == 0 and st["card"] == "qr" and st["qrSize"] >= 21 and st["qrPath"].startswith("M")
    assert st["hasLink"] and st["linkText"].count("/") >= 1
    target = tmp_path / "share.txt"
    target.write_text("hello")
    engine_model.addUrls([QUrl.fromLocalFile(str(target))])
    pump(qapp, 0.8)
    rows = engine_model.rows
    assert [r["title"] for r in rows] == ["share.txt"] and rows[0]["path"] == str(target)
    assert engine_model.st["info"]["kind"] == "code"
    engine_model.removeRow(rows[0]["key"])
    pump(qapp, 0.5)
    assert engine_model.rows == []


def test_engine_library_group_hides_its_files_and_removes_them_again(qapp, engine_model, photos):
    engine_model.refreshLibrary(-1)
    engine_model.addLibrary("win")                           # A1.NEF (+ A1.JPG) and B1.jpg
    pump(qapp, 0.8)
    rows = engine_model.rows
    assert [r["kind"] for r in rows] == ["coll"] and rows[0]["title"] == "Winners"
    import json
    from urllib.parse import urlsplit
    url = 'http://' + engine_model.st['linkText']
    _, headers, _ = local_request(url, 'GET', urlsplit(url).path)
    manifest = json.loads(local_request(url, 'GET', headers['Location'] + 'api/manifest')[2])
    assert len(manifest['files']) == 3  # the engine was handed all three files
    engine_model.removeRow(rows[0]["key"])
    pump(qapp, 0.6)
    assert engine_model.rows == [] and not engine_model.st["sharing"]


def test_engine_anywhere_needs_the_warning_answered_then_reports_the_tunnel(qapp, engine_model):
    engine_model.setMode(1)
    assert engine_model.riskPending and engine_model.st["mode"] == 0
    engine_model.confirmRisk(True, False)
    pump(qapp, 0.4)
    st = engine_model.st
    assert st["mode"] == 1
    assert st["card"] in ("missing", "busy", "downloading", "failed", "qr")        # depends on whether cloudflared is installed here
    engine_model.setMode(0)
    assert engine_model.st["mode"] == 0


def test_engine_copy_and_menu_commands(qapp, engine_model):
    engine_model.command("copy")
    pump(qapp, 0.2)
    assert engine_model.st["copied"] is True
    assert QGuiApplication.clipboard().text().startswith("http://")
    burn = engine_model.st["menu"]["burn"]
    engine_model.command("menu", "burn")
    assert engine_model.st["menu"]["burn"] is (not burn)
    old = engine_model.st["linkText"]
    engine_model.command("new_link")
    assert engine_model.st["linkText"] != old

# Real HTTP requests to this test process's native server (no public tunnel).
def local_request(url, method, path, body=None):
    import http.client
    from urllib.parse import urlsplit
    target = urlsplit(url)
    connection = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
    try:
        connection.request(method, path, body=body)
        reply = connection.getresponse()
        return reply.status, dict(reply.getheaders()), reply.read()
    finally:
        connection.close()


def test_engine_local_uploads_require_code_and_new_link_revokes_sessions(qapp, engine_model, tmp_path):
    import json
    from urllib.parse import urlsplit
    url = 'http://' + engine_model.st['linkText'].removeprefix('http://')
    status, headers, _ = local_request(url, 'GET', urlsplit(url).path)
    assert status == 302
    session = headers['Location']
    engine_model.command('set_inbox', str(tmp_path))
    assert local_request(url, 'POST', session + 'text', b'unauthorized test')[0] == 401
    assert local_request(url, 'POST', session + 'up?name=test.txt&size=1')[0] == 401
    for method in ('PUT', 'GET', 'DELETE'):
        assert local_request(url, method, session + 'up/unknown')[0] == 401
    assert not list(tmp_path.glob('*.pdpart'))
    assert local_request(url, 'POST', session + 'auth', b'WRONG')[0] == 401
    manifest = json.loads(local_request(url, 'GET', session + 'api/manifest')[2])
    assert manifest['receiveAuth'] and not manifest['receiveOk']
    code = engine_model.st['info']['code']
    assert code and code.encode() not in json.dumps(manifest).encode()
    assert local_request(url, 'POST', session + 'auth', code.encode())[0] == 204
    assert local_request(url, 'POST', session + 'text', b'authorized test')[0] == 204
    status, _, body = local_request(url, 'POST', session + 'up?name=test.txt&size=5')
    assert status == 200
    upload = session + 'up/' + json.loads(body)['id']
    assert local_request(url, 'PUT', upload + '?offset=0', b'hello')[0] == 200
    assert (tmp_path / 'test.txt').read_bytes() == b'hello'
    assert not list(tmp_path.glob('*.pdpart'))
    engine_model.command('new_link'); pump(qapp, .2)
    assert local_request(url, 'GET', session + 'api/manifest')[0] == 404
    newer = 'http://' + engine_model.st['linkText'].removeprefix('http://')
    status, headers, _ = local_request(newer, 'GET', urlsplit(newer).path)
    assert status == 302
    assert local_request(newer, 'POST', headers['Location'] + 'auth', code.encode())[0] == 401
    assert engine_model.st['info']['code'] != code


def test_engine_local_listener_does_not_accept_on_loopback(engine_model):
    import socket
    from urllib.parse import urlsplit
    url = 'http://' + engine_model.st['linkText'].removeprefix('http://')
    endpoint = urlsplit(url)
    assert endpoint.hostname != '127.0.0.1'
    assert local_request(url, 'GET', '/ping')[0] == 204
    with pytest.raises(OSError):
        with socket.create_connection(('127.0.0.1', endpoint.port), timeout=1):
            pass


def test_engine_mode_changes_rebind_and_revoke_local_sessions(qapp, engine_model, monkeypatch, tmp_path):
    """Exercise Anywhere's listener without creating a public tunnel."""
    import ctypes
    import socket
    from urllib.parse import urlsplit
    if sys.platform != 'win32':
        pytest.skip('Windows helper search isolation')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setenv('PATH', os.environ['SystemRoot'] + '/System32')
    # SearchPath includes the executable/current/system directories, beyond PATH.
    found = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.kernel32.SearchPathW(None, 'cloudflared.exe', None, len(found), found, None):
        pytest.skip('helper present; keep this test local-only')
    url = 'http://' + engine_model.st['linkText']
    endpoint = urlsplit(url)
    _, headers, _ = local_request(url, 'GET', endpoint.path)
    session = headers['Location']
    code = engine_model.st['info']['code']
    assert local_request(url, 'POST', session + 'auth', code.encode())[0] == 204
    engine_model.command('menu', 'auto_any')
    assert not engine_model.st['menu']['tunnelOpen']
    engine_model.command('card')
    assert not engine_model.st['menu']['tunnelOpen']
    engine_model.setMode(1)
    engine_model.confirmRisk(True, False)
    pump(qapp, .4)
    assert engine_model.st['mode'] == 1 and engine_model.st['card'] == 'missing'
    loopback = f'http://127.0.0.1:{endpoint.port}'
    assert local_request(loopback, 'GET', '/ping')[0] == 204
    assert local_request(loopback, 'GET', session + 'api/manifest')[0] == 404
    with pytest.raises(OSError):
        with socket.create_connection((endpoint.hostname, endpoint.port), timeout=1):
            pass
    engine_model.setMode(0)
    pump(qapp, .3)
    assert not engine_model.st['menu']['tunnelOpen']
    newer = 'http://' + engine_model.st['linkText']
    assert engine_model.st['info']['code'] and engine_model.st['info']['code'] != code
    assert local_request(newer, 'GET', session + 'api/manifest')[0] == 404
    _, headers, _ = local_request(newer, 'GET', urlsplit(newer).path)
    assert local_request(newer, 'POST', headers['Location'] + 'auth', code.encode())[0] == 401
    test_engine_local_listener_does_not_accept_on_loopback(engine_model)


def test_engine_selected_address_rebinds(qapp, engine_model):
    import socket
    from urllib.parse import urlsplit
    addresses = engine_model.st['menu']['addrs']
    if len(addresses) < 2:
        pytest.skip('needs two local IPv4 addresses')
    old = urlsplit('http://' + engine_model.st['linkText'])
    code = engine_model.st['info']['code']
    index = next(i for i, address in enumerate(addresses) if address['ip'] != old.hostname)
    engine_model.command('addr', str(index))
    pump(qapp, .2)
    url = 'http://' + engine_model.st['linkText']
    new = urlsplit(url)
    assert new.hostname == addresses[index]['ip']
    assert engine_model.st['info']['code'] != code
    assert local_request(url, 'GET', '/ping')[0] == 204
    with pytest.raises(OSError):
        with socket.create_connection((old.hostname, old.port), timeout=1):
            pass


# ---- phone uploads into the open folder -------------------------------------------------------------------------------------

def test_receive_into_open_folder_switches_the_inbox_and_puts_it_back(qapp, photos, engine_model, tmp_path):
    original = engine_model.st["menu"]["inbox"]
    assert not engine_model.receiveIntoFolder
    engine_model.setReceiveIntoFolder(True)
    pump(qapp, 0.2)
    assert os.path.normcase(engine_model.st["menu"]["inbox"]) == os.path.normcase(os.path.normpath(str(tmp_path)))
    other = tmp_path / "second"
    other.mkdir()
    photos.setRows([], str(other))
    photos.folderChanged.emit()
    pump(qapp, 0.2)
    assert os.path.normcase(engine_model.st["menu"]["inbox"]) == os.path.normcase(os.path.normpath(str(other)))   # follows the open folder
    engine_model.setReceiveIntoFolder(False)
    pump(qapp, 0.2)
    assert engine_model.st["menu"]["inbox"] == original                                                              # the person's own folder returns


def test_a_file_that_lands_in_the_open_folder_refreshes_the_grid(qapp, photos, engine_model, tmp_path):
    reloads = []
    engine_model._reload_timer.timeout.disconnect()
    engine_model._reload_timer.timeout.connect(lambda: reloads.append(1))
    engine_model._seen_received.clear()
    elsewhere = tmp_path.parent / "elsewhere" / "x.jpg"
    engine_model._notice_received([{"tone": "recv", "path": str(elsewhere)}])
    pump(qapp, 0.7)
    assert not reloads                                                      # a file saved somewhere else changes nothing
    engine_model._notice_received([{"tone": "recv", "path": str(tmp_path / "from-phone.jpg")}])
    pump(qapp, 0.7)
    assert reloads == [1]
    engine_model._notice_received([{"tone": "recv", "path": str(tmp_path / "from-phone.jpg")}])
    pump(qapp, 0.7)
    assert reloads == [1]                                                   # the same file is not announced twice


def test_menu_state_carries_history_phones_and_notes(qapp, photos, engine_model):
    menu = engine_model.st["menu"]
    assert menu["history"] == [] and menu["phones"] == 0 and menu["noteClip"] is False
    engine_model.command("menu", "note_clip")
    assert engine_model.st["menu"]["noteClip"] is True
    engine_model.command("menu", "note_clip")
    assert engine_model.st["menu"]["noteClip"] is False
