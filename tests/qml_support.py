"""Context objects the QML shell's windows need besides the photo model, built the way app.py builds them (sample data, no disk)."""
from image_triage.qml_shell.bestof_model import BestOfModel
from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.menu_model import MenuModel, ToastModel
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from image_triage.qml_shell.people_model import PeopleModel
from image_triage.qml_shell.reference_model import ReferenceModel


def collection_models(photos, qapp):
    """(name, object) pairs: collections, people, best-of and PocketDrop (which lists the collections)."""
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    cull = CullModel(photos, memory=True, parent=qapp)
    toasts = ToastModel(parent=qapp)
    return (("reference", ReferenceModel(photos, toasts, memory=True, parent=qapp)), ("collections", collections), ("people", PeopleModel(photos, memory=True, parent=qapp)), ("bestOf", BestOfModel(photos, parent=qapp)), ("cull", cull), ("toasts", toasts), ("ctxMenu", MenuModel(photos, collections, cull, toasts, memory=True, parent=qapp)),
            ("pocketDrop", PocketDropModel(photos, memory=True, parent=qapp, collections=collections)))
