"""QML authoring for PhotoCraft's native local-mask recipe. No pixel adjustments here."""
from copy import deepcopy
import base64
import math
import json

from PySide6.QtCore import QObject, Property, Signal, Slot

from ..camera_raw_recipe_masks import masks_valid, LOCAL_DEFAULTS, LOCAL_SCALARS, LOCAL_CURVES, LOCAL_ARRAYS, LOCAL_WHEELS, local_adjust_valid

ADJUST = tuple(LOCAL_SCALARS)


def encode_raster(array):
    """Run-length recipe encoding: a coverage byte, then its run as a little-endian base-128 number."""
    import numpy as np
    array = np.asarray(array, dtype=np.uint8)
    values = array.ravel()
    starts = np.r_[0, np.flatnonzero(values[1:] != values[:-1])+1]
    runs = np.diff(np.r_[starts, len(values)]).astype(np.int64)
    # Bytes each run number needs (a raster holds at most 1536 x 1536 pixels, so four is enough).
    digits = 1 + (runs >= 1 << 7) + (runs >= 1 << 14) + (runs >= 1 << 21)
    offsets = np.r_[0, np.cumsum(digits+1)[:-1]]
    out = np.zeros(int((digits+1).sum()), dtype=np.uint8)
    out[offsets] = values[starts]
    for place in range(4):
        used = digits > place
        out[offsets[used]+1+place] = ((runs[used] >> (7*place)) & 127) | ((digits[used] > place+1) << 7)
    h, w = array.shape
    return {"type": "raster", "w": w, "h": h, "data": base64.b64encode(out.tobytes()).decode("ascii")}


def decode_raster(shape):
    import numpy as np
    if not masks_valid([{"shape": shape}]):
        raise ValueError("Invalid raster mask")
    result = np.empty(shape["w"]*shape["h"], dtype=np.uint8)
    raw, index, target = base64.b64decode(shape["data"]), 0, 0
    while index < len(raw):
        value = raw[index]; index += 1
        run = shift = 0
        while True:
            byte = raw[index]; index += 1
            run |= (byte & 127) << shift
            if byte < 128:
                break
            shift += 7
        result[target:target+run] = value
        target += run
    return result.reshape(shape["h"], shape["w"])


def _coverage_key(mask):
    return json.dumps([mask.get("shape"),mask.get("invert",False),mask.get("parts",[])],sort_keys=True)


def _mask_coverage(mask,w,h):
    """Coverage-only UI thumbnails. Pixel adjustments remain native PhotoCraft."""
    import numpy as np
    from PIL import Image
    xx,yy=np.meshgrid((np.arange(w)+.5)/w,(np.arange(h)+.5)/h)
    def shape_map(shape):
        kind=shape.get("type")
        if kind=="raster":
            image=Image.fromarray(decode_raster(shape)).resize((w,h),Image.Resampling.BILINEAR)
            return np.asarray(image,dtype=np.float32)/255
        if kind=="linear":
            vx,vy=(shape["x1"]-shape["x0"])*w,(shape["y1"]-shape["y0"])*h
            return 1-np.clip(((xx-shape["x0"])*w*vx+(yy-shape["y0"])*h*vy)/max(1e-6,vx*vx+vy*vy),0,1)
        if kind=="radial":
            angle=math.radians(shape["angle"]);dx,dy=(xx-shape["cx"])*w,(yy-shape["cy"])*h
            u=(dx*math.cos(angle)+dy*math.sin(angle))/max(1e-6,shape["rx"]*w)
            v=(-dx*math.sin(angle)+dy*math.cos(angle))/max(1e-6,shape["ry"]*h)
            t=np.clip((np.sqrt(u*u+v*v)-(1-shape["feather"]/100))/max(1e-4,shape["feather"]/100),0,1)
            return 1-t*t*(3-2*t)
        return None
    value=shape_map(mask.get("shape",{}))
    if value is None:return None
    if mask.get("invert",False):value=1-value
    for part in mask.get("parts",[]):
        p=shape_map(part.get("shape",{}))
        if p is None:return None
        if part.get("invert",False):p=1-p
        op=part.get("op","add")
        value=np.maximum(value,p) if op=="add" else value*(1-p) if op=="subtract" else value*p
    return value


