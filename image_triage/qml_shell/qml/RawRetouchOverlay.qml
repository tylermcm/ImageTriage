import QtQuick

Item {
    id: overlay
    required property var model
    required property var raw
    property Item viewport: null
    readonly property var crop: raw.values.hostCrop
    property var points: []
    signal zoomWheel(real x, real y, real steps)
    property bool drawing: false
    // The brush on screen, never larger than twice what can be seen (zoomed in, or on a crop of a sliver, it would be enormous, and
    // Qt's software renderer keeps a picture of every rounded rectangle at its full size; see RawRedEyeOverlay).
    readonly property real limit: 2*Math.max(viewport ? viewport.width : width, viewport ? viewport.height : height, 6)
    readonly property real diameter: visible ? Math.min(limit, model.size/100*Math.min(width/Math.max(crop.w, 0.001), height/Math.max(crop.h, 0.001))) : 0
    // The part of the photo that can be seen when a stroke starts: the stroke's canvas covers only that (the view cannot move during a
    // stroke), not the whole zoomed photo, which at 300% of a 24 MP RAW is a canvas of gigabytes.
    property rect strokeArea: Qt.rect(0, 0, 0, 0)
    function seenArea() {
        if (!viewport) return Qt.rect(0, 0, width, height)
        var a = mapFromItem(viewport, 0, 0), b = mapFromItem(viewport, viewport.width, viewport.height)
        var x0 = Math.max(0, Math.min(a.x, b.x)), y0 = Math.max(0, Math.min(a.y, b.y))
        var x1 = Math.min(width, Math.max(a.x, b.x)), y1 = Math.min(height, Math.max(a.y, b.y))
        return Qt.rect(Math.floor(x0), Math.floor(y0), Math.max(0, Math.ceil(x1 - x0)), Math.max(0, Math.ceil(y1 - y0)))
    }
    function toView(p) { return Qt.point((p[0]-crop.x)/crop.w*width,(p[1]-crop.y)/crop.h*height) }
    function acceptsPoint(x, y) {
        if (x < 0 || y < 0 || x >= width || y >= height) return false
        if (!viewport) return true
        var p = mapToItem(viewport, x, y)
        return p.x >= 0 && p.y >= 0 && p.x < viewport.width && p.y < viewport.height
    }
    function finishStroke() {
        var completed = points
        drawing = false; points = []; path.requestPaint()
        if (completed.length) model.stroke(completed)
    }
    onVisibleChanged: { if(!visible) {drawing=false;points=[];path.requestPaint()} }
    Canvas {
        id: path
        objectName: "rawRetouchStroke"
        x: overlay.strokeArea.x; y: overlay.strokeArea.y
        width: overlay.visible && overlay.drawing ? overlay.strokeArea.width : 0
        height: overlay.visible && overlay.drawing ? overlay.strokeArea.height : 0
        onPaint: {
            if (width < 1 || height < 1) return
            var ctx=getContext('2d');ctx.reset();ctx.clearRect(0,0,width,height)
            if(!overlay.drawing || !overlay.points.length)return
            var w=overlay.width, h=overlay.height
            ctx.translate(-x, -y)
            ctx.strokeStyle='#88589fff';ctx.lineWidth=overlay.diameter;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath()
            for(var i=0;i<overlay.points.length;i++) { var p=overlay.points[i];if(i===0)ctx.moveTo(p[0]*w,p[1]*h);else ctx.lineTo(p[0]*w,p[1]*h) }
            if(overlay.points.length===1) {var a=overlay.points[0];ctx.lineTo(a[0]*w+.01,a[1]*h)}
            ctx.stroke()
        }
    }
    Image { anchors.fill: parent; visible: overlay.model.kind==="erase" && source!==""; source: overlay.visible ? overlay.raw.eraseEditor.overlay : ""; fillMode: Image.Stretch; cache: false }
    Rectangle { objectName: "rawRetouchCursor"; visible: overlay.model.kind!=="erase" && pointer.enabled && pointer.containsMouse && overlay.acceptsPoint(pointer.mouseX, pointer.mouseY); x: pointer.mouseX-width/2; y: pointer.mouseY-height/2; width: visible ? overlay.diameter : 0; height: width; radius: width/2; color:'transparent'; border.color:'white' }
    Rectangle {
        visible: overlay.model.source.length===2 && (overlay.model.kind==='clone' || overlay.model.kind==='heal')
        x: visible ? overlay.toView(overlay.model.source).x-6 : 0; y: visible ? overlay.toView(overlay.model.source).y-6 : 0
        width:12;height:12;radius:6;color:'transparent';border.color:theme.accent;border.width:2
    }
    MouseArea {
        id: pointer; objectName:'rawRetouchInput'; anchors.fill: parent
        acceptedButtons:Qt.LeftButton;preventStealing:true;hoverEnabled:true;cursorShape:Qt.CrossCursor
        enabled: overlay.raw.loaded && !overlay.raw.saving && !overlay.raw.busy
        function point(event) {return [Math.max(0,Math.min(1,event.x/width)),Math.max(0,Math.min(1,event.y/height))]}
        onPressed: (event) => {
            if (!overlay.acceptsPoint(event.x, event.y)) { event.accepted = false; return }
            var p=point(event)
            if(overlay.model.kind==="erase") {overlay.raw.eraseEditor.select(p[0],p[1]);return}
            if(event.modifiers & Qt.AltModifier) {overlay.model.pick(p[0],p[1]);return}
            overlay.strokeArea=overlay.seenArea();overlay.points=[p];overlay.drawing=true;path.requestPaint()
        }
        onPositionChanged: (event) => {
            if (overlay.drawing && pressed) {
                if (!overlay.acceptsPoint(event.x, event.y)) { overlay.finishStroke(); return }
                if (overlay.points.length < 4096) { overlay.points=overlay.points.concat([point(event)]); path.requestPaint() }
            }
        }
        onReleased: { if (overlay.drawing) overlay.finishStroke() }
        onCanceled: {overlay.drawing=false;overlay.points=[];path.requestPaint()}
        // Erase only clicks, so the wheel may zoom (the viewer does the zooming)
        WheelHandler {
            target: null; enabled: overlay.model.kind==="erase"
            onWheel: (event) => {
                var steps = event.pixelDelta.y !== 0 ? event.pixelDelta.y / 60 : event.angleDelta.y / 120
                if (steps !== 0) overlay.zoomWheel(event.x, event.y, steps)
                event.accepted = true
            }
        }
    }
}
