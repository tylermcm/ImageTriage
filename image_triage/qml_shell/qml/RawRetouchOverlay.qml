import QtQuick

Item {
    id: overlay
    required property var model
    required property var raw
    property Item viewport: null
    readonly property var crop: raw.values.hostCrop
    property var points: []
    property bool drawing: false
    readonly property real diameter: model.size/100*Math.min(width/crop.w,height/crop.h)
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
        id: path; anchors.fill: parent
        onPaint: {
            var ctx=getContext('2d');ctx.reset();ctx.clearRect(0,0,width,height)
            if(!overlay.drawing || !overlay.points.length)return
            ctx.strokeStyle='#88589fff';ctx.lineWidth=overlay.diameter;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath()
            for(var i=0;i<overlay.points.length;i++) { var p=overlay.points[i];if(i===0)ctx.moveTo(p[0]*width,p[1]*height);else ctx.lineTo(p[0]*width,p[1]*height) }
            if(overlay.points.length===1) {var a=overlay.points[0];ctx.lineTo(a[0]*width+.01,a[1]*height)}
            ctx.stroke()
        }
    }
    Rectangle { objectName: "rawRetouchCursor"; visible: pointer.enabled && pointer.containsMouse && overlay.acceptsPoint(pointer.mouseX, pointer.mouseY); x: pointer.mouseX-width/2; y: pointer.mouseY-height/2; width: overlay.diameter; height: width; radius: width/2; color:'transparent'; border.color:'white' }
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
            if(event.modifiers & Qt.AltModifier) {overlay.model.pick(p[0],p[1]);return}
            overlay.points=[p];overlay.drawing=true;path.requestPaint()
        }
        onPositionChanged: (event) => {
            if (overlay.drawing && pressed) {
                if (!overlay.acceptsPoint(event.x, event.y)) { overlay.finishStroke(); return }
                if (overlay.points.length < 4096) { overlay.points=overlay.points.concat([point(event)]); path.requestPaint() }
            }
        }
        onReleased: { if (overlay.drawing) overlay.finishStroke() }
        onCanceled: {overlay.drawing=false;overlay.points=[];path.requestPaint()}
    }
}
