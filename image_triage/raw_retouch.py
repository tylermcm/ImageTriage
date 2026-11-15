"""Bounded non-destructive stroke contract shared with PhotoCraft CameraRaw."""
import math

def patch_valid(patch):
    """The picture a ``patch`` stroke drops in: an absolute file path and the part of the frame it covers."""
    if not isinstance(patch,dict) or set(patch)!={"path","region"}:return False
    path,region=patch["path"],patch["region"]
    if not isinstance(path,str) or not 1<=len(path)<=512 or not (path[1:3] in (":\\",":/") or path.startswith("/")):return False
    if not isinstance(region,list) or len(region)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in region):return False
    return region[0]<region[2] and region[1]<region[3]


def valid(strokes):
    if not isinstance(strokes,list) or len(strokes)>128:return False
    total=0
    def point(p):return isinstance(p,list) and len(p)==2 and all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in p)
    for s in strokes:
        if not isinstance(s,dict) or set(s)-{"kind","points","source","size","feather","opacity","patch"}:return False
        if s.get("kind") not in ("remove","heal","spot","clone","patch"):return False
        if (s["kind"]=="patch")!=("patch" in s) or ("patch" in s and not patch_valid(s["patch"])):return False
        points=s.get("points")
        if not isinstance(points,list) or not 1<=len(points)<=4096 or not all(point(p) for p in points):return False
        total+=len(points)
        if total>32768:return False
        for key,low,high in (("size",.001,.5),("feather",0,100),("opacity",0,100)):
            v=s.get(key)
            if type(v) not in (int,float) or not math.isfinite(v) or not low<=v<=high:return False
        if s.get("source") is not None and not point(s["source"]):return False
        if s["kind"] in ("clone","heal") and s.get("source") is None:return False
    return True


def fits(stroke,width,height):
    if stroke.get("kind")=="patch":
        x0,y0,x1,y1=stroke["patch"]["region"]
        return max(1,round((x1-x0)*width))*max(1,round((y1-y0)*height))<=16*1024*1024
    radius=stroke["size"]*min(width,height)/2
    margin=max(radius*4,32) if stroke["kind"] in ("remove","spot") else 3
    xs,ys=zip(*stroke["points"])
    w=min(width,math.ceil(max(xs)*width+radius+margin))-max(0,math.floor(min(xs)*width-radius-margin))
    h=min(height,math.ceil(max(ys)*height+radius+margin))-max(0,math.floor(min(ys)*height-radius-margin))
    return w*h<=16*1024*1024
