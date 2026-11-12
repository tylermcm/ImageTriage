"""Async adapter from the QML Mask panel to the existing shared model services."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot, Qt, QTimer
from PySide6.QtGui import QImageReader

from .. import editor_tools, photocraft_bridge as bridge
from .raw_masks import encode_raster
from ..camera_raw_recipe_masks import MAX_RASTER_SIDE

KINDS = {"subject": "subjectMask", "background": "subjectMask", "sky": "sceneMasks",
         "scene": "sceneMasks", "people": "peopleMasks", "object": "clickSelect"}
LABELS = {"subject": "Subject", "background": "Background", "sky": "Sky", "scene": "Scene",
          "people": "People", "object": "Object"}


def read_coverage(path, aspect):
    import numpy as np
    from PIL import Image
    with Image.open(path) as opened:
        if opened.width*opened.height > 16_000_000 or min(opened.size) <= 0:
            raise ValueError("The model returned an invalid mask size")
        if abs(opened.width/opened.height/aspect-1) > .025:
            raise ValueError("The model mask does not match the photo's proportions")
        image = opened.convert("L")
        image.thumbnail((MAX_RASTER_SIDE,MAX_RASTER_SIDE), Image.Resampling.BILINEAR)
        values = np.asarray(image, dtype=np.uint8)
    return encode_raster(values), float((values >= 128).mean())


def run_mask_job(kind, source, root, points, labels, on_progress, cancel):
    """Reuse exactly the model services used by PhotoCraft's host jobs."""
    from ..scene_mask_jobs import run_subject_mask, run_scene_masks, run_people_masks, Cancelled
    from PIL import Image
    with Image.open(source) as image:
        aspect = image.width/image.height
    if cancel.is_set(): raise Cancelled()
    if kind in ("subject", "background"):
        path = root / "subject.png"
        run_subject_mask(source, path, on_progress, cancel)
        shape, coverage = read_coverage(path, aspect)
        if coverage < .0005:
            return []
        return [{"id": kind, "label": LABELS[kind], "shape": shape, "coverage": coverage,
                 "invert": kind == "background"}]
    if kind == "object":
        from ..prompt_masks import ensure_prompt_mask
        result = ensure_prompt_mask(source, points_norm=points, labels=labels, progress_callback=on_progress,
                                    cache_root=root/"sam-cache")
        if cancel.is_set(): raise Cancelled()
        shape, coverage = read_coverage(result.mask_path, aspect)
        return [{"id": "object", "label": "Object", "shape": shape, "coverage": coverage}] if coverage >= .0005 else []
    manifest = root/"manifest.json"
    payload = (run_people_masks if kind == "people" else run_scene_masks)(source, manifest, on_progress, cancel)
    if cancel.is_set(): raise Cancelled()
    results = []
    for item in payload.get("people" if kind == "people" else "categories", []):
        if kind == "sky" and item["id"] != "sky": continue
        path = (root/item["file"]).resolve()
        if path.parent != root.resolve(): raise ValueError("Unexpected model mask path")
        shape, coverage = read_coverage(path, aspect)
        if coverage >= .0005:
            results.append({"id": item["id"], "label": item["label"], "shape": shape, "coverage": coverage})
    return results