class RawMasks(QObject):
    changed = Signal()

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._selected = -1
        self._tool = ""
        self._replacing = False
        self._path = ""
        self._size, self._feather, self._erase = 12., 50., False
        self._flow, self._density = 100., 100.
        self._range = 50.
        self._swatch = "#999999"
        self._overlay_key = None
        self._overlay_url = ""
        self._overlay_revision = 0
        self._thumbs={}
        self._native_coverage=None
        self._combine=None
        self._component=-1
        self._brush_part=None
        self._display_mode="color"
        self._display_color="#ff4040"
        self._display_opacity=50
        self._auto_overlay=True
        self._auto_hide=False
        self._pins=True
        self._unselected_pins=False
        self._pointer=False
        raw.changed.connect(self._sync)
        from .raw_mask_ai import RawMaskAI
        self._ai = RawMaskAI(self)

    ai = Property(QObject, lambda self: self._ai, constant=True)
    overlayMode=Property(str,lambda self:self._display_mode,notify=changed)
    overlayColor=Property(str,lambda self:self._display_color,notify=changed)
    overlayOpacity=Property(int,lambda self:self._display_opacity,notify=changed)
    autoOverlay=Property(bool,lambda self:self._auto_overlay,notify=changed)
    autoHide=Property(bool,lambda self:self._auto_hide,notify=changed)
    showPins=Property(bool,lambda self:self._pins,notify=changed)
    unselectedPins=Property(bool,lambda self:self._unselected_pins,notify=changed)
    pointerOnImage=Property(bool,lambda self:self._pointer,notify=changed)
    component=Property(int,lambda self:self._component,notify=changed)
    combining=Property(str,lambda self:self._combine[1] if self._combine else "",notify=changed)
    adjusting=Property(bool,lambda self:self.raw._gesture,notify=changed)

    @Property("QVariantList",notify=changed)
    def pins(self):
        result=[]
        for index,mask in enumerate(self.rows):
            shape=mask.get("shape",{});kind=shape.get("type")
            x,y=(shape.get("cx",.5),shape.get("cy",.5)) if kind=="radial" else ((shape.get("x0",.5)+shape.get("x1",.5))/2,(shape.get("y0",.5)+shape.get("y1",.5))/2)
            result.append({"index":index,"x":x,"y":y})
        return result

    @Slot(str,str)
    def displayOption(self,key,value):
        from ..mask_display import OVERLAY_DISPLAY_MODES
        from PySide6.QtGui import QColor
        if key=="mode" and value in OVERLAY_DISPLAY_MODES:self._display_mode=value
        elif key=="color" and QColor(value).isValid():self._display_color=QColor(value).name()
        elif key=="opacity":
            try:self._display_opacity=max(0,min(100,int(value)))
            except ValueError:return
        elif key in ("autoOverlay","autoHide","showPins","unselectedPins"):
            setattr(self,{"autoOverlay":"_auto_overlay","autoHide":"_auto_hide","showPins":"_pins","unselectedPins":"_unselected_pins"}[key],value=="true")
        else:return
        self._overlay_key=None;self.changed.emit()

    @Slot(bool)
    def pointer(self,value):
        if value!=self._pointer:self._pointer=value;self.changed.emit()

    @Property("QVariantList",notify=changed)
    def components(self):
        mask=self.current
        if not mask:return []
        names={"raster":"Coverage","linear":"Linear Gradient","radial":"Radial Gradient","colorRange":"Color Range","luminanceRange":"Luminance Range"}
        return [{"label":names.get(p.get("shape",{}).get("type"),"Coverage"),"op":p.get("op","base"),"invert":p.get("invert",False)} for p in [mask,*mask.get("parts",[])]]

    @Slot(int)
    def selectComponent(self,index):
        if 0<=index<len(self.components):self._component=index;self.changed.emit()

    @Slot(str,str)
    def combineWith(self,op,kind):
        if op not in ("add","subtract","intersect") or self._selected<0:return
        if len(self.current.get("parts",[]))>=8:
            self.raw._error="A mask can have up to nine components.";self.raw.changed.emit();return
        target=self._selected
        self.stop();self._combine=(target,op)
        if kind in ("subject","sky","background","scene","people","object"):self.ai.start(kind)
        else:self.arm(kind)
        self.changed.emit()

    @Slot(str)
    def componentAction(self,action):
        rows=self.rows
        if not 0<=self._selected<len(rows):return
        mask=rows[self._selected];index=self._component
        if not 0<=index<=len(mask.get("parts",[])):return
        target=mask if index==0 else mask["parts"][index-1]
        if action=="invert":target["invert"]=not target.get("invert",False)
        elif action in ("add","subtract","intersect") and index>0:target["op"]=action
        elif action=="duplicate" and len(mask.get("parts",[]))<8:
            part={"op":"add","shape":deepcopy(target["shape"]),"invert":target.get("invert",False)}
            mask.setdefault("parts",[]).append(part)
        elif action=="delete":
            if index==0:
                if not mask.get("parts"):self.remove();return
                part=mask["parts"].pop(0);mask["shape"]=part["shape"];mask["invert"]=part.get("invert",False)
            else:mask["parts"].pop(index-1)
            self._component=max(0,index-1)
        elif action in ("up","down") and index>0:
            dest=index-1+(-1 if action=="up" else 1)
            if not 0<=dest<len(mask["parts"]):return
            mask["parts"][index-1],mask["parts"][dest]=mask["parts"][dest],mask["parts"][index-1]
            self._component=dest+1
        else:return
        self._commit(rows,"Mask component "+action)

    @Slot()
    def removeEmpty(self):
        import numpy as np
        rows=[]
        for mask in self.rows:
            shape=mask.get("shape",{})
            # Only delete provably empty coverage; a thumbnail can miss tiny areas.
            empty=shape.get("type")=="raster" and not mask.get("parts") and not mask.get("invert",False) and not np.any(decode_raster(shape))
            if not empty:rows.append(mask)
        self._commit(rows,"Delete empty masks")

    @Slot(bool)
    def duplicateMask(self,invert):
        if not self.current or len(self.rows)>=16:return
        if invert and self.current.get("parts"):return
        rows=self.rows;item=deepcopy(rows[self._selected])
        item["name"]=(item.get("name","Mask")+" copy")[:64]
        if invert:item["invert"]=not item.get("invert",False)
        rows.append(item);self._selected=len(rows)-1
        self._commit(rows,"Duplicate and invert mask" if invert else "Duplicate mask")

    @Slot()
    def removeAll(self):
        self.stop();self._selected=-1;self._commit([],"Delete all masks")

    def _sync(self):
        if self._path != self.raw._path:
            self._path = self.raw._path
            self._selected, self._tool = -1, ""
            self._combine=None;self._component=-1
            self._replacing = False
            self._overlay_key = None
            self._thumbs={}
            self._native_coverage=None
        if self._selected >= len(self.rows):
            self._selected = len(self.rows)-1
        self.changed.emit()

    rows = Property("QVariantList", lambda self: deepcopy(self.raw._draft.get("masks", [])), notify=changed)
    selected = Property(int, lambda self: self._selected, notify=changed)
    tool = Property(str, lambda self: self._tool, notify=changed)
    brushSize = Property(float, lambda self: self._size, notify=changed)
    brushFeather = Property(float, lambda self: self._feather, notify=changed)
    erasing = Property(bool, lambda self: self._erase, notify=changed)
    brushFlow = Property(float, lambda self: self._flow, notify=changed)
    brushDensity = Property(float, lambda self: self._density, notify=changed)
    sampleSwatch = Property(str, lambda self: self._swatch, notify=changed)

    @Property("QVariantMap", notify=changed)
    def current(self):
        rows = self.rows
        if not 0 <= self._selected < len(rows):
            return {}
        mask = {"enabled": True, "invert": False, "amount": 100, **rows[self._selected]}
        mask["adjust"] = {**deepcopy(LOCAL_DEFAULTS), **mask.get("adjust", {})}
        for key in LOCAL_WHEELS:
            mask["adjust"][key]={**LOCAL_DEFAULTS[key],**mask["adjust"][key]}
        return mask

    @Slot(int,result=str)
    def thumbnail(self,index):
        from PySide6.QtGui import QImage
        from .raw_preview import put_thumbnail
        rows=self.rows
        if not 0<=index<len(rows):return ""
        mask=rows[index]
        key=json.dumps([mask.get("shape"),mask.get("invert",False),mask.get("parts",[])],sort_keys=True)
        if index in self._thumbs and self._thumbs[index][0]==key:return self._thumbs[index][1]
        w,h=self.raw._full_size
        scale=120/max(1,w,h);w,h=max(1,round(w*scale)),max(1,round(h*scale))
        coverage=_mask_coverage(mask,w,h)
        if coverage is None:
            if not self._native_coverage or self._native_coverage[0]!=_coverage_key(mask):return ""
            native=self._native_coverage[1].convertToFormat(QImage.Format_RGBA8888)
            import numpy as np
            from PIL import Image
            rgba=np.frombuffer(native.constBits(),dtype=np.uint8).reshape(native.height(),native.bytesPerLine())[:,:native.width()*4].reshape(native.height(),native.width(),4)
            values=np.clip(rgba[:,:,3].astype(np.float32)/90*255,0,255).astype(np.uint8)
            coverage=np.asarray(Image.fromarray(values).resize((w,h),Image.Resampling.BILINEAR),dtype=np.float32)/255
        import numpy as np
        pixels=np.rint(coverage*255).astype(np.uint8)
        image=QImage(pixels.data,w,h,w,QImage.Format_Grayscale8).copy()
        self._overlay_revision+=1
        url=put_thumbnail(f"{self.raw._owner}/{index}/{self._overlay_revision}",image)
        self._thumbs[index]=(key,url)
        return url

    def acceptCoverage(self,image,mask):
        if _coverage_key(mask)!=_coverage_key(self.current):return
        self._native_coverage=(_coverage_key(mask),image)
        self._overlay_key=None;self._thumbs={}
        self.changed.emit()

    @Property(str, notify=changed)
    def overlay(self):
        from PySide6.QtGui import QImage, QColor
        from . import raw_preview
        from ..raw_crop import bounds
        from ..mask_display import compose_mask_overlay
        import numpy as np
        mask=self.current
        if not mask:return ""
        crop=self.raw.values["hostCrop"]
        key=(_coverage_key(mask),tuple(crop.values()),self._display_mode,self._display_color,self._display_opacity,self.raw._preview)
        if key!=self._overlay_key:
            native=self._native_coverage
            if native and native[0]==_coverage_key(mask):
                image=native[1].convertToFormat(QImage.Format_RGBA8888)
                pixels=np.frombuffer(image.constBits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
                coverage=np.rint(pixels[:,:,3].astype(np.float32)/90*255).clip(0,255).astype(np.uint8)
            else:
                w,h=self.raw._full_size
                if min(w,h)<=0:return ""
                scale=min(1,1000/max(w,h));w,h=max(1,round(w*scale)),max(1,round(h*scale))
                values=_mask_coverage(mask,w,h)
                if values is None:return ""
                coverage=np.rint(values*255).astype(np.uint8)
            h,w=coverage.shape
            strength=QImage(coverage.data,w,h,w,QImage.Format_Grayscale8).copy()
            base=raw_preview.frame_image(self.raw._preview) if self.raw._preview.startswith("image://") else QImage(self.raw._preview)
            color=QColor(self._display_color);color.setAlphaF(self._display_opacity/100)
            image=compose_mask_overlay(strength,self._display_mode,color,base)
            image=image.copy(*bounds(crop,image.width(),image.height()))
            raw_preview.discard_frame(self._overlay_url);self._overlay_revision+=1
            self._overlay_url=raw_preview.put_frame(f"{self.raw._owner}/mask/{self._overlay_revision}",image)
            self._overlay_key=key
        return self._overlay_url

    @Slot(int)
    def select(self, index):
        if 0 <= index < len(self.rows):
            self._selected, self._tool = index, ""
            self._component=-1;self._combine=None;self._brush_part=None
            self._replacing = False
            self.changed.emit()
            if self.current.get("shape",{}).get("type") in ("colorRange","luminanceRange") and not self.raw._memory:
                self.raw._enqueue("preview")

    @Slot(str)
    def arm(self, kind):
        if not self.raw.loaded or self.raw.saving:
            return
        self._replacing = False
        self._brush_part=None
        if kind in ("brush", "linear", "radial"):
            self._tool, self._erase = kind, False
            if kind != "brush" or self.current.get("shape", {}).get("type") != "raster":
                self._selected = -1
            self.changed.emit()
        elif kind == "luminanceRange":
            self._append({"type": kind, "low": .25, "high": .75, "smooth": 25}, "Luminance range")
            self._tool = kind
            self.changed.emit()
        elif kind == "colorRange":
            self._tool, self._selected = kind, -1
            if self._combine:self._selected=self._combine[0]
        self.changed.emit()

    @Slot()
    def createNew(self):
        self.stop()
        self._selected = -1
        self.changed.emit()

    @Slot()
    def sampleAgain(self):
        kind = self.current.get("shape",{}).get("type")
        if kind in ("colorRange","luminanceRange"):
            self._tool = kind
            self.changed.emit()

    @Slot(float,float)
    def brushDynamics(self, flow, density):
        if all(math.isfinite(v) for v in (flow,density)):
            self._flow,self._density = max(1,min(100,flow)),max(1,min(100,density))
            self.changed.emit()

    @Slot()
    def stop(self):
        self._tool = ""
        self._combine=None
        self._brush_part=None
        self._replacing = False
        self._ai.stopDrawing()
        self.changed.emit()

    @Slot()
    def redraw(self):
        kind = self.current.get("shape", {}).get("type")
        if kind in ("linear", "radial"):
            self._tool, self._replacing = kind, True
            self.changed.emit()

    @Slot()
    def newBrush(self):
        self._selected = -1
        self.arm("brush")

    @Slot(float, float, bool)
    def brushOptions(self, size, feather, erase):
        if math.isfinite(size) and math.isfinite(feather):
            self._size, self._feather, self._erase = max(1, min(100, size)), max(0, min(100, feather)), erase
            self.changed.emit()

    def _commit(self, rows, label):
        if masks_valid(rows):
            self.raw._set_values({"masks": rows}, label)

    def _append(self, shape, name, invert=False,parts=None):
        rows = self.rows
        if self._combine:
            target,op=self._combine
            if not 0<=target<len(rows) or len(rows[target].get("parts",[]))>=8:return False
            if parts:
                import numpy as np
                w,h=self.raw._full_size;scale=min(1,768/max(1,w,h))
                values=_mask_coverage({"shape":shape,"invert":invert,"parts":parts},max(1,round(w*scale)),max(1,round(h*scale)))
                if values is None:return False
                shape=encode_raster(np.rint(values*255).astype(np.uint8));invert=False;parts=None
            rows[target].setdefault("parts",[]).append({"op":op,"shape":deepcopy(shape),"invert":invert})
            if parts:
                for part in parts:
                    if len(rows[target]["parts"])>=8:break
                    rows[target]["parts"].append(deepcopy(part))
            self._selected=target;self._component=len(rows[target]["parts"])
            self._brush_part=(target,self._component-1) if shape.get("type")=="raster" else None
            self._combine=None;self._tool=""
            self._commit(rows,"Combine mask "+op)
            return True
        if self._replacing and 0 <= self._selected < len(rows):
            rows[self._selected]["shape"] = shape
            self._tool, self._replacing = "", False
            self._commit(rows, "Redraw mask")
            return True
        if len(rows) >= 16:
            self.raw._error = "A photo can have up to 16 masks."
            self.raw.changed.emit()
            return False
        self._selected = len(rows)
        self._tool = ""
        rows.append({"name": f"{name[:58]} {len(rows)+1}", "enabled": True, "invert": invert,
                     "amount": 100, "shape": shape, "adjust": {}})
        if parts:rows[-1]["parts"]=parts
        self._commit(rows, "Create mask")
        return True

    def _source_point(self, x, y):
        c = self.raw.values["hostCrop"]
        return c["x"]+max(0, min(1, x))*c["w"], c["y"]+max(0, min(1, y))*c["h"]

    @Slot(float, float, float, float)
    def draw(self, x0, y0, x1, y1):
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
            return
        x0, y0 = self._source_point(x0, y0)
        x1, y1 = self._source_point(x1, y1)
        if abs(x1-x0)+abs(y1-y0) < .003:
            return
        if self._tool == "linear":
            self._append({"type": "linear", "x0": x0, "y0": y0, "x1": x1, "y1": y1}, "Linear gradient")
        elif self._tool == "radial":
            self._append({"type": "radial", "cx": (x0+x1)/2, "cy": (y0+y1)/2,
                          "rx": max(.001, abs(x1-x0)/2), "ry": max(.001, abs(y1-y0)/2),
                          "angle": 0, "feather": self._feather}, "Radial gradient")

    @Slot("QVariantList")
    def stroke(self, points):
        import numpy as np
        if self._tool != "brush" or not points or len(points) > 4096:
            return
        if any(not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p) for p in points):
            return
        points = [self._source_point(*p) for p in points]
        rows = self.rows
        current = self.current
        part=self._brush_part
        if part and (part[0]!=self._selected or part[1]>=len(current.get("parts",[]))):part=self._brush_part=None
        brush_shape=current["parts"][part[1]]["shape"] if part else current.get("shape",{})
        existing = not self._combine and brush_shape.get("type") == "raster"
        if existing:
            raster = decode_raster(brush_shape)
        else:
            w, h = self.raw._full_size
            if min(w, h) <= 0:
                self.raw._error = "Wait for the RAW preview before painting."
                self.raw.changed.emit()
                return
            scale = 768/max(1, w, h)
            raster = np.zeros((max(1, round(h*scale)), max(1, round(w*scale))), dtype=np.uint8)
        h, w = raster.shape
        radius = max(1, min(w, h)*self._size/200)
        last = points[0]
        for point in points:
            length = math.hypot((point[0]-last[0])*w, (point[1]-last[1])*h)
            steps = max(1, math.ceil(length/max(1, radius/3)))
            for t in np.linspace(0, 1, steps+1):
                cx, cy = (last[0]+(point[0]-last[0])*t)*w, (last[1]+(point[1]-last[1])*t)*h
                left, top = max(0, int(cx-radius)), max(0, int(cy-radius))
                right, bottom = min(w, math.ceil(cx+radius+1)), min(h, math.ceil(cy+radius+1))
                yy, xx = np.mgrid[top:bottom, left:right]
                d = np.sqrt((xx+.5-cx)**2+(yy+.5-cy)**2)/radius
                feather = max(.001, self._feather/100)
                coverage = np.clip((1-d)/feather, 0, 1)
                coverage = coverage*coverage*(3-2*coverage)
                region = raster[top:bottom, left:right]
                ink = coverage*self._flow/100
                raster[top:bottom, left:right] = (region*(1-ink*self._density/100)).astype(np.uint8) if self._erase else np.maximum(region,np.minimum(round(self._density*255/100),region+(255-region)*ink)).astype(np.uint8)
            last = point
        shape = encode_raster(raster)
        if existing:
            if part:rows[self._selected]["parts"][part[1]]["shape"]=shape
            else:rows[self._selected]["shape"] = shape
            self._commit(rows, "Brush stroke")
        elif not self._erase:
            self._append(shape, "Brush")
        self._tool = "brush"
        self.changed.emit()

    @Slot(str, float)
    def adjust(self, key, value):
        if not math.isfinite(value) or key not in (*ADJUST, "amount", "low", "high", "smooth", "feather", "range"):
            return
        rows = self.rows
        if not 0 <= self._selected < len(rows):
            return
        mask = rows[self._selected]
        if key in ADJUST:
            low,high=LOCAL_SCALARS[key]
            mask.setdefault("adjust", {})[key] = max(low,min(high,value))
        elif key == "amount":
            mask[key] = max(0, min(100, value))
        else:
            shape = mask.get("shape", {})
            if key not in shape:
                return
            shape[key] = max(0, min(1 if key in ("low", "high") else 100, value))
            if key == "low": shape[key] = min(shape[key], shape["high"])
            if key == "high": shape[key] = max(shape[key], shape["low"])
        self._commit(rows, "Mask " + key)

    def _set_local(self,key,value,label):
        rows=self.rows
        if not 0<=self._selected<len(rows) or self.raw.saving:return
        adjust=rows[self._selected].setdefault("adjust",{})
        candidate={**adjust,key:deepcopy(value)}
        if local_adjust_valid(candidate):
            rows[self._selected]["adjust"]=candidate
            self._commit(rows,label)

    @Slot(str,"QVariantList")
    def setCurve(self,channel,points):
        if channel in LOCAL_CURVES:self._set_local(channel,points,"Mask tone curve")

    @Slot(int,str,float)
    def setMixer(self,channel,component,value):
        if component not in ("hslHue","hslSat","hslLum") or channel not in range(-1,8) or not math.isfinite(value):return
        values=list(self.current.get("adjust",{}).get(component,LOCAL_DEFAULTS[component]))
        value=max(-100,min(100,value))
        if channel==-1:values=[value]*8
        else:values[channel]=value
        self._set_local(component,values,"Mask color mixer")

    @Slot(str,float,float,float)
    def setGrading(self,wheel,hue,saturation,luminance):
        if wheel not in LOCAL_WHEELS or not all(math.isfinite(v) for v in (hue,saturation,luminance)):return
        self._set_local(wheel,{"hue":hue%360,"sat":max(0,min(100,saturation)),"lum":max(-100,min(100,luminance))},"Mask color grading")

    @Slot(int,str,float)
    def setCalibration(self,channel,component,value):
        if component not in ("calibHue","calibSat") or channel not in range(3) or not math.isfinite(value):return
        values=list(self.current.get("adjust",{}).get(component,LOCAL_DEFAULTS[component]))
        values[channel]=max(-100,min(100,value))
        self._set_local(component,values,"Mask calibration")

    @Slot(float,float,float,float,bool)
    def sampleRange(self,x0,y0,x1,y1,add):
        from .raw_preview import executable
        if self._tool not in ("colorRange","luminanceRange") or not executable():
            self.raw._error = "Range sampling needs the updated RAW preview helper."
            self.raw.changed.emit();return
        if not all(math.isfinite(v) for v in (x0,y0,x1,y1)):return
        a,b = self._source_point(x0,y0),self._source_point(x1,y1)
        self.raw._enqueue("maskSample",{"area":[*a,*b],"kind":self._tool,"add":add,"target":self._selected})
        self.raw.changed.emit()

    def applySample(self,result,sample):
        rgb = result["sampleRGB"]
        if len(rgb)!=3 or any(not math.isfinite(v) for v in rgb):raise ValueError("Invalid sampled color")
        kind = sample["kind"]
        if kind == "colorRange":
            shape = {"type":kind,"color":list(rgb),"range":self._range,"smooth":50}
            display=result.get("sampleDisplay",rgb)
            self._swatch = "#"+"".join(f"{max(0,min(255,round(v*255))):02x}" for v in display)
        else:
            low,high = result["sampleLuminance"]
            if abs(high-low)<.005: low,high=max(0,low-.05),min(1,high+.05)
            shape={"type":kind,"low":low,"high":high,"smooth":25}
        rows=self.rows;target=sample["target"]
        if self._combine:
            self._append(shape,"Range component")
        elif 0<=target<len(rows) and rows[target].get("shape",{}).get("type")==kind:
            if kind=="colorRange" and sample["add"]:
                if len(rows[target].get("parts",[]))>=8:
                    raise ValueError("A mask can have up to nine sampled colors.")
                rows[target].setdefault("parts",[]).append({"op":"add","shape":shape,"invert":False})
            else:rows[target]["shape"]=shape
            self._selected=target
            self._commit(rows,"Sample range")
        else:self._append(shape,"Color range" if kind=="colorRange" else "Luminance range")
        self._tool=kind
        self.changed.emit()

    @Slot(str)
    def toggle(self, key):
        rows = self.rows
        if 0 <= self._selected < len(rows) and key in ("enabled", "invert"):
            rows[self._selected][key] = not self.current[key]
            self._commit(rows, "Mask " + key)

    @Slot(str)
    def rename(self, name):
        rows = self.rows
        if 0 <= self._selected < len(rows):
            rows[self._selected]["name"] = name.strip()[:64] or "Mask"
            self._commit(rows, "Rename mask")

    @Slot()
    def remove(self):
        rows = self.rows
        if 0 <= self._selected < len(rows):
            rows.pop(self._selected)
            self._selected, self._tool = min(self._selected, len(rows)-1), ""
            self._commit(rows, "Delete mask")

    @Slot()
    def duplicate(self):
        if self.current and len(self.rows) < 16:
            rows = self.rows
            rows.append(deepcopy(rows[self._selected]))
            rows[-1]["name"] = (rows[-1].get("name", "Mask") + " copy")[:64]
            self._selected = len(rows)-1
            self._commit(rows, "Duplicate mask")

    @Slot()
    def resetAdjustments(self):
        rows = self.rows
        if 0 <= self._selected < len(rows):
            rows[self._selected]["adjust"] = {}
            rows[self._selected]["amount"] = 100
            self._commit(rows, "Reset mask adjustments")
