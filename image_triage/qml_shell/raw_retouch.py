"""QML stroke authoring; native PhotoCraft performs every pixel operation."""
from copy import deepcopy
from PySide6.QtCore import QObject,Property,Signal,Slot
from ..raw_retouch import valid,fits

class RawRetouch(QObject):
    changed=Signal()
    def __init__(self,raw):
        super().__init__(raw);self.raw=raw;self._kind="remove";self._size=3.;self._feather=50.;self._opacity=100.;self._source=None;self._path=""
        raw.changed.connect(self._sync)
    def _sync(self):
        if self._path!=self.raw._path:self._path=self.raw._path;self._source=None
        self.changed.emit()
    kind=Property(str,lambda self:self._kind,notify=changed)
    size=Property(float,lambda self:self._size,notify=changed)
    feather=Property(float,lambda self:self._feather,notify=changed)
    opacity=Property(float,lambda self:self._opacity,notify=changed)
    source=Property("QVariantList",lambda self:list(self._source or []),notify=changed)
    rows=Property("QVariantList",lambda self:deepcopy(self.raw._draft.get("retouch",[])),notify=changed)
    @Slot(str)
    def choose(self,kind):
        if kind in ("remove","heal","spot","clone"):self._kind=kind;self.changed.emit()
    @Slot(str,float)
    def option(self,key,value):
        import math
        if key not in ("size","feather","opacity") or not math.isfinite(value):return
        setattr(self,"_"+key,max(.1 if key=="size" else 0,min(50 if key=="size" else 100,value)));self.changed.emit()
    def _point(self,x,y):
        crop=self.raw.values["hostCrop"]
        return [crop["x"]+x*crop["w"],crop["y"]+y*crop["h"]]
    @Slot(float,float)
    def pick(self,x,y):
        import math
        if not all(math.isfinite(v) and 0<=v<=1 for v in (x,y)):return
        self._source=self._point(x,y);self.changed.emit()
    @Slot("QVariantList")
    def stroke(self,points):
        import math
        if not self.raw.loaded or self.raw.saving or not points:return
        if any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in p) for p in points):return
        if self._kind in ("clone","heal") and self._source is None:
            self.raw._error="Alt-click the photo to choose a source first.";self.raw.changed.emit();return
        stroke={"kind":self._kind,"points":[self._point(*p) for p in points],"size":self._size/100,"feather":self._feather,"opacity":self._opacity}
        if self._source is not None and self._kind in ("clone","heal"):stroke["source"]=list(self._source)
        rows=self.rows+[stroke]
        if not valid(rows):self.raw._error="Retouch stroke limit reached. Undo or clear strokes before adding more.";self.raw.changed.emit();return
        if min(self.raw._full_size)<=0:return
        if not fits(stroke,*self.raw._full_size):
            self.raw._error="This stroke covers too large an area. Use smaller, separate strokes.";self.raw.changed.emit();return
        self.raw._set_values({"retouch":rows},self._kind.title()+" stroke")
    @Slot(int)
    def remove(self,index):
        rows=self.rows
        if 0<=index<len(rows):rows.pop(index);self.raw._set_values({"retouch":rows},"Delete retouch stroke")
    @Slot()
    def clear(self):
        self.raw._set_values({"retouch":[]},"Clear retouch strokes")