class RawMaskAI(QObject):
    changed = Signal()
    _finished = Signal(int, object, str)
    _progress = Signal(int, str)
    _peopleFinished = Signal(int,object,str)

    def __init__(self, masks):
        super().__init__(masks)
        self.masks, self.raw = masks, masks.raw
        self._busy, self._message, self._error = False, "", ""
        self._availability = {}
        self._results = []
        self._ticket = 0
        self._cancel = threading.Event()
        self._path = ""
        self._session = None
        self._points, self._labels = [], []
        self._sam_target = None
        self._kind, self._signature = "", None
        self._runner = run_mask_job
        self._masking_open=False
        self._auto_people=not self.raw._memory
        self._people_ticket=0
        self._people_cancel=threading.Event()
        self._people_scanning=False
        self._people_done=False
        self._people_results=[]
        self._people_message=""
        self._people_signature=None
        self._peopleFinished.connect(self._people_arrived)
        self._options_open=False
        self._scene_results=None
        self._scene_signature=None
        self._finished.connect(self._arrived)
        self._progress.connect(self._report)
        self.raw.changed.connect(self._sync)

    busy = Property(bool, lambda self: self._busy, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    availability = Property("QVariantMap", lambda self: dict(self._availability), notify=changed)
    choices = Property("QVariantList", lambda self: [{k:v for k,v in r.items() if k != "shape"} for r in self._results], notify=changed)
    selectionRequired = Property(bool, lambda self: self._kind in ("scene","people"), notify=changed)
    points = Property("QVariantList", lambda self: [[*p, label] for p,label in zip(self._points,self._labels)], notify=changed)
    optionsOpen = Property(bool,lambda self:self._options_open,notify=changed)
    optionsTitle = Property(str,lambda self:"Select a person" if self._kind=="people" else "Landscape Mask Options",notify=changed)
    peopleCount = Property(int,lambda self:len(self._people_results),notify=changed)
    peopleScanning = Property(bool,lambda self:self._people_scanning,notify=changed)
    peopleMessage = Property(str,lambda self:self._people_message,notify=changed)

    def _sync(self):
        if self._path != self.raw._path or self.raw.saving:
            self._cancel.set()
            self._ticket += 1
            self._path = self.raw._path
            self._busy = False
            self._session = None
            self._results = []
            self._points, self._labels = [], []
            self._sam_target = None
            self._message = self._error = ""
            self._people_cancel.set();self._people_ticket+=1
            self._people_scanning=self._people_done=False
            self._people_results=[];self._people_message=""
            self._scene_results=None;self._options_open=False
            self.changed.emit()
        if self._masking_open:QTimer.singleShot(0,self._maybe_people)

    @Slot()
    def openMasking(self):
        self._masking_open=True
        self.refresh()
        QTimer.singleShot(0,self._maybe_people)

    @Slot()
    def closeMasking(self):
        self._masking_open=False

    def _maybe_people(self):
        if self._people_done and self._people_signature!=bridge.file_signature(self.raw._path):
            self._people_done=False;self._people_results=[]
        if not self._auto_people or not self._masking_open or self._people_scanning or self._people_done or self._busy or not self.raw.loaded or self.raw.saving:return
        reason=self._availability.get("people","Checking models…")
        if reason:
            self._people_message=reason;self.changed.emit();return
        try:image=self._snapshot()
        except ValueError:
            self._people_message="Waiting for the RAW preview…";return
        self._people_ticket+=1;ticket=self._people_ticket
        self._people_cancel=cancel=threading.Event()
        self._people_scanning=True;self._people_message="Detecting people…"
        path=self.raw._path;signature=bridge.file_signature(path);runner=self._runner
        self._people_signature=signature
        self.changed.emit()
        def work():
            from . import mask_people_cache as cache
            result,error=[],""
            try:
                kept=cache.load(path,signature)
                if kept is not None:result=kept
                else:
                    hint=cache.culling_faces(path)
                    if hint:
                        try:self._progress.emit(-ticket,f"Reusing {hint} cached face detections; preparing person masks…")
                        except RuntimeError:pass
                    with tempfile.TemporaryDirectory(prefix="image-triage-people-") as scratch:
                        root=Path(scratch);source=root/"source.png"
                        if not image.save(str(source),"PNG"):raise OSError("Could not prepare the photo")
                        if not cancel.is_set():result=runner("people",source,root,[],[],lambda msg:None,cancel)
                        if not cancel.is_set() and signature==bridge.file_signature(path):cache.save(path,signature,result)
            except Exception as exc:error=str(exc) or "People detection failed"
            try:self._peopleFinished.emit(ticket,result,error)
            except RuntimeError:pass
        threading.Thread(target=work,name="qml-people-detect",daemon=True).start()

    def _people_arrived(self,ticket,results,error):
        if ticket!=self._people_ticket:return
        self._people_scanning=False
        if self._people_cancel.is_set():self.changed.emit();return
        if self._people_signature!=bridge.file_signature(self.raw._path):
            self._people_done=False;self._people_results=[]
            self._people_message="The photo changed. Check for people again."
            self.changed.emit();return
        self._people_done=True
        self._people_results=results[:16] if not error else []
        self._people_message=error or (f"{len(results)} {'person' if len(results)==1 else 'people'} detected" if results else "Unable to select people in this photo.")
        self.changed.emit()

    @Slot()
    def retryPeople(self):
        if self._people_scanning:return
        self._people_done=False;self._people_results=[]
        self.refresh();self._maybe_people()

    @Slot()
    def closeOptions(self):
        self._options_open=False;self.masks.stop();self.changed.emit()

    @Slot("QVariantList")
    def createSelection(self,indices):
        indices=list(dict.fromkeys(int(i) for i in indices if type(i) in (int,float) and int(i)==i and 0<=i<len(self._results)))
        if not indices:return
        if self._kind=="people":
            self.choose(indices[0])
        else:
            if len(indices)>9:
                self._error="Choose up to nine regions for one mask.";self.changed.emit();return
            if self._signature!=bridge.file_signature(self.raw._path):
                self._error="The photo changed. Run Landscape again.";self.changed.emit();return
            items=[self._results[i] for i in indices]
            self.masks._append(deepcopy(items[0]["shape"])," + ".join(i["label"] for i in items),
                               parts=[{"op":"add","shape":deepcopy(i["shape"]),"invert":False} for i in items[1:]])
        self.closeOptions()

    @Slot()
    def refresh(self):
        have = editor_tools.installed_models()
        self._availability = {kind:editor_tools.block_reason(tool,have) for kind,tool in KINDS.items()}
        self.changed.emit()

    def _allowed(self, kind):
        if self._busy or not self.raw.loaded or self.raw.saving or kind not in KINDS:
            return False
        self.refresh()
        reason = self._availability[kind]
        if reason:
            self._error = reason
            self.changed.emit()
            return False
        return True

    def _snapshot(self):
        from .raw_preview import frame_image
        source = self.raw._preview
        image = frame_image(source) if source.startswith("image://") else QImageReader(source).read()
        if image.isNull() or min(self.raw._full_size) <= 0:
            raise ValueError("Wait for the RAW preview before creating an AI mask.")
        return image.scaled(1600,1600,Qt.KeepAspectRatio,Qt.SmoothTransformation)

    @Slot(str)
    def start(self, kind):
        if not self._allowed(kind): return
        signature=bridge.file_signature(self.raw._path)
        if signature!=self._people_signature:
            self._people_done=False;self._people_results=[]
        if signature!=self._scene_signature:self._scene_results=None
        if kind=="people" and self._people_done:
            self._kind=kind;self._results=deepcopy(self._people_results)
            self._signature=self._people_signature
            self._options_open=bool(self._results);self.changed.emit();return
        if kind=="scene" and self._scene_results is not None:
            self._kind=kind;self._results=deepcopy(self._scene_results)
            self._signature=self._scene_signature
            self._options_open=bool(self._results);self.changed.emit();return
        if self._people_scanning:self._people_cancel.set()
        combine=self.masks._combine
        self.masks.stop()
        self.masks._combine=combine
        self._results = []
        try:
            image = self._snapshot()
        except Exception as error:
            self._error = str(error);self.changed.emit();return
        session = {"dir": tempfile.TemporaryDirectory(prefix="image-triage-mask-"), "image": image,
                   "signature": bridge.file_signature(self.raw._path)}
        if kind == "object":
            self._session = session
            self._points, self._labels, self._sam_target = [], [], None
            self.masks._tool = "object"
            self.masks.changed.emit()
            self._error, self._message = "", "Click an object to select it. Ctrl-click excludes an area."
            self.changed.emit()
        else:
            self._launch(kind, session, [], [])

    @Slot(float, float, bool)
    def click(self, x, y, exclude):
        import math
        if self._busy or not self._session or self.masks.tool != "object" or not all(math.isfinite(v) and 0 <= v <= 1 for v in (x,y)):
            return
        if exclude and not self._points:
            self._error = "Click the object first, then Ctrl-click areas to exclude."
            self.changed.emit();return
        if len(self._points) >= 64:
            self._error = "Start a new object selection after 64 clicks."
            self.changed.emit();return
        self._points.append(self.masks._source_point(x,y))
        self._labels.append(0 if exclude else 1)
        self._launch("object",self._session,list(self._points),list(self._labels))

    def _launch(self, kind, session, points, labels):
        self._ticket += 1
        ticket = self._ticket
        self._kind = kind
        self._signature = session["signature"]
        self._cancel = cancel = threading.Event()
        self._busy, self._message, self._error = True, "Preparing "+LABELS[kind].lower()+" mask…", ""
        self.changed.emit()
        runner = self._runner
        def progress(message):
            if not cancel.is_set():
                try: self._progress.emit(ticket,message)
                except RuntimeError: pass
        def work():
            result, error = [], ""
            try:
                root = Path(session["dir"].name)
                source = root/"source.png"
                if not source.exists() and not session["image"].save(str(source),"PNG"):
                    raise OSError("Could not prepare the photo for masking")
                if not cancel.is_set():
                    result = runner(kind,source,root,points,labels,progress,cancel)
            except Exception as exc:
                error = str(exc) or "Mask creation failed"
            try: self._finished.emit(ticket,result,error)
            except RuntimeError: pass
        threading.Thread(target=work,name="qml-mask-ai",daemon=True).start()

    @Slot()
    def cancel(self):
        if self._busy:
            self._cancel.set()
            self._message = "Cancelling after the current model step…"
        else:
            self.masks.stop()
            self._session = None
            self._points, self._labels = [], []
        self.changed.emit()

    def stopDrawing(self):
        if self._kind == "object" and self._busy:
            self._cancel.set()
        self._session = None
        self._points, self._labels = [], []
        self._sam_target = None
        self.changed.emit()

    def _report(self, ticket, message):
        if ticket==-self._people_ticket and self._people_scanning:
            self._people_message=message;self.changed.emit();return
        if ticket == self._ticket and not self._cancel.is_set():
            self._message = message
            self.changed.emit()

    def _arrived(self, ticket, result, error):
        if ticket != self._ticket: return
        self._busy = False
        if self._cancel.is_set():
            self.masks._combine=None
            if self._kind == "object": self.masks.stop()
            self._message = "Cancelled";self.changed.emit();return
        if self._signature != bridge.file_signature(self.raw._path):
            error = "The photo changed while masking. Run the selection again."
        if error:
            self._error, self._message = error, ""
        elif not result:
            self._message = "No "+LABELS[self._kind].lower()+" found. Try another selection."
        else:
            self._results = result
            self._message = "Choose a region to create its mask."
            if self._kind in ("subject","background","sky","object"):
                self.choose(0)
            else:self._options_open=True
            if self._kind=="scene":
                self._scene_results=deepcopy(result);self._scene_signature=self._signature
            if self._kind=="people":
                self._people_results=deepcopy(result);self._people_done=True;self._people_signature=self._signature
        self.changed.emit()
        if self._masking_open:QTimer.singleShot(0,self._maybe_people)

    @Slot(int)
    def choose(self, index):
        if self._busy or not 0 <= index < len(self._results) or not self.raw.active or self.raw.saving: return
        if self._signature != bridge.file_signature(self.raw._path):
            self._results = []
            self._error = "The photo changed. Run the selection again."
            self.changed.emit()
            return
        item = self._results[index]
        shape = deepcopy(item["shape"])
        rows = self.masks.rows
        if self._kind == "object" and self._sam_target:
            target, component, previous = self._sam_target
            row=rows[target] if target<len(rows) else {}
            entry=row if component is None else row.get("parts",[])[component] if component<len(row.get("parts",[])) else {}
            if entry.get("shape") == previous:
                entry["shape"] = shape
                self.masks._selected = target
                self.masks._commit(rows,"Refine object selection")
                self._sam_target = (target,component,shape)
                self._message = "Click to add; Ctrl-click to exclude."
                return
        combining=bool(self.masks._combine)
        if not self.masks._append(shape,item["label"],invert=item.get("invert",False)):
            self._message = "Delete a mask before adding another."
            self.changed.emit()
            return
        if self._kind == "object":
            self._sam_target = (self.masks.selected,self.masks._component-1 if combining else None,shape)
            self.masks._tool = "object"
            self.masks.changed.emit()
            self._message = "Click to add; Ctrl-click to exclude."
        else:
            self._message = item["label"]+" mask added."
        self.changed.emit()
