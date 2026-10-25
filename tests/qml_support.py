"""Context objects the QML shell's windows need besides the photo model, built the way app.py builds them (sample data, no disk)."""
from image_triage.qml_shell.bestof_model import BestOfModel
from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.file_actions import FileActions
from image_triage.qml_shell.menu_model import MenuModel, ToastModel
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from image_triage.qml_shell.people_model import PeopleModel
from image_triage.qml_shell.ai_scores import AiScores
from image_triage.qml_shell.histograms import HistogramService
from image_triage.qml_shell.inspection import InspectionService
from image_triage.qml_shell.library_nav import LibraryNavModel
from image_triage.qml_shell.popout import PopoutHost
from image_triage.qml_shell.reference_model import ReferenceModel
from PySide6.QtCore import QObject, Signal


class NoThumbs(QObject):
    """The thumbnail service as the popout sees it, with nothing cached and nothing made."""
    ready = Signal(str)

    def cached_file(self, path, bucket):
        return ""

    def url(self, path, bucket):
        return ""

    def unwant(self, path):
        pass


def collection_models(photos, qapp):
    """(name, object) pairs: collections, people, best-of, PocketDrop (which lists the collections) and the popout."""
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    popout = PopoutHost(photos, NoThumbs(parent=qapp), collections, prebuild=False, parent=qapp)
    cull = CullModel(photos, memory=True, parent=qapp)
    toasts = ToastModel(parent=qapp)
    files = FileActions(photos, toasts, memory=True, parent=qapp)
    return (("reference", ReferenceModel(photos, toasts, memory=True, parent=qapp)), ("collections", collections), ("people", PeopleModel(photos, memory=True, parent=qapp)), ("bestOf", BestOfModel(photos, parent=qapp)), ("cull", cull), ("toasts", toasts), ("fileActions", files), ("ctxMenu", MenuModel(photos, collections, cull, toasts, memory=True, parent=qapp, files=files)),
            ("pocketDrop", PocketDropModel(photos, memory=True, parent=qapp, collections=collections)), ("popout", popout),
            ("libraryNav", LibraryNavModel(photos, memory=True, parent=qapp)), ("histograms", HistogramService(loader=lambda path: None, parent=qapp)),
            ("inspection", InspectionService(loader=lambda path: None, parent=qapp)), ("aiScores", AiScores(photos, memory=True, parent=qapp)))
