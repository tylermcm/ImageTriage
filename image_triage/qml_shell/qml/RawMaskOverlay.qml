import QtQuick

Item {
    id: overlay
    required property var model
    required property var raw
    property bool dragging: false
    property bool showCoverage: true
    property point start: Qt.point(0,0)
    property point end: Qt.point(0,0)
    property var points: []
    property bool addSample: false
    readonly property var current: model.current
    readonly property var crop: raw.values.hostCrop
    function toView(x,y) { return Qt.point((x-crop.x)/crop.w*width,(y-crop.y)/crop.h*height) }
    Connections { target: overlay.model; function onChanged() { guides.requestPaint() } }
    Connections { target: overlay.model.ai; function onChanged() { guides.requestPaint() } }
    onWidthChanged: guides.requestPaint()
    onHeightChanged: guides.requestPaint()
    onVisibleChanged: { if (!visible) { dragging=false; points=[];model.pointer(false) } }
    Keys.onEscapePressed: (event) => { dragging=false; points=[]; model.stop(); guides.requestPaint(); event.accepted=true }
    Image { anchors.fill: parent; visible: overlay.showCoverage && !(overlay.model.autoOverlay && overlay.model.adjusting); source: overlay.visible ? overlay.model.overlay : ""; fillMode: Image.Stretch; asynchronous: true; cache: false }
    Canvas {
        id: guides
        anchors.fill: parent
        visible: overlay.model.showPins && (!!overlay.model.tool || overlay.dragging)
        onPaint: {
            var ctx=getContext("2d");ctx.reset();ctx.clearRect(0,0,width,height)
            var shape=overlay.current.shape || ({})
            ctx.strokeStyle="white";ctx.lineWidth=1.5;ctx.fillStyle="#35ff4d62"
            if(overlay.model.tool === "object") {
                var prompts=overlay.model.ai.points
                for(var n=0;n<prompts.length;n++) {
                    var prompt=prompts[n], pos=overlay.toView(prompt[0],prompt[1])
                    ctx.fillStyle=prompt[2] ? "#589fff" : "#f36565"
                    ctx.beginPath();ctx.arc(pos.x,pos.y,6,0,Math.PI*2);ctx.fill();ctx.stroke()
                    ctx.beginPath();ctx.moveTo(pos.x-3,pos.y);ctx.lineTo(pos.x+3,pos.y)
                    if(prompt[2]) {ctx.moveTo(pos.x,pos.y-3);ctx.lineTo(pos.x,pos.y+3)}
                    ctx.stroke()
                }
                return
            }
            if (overlay.dragging && overlay.model.tool==="brush") {
                ctx.strokeStyle="#77ff4d62";ctx.lineWidth=overlay.model.brushSize/100*Math.min(width/overlay.crop.w,height/overlay.crop.h)
                ctx.lineCap="round";ctx.lineJoin="round";ctx.beginPath()
                for(var i=0;i<overlay.points.length;i++) {var p=overlay.points[i]; if(i===0)ctx.moveTo(p[0]*width,p[1]*height);else ctx.lineTo(p[0]*width,p[1]*height)}
                ctx.stroke();return
            }
            var kind=overlay.dragging ? overlay.model.tool : shape.type
            if(overlay.dragging && (kind==="colorRange" || kind==="luminanceRange")) {
                ctx.setLineDash([4,3]);ctx.fillStyle="#22589fff"
                var left=Math.min(overlay.start.x,overlay.end.x),top=Math.min(overlay.start.y,overlay.end.y)
                var w=Math.abs(overlay.end.x-overlay.start.x),h=Math.abs(overlay.end.y-overlay.start.y)
                ctx.fillRect(left,top,w,h);ctx.strokeRect(left,top,w,h);return
            }
            if(kind!=="linear" && kind!=="radial")return
            var a,b
            if(overlay.dragging) { a=overlay.start;b=overlay.end }
            else if(kind==="linear") { a=overlay.toView(shape.x0,shape.y0);b=overlay.toView(shape.x1,shape.y1) }
            else { a=overlay.toView(shape.cx-shape.rx,shape.cy-shape.ry);b=overlay.toView(shape.cx+shape.rx,shape.cy+shape.ry) }
            ctx.beginPath()
            if(kind==="linear") {
                var gradient=ctx.createLinearGradient(a.x,a.y,b.x,b.y)
                var invert=!overlay.dragging && overlay.current.invert
                gradient.addColorStop(0,invert ? "#00ff4d62" : "#55ff4d62")
                gradient.addColorStop(1,invert ? "#55ff4d62" : "#00ff4d62")
                ctx.fillStyle=gradient;if(overlay.dragging)ctx.fillRect(0,0,width,height);ctx.fillStyle=overlay.model.overlayColor
                ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()
                ctx.beginPath();ctx.arc(a.x,a.y,5,0,Math.PI*2);ctx.fill();ctx.stroke()
                ctx.beginPath();ctx.arc(b.x,b.y,4,0,Math.PI*2);ctx.stroke()
            } else {
                var inverted=!overlay.dragging && overlay.current.invert
                if(inverted && overlay.dragging) {ctx.fillStyle="#55ff4d62";ctx.fillRect(0,0,width,height)}
                var rx=Math.max(1,Math.abs(b.x-a.x)/2), ry=Math.max(1,Math.abs(b.y-a.y)/2)
                var feather=overlay.dragging ? overlay.model.brushFeather : shape.feather
                ctx.save();ctx.translate((a.x+b.x)/2,(a.y+b.y)/2)
                ctx.rotate((overlay.dragging ? 0 : shape.angle || 0)*Math.PI/180);ctx.scale(rx,ry)
                ctx.beginPath();ctx.arc(0,0,1,0,Math.PI*2);ctx.clip();ctx.clearRect(-1,-1,2,2)
                var radial=ctx.createRadialGradient(0,0,Math.min(.999,1-feather/100),0,0,1)
                radial.addColorStop(0,inverted ? "#00ff4d62" : "#55ff4d62")
                radial.addColorStop(1,inverted ? "#55ff4d62" : "#00ff4d62")
                ctx.fillStyle=radial;if(overlay.dragging)ctx.fillRect(-1,-1,2,2)
                ctx.lineWidth=1.5/Math.min(rx,ry);ctx.beginPath();ctx.arc(0,0,1,0,Math.PI*2);ctx.stroke();ctx.restore()
            }
        }
    }
    Rectangle {
        visible: overlay.model.showPins && input.containsMouse && overlay.model.tool === "brush"
        width: overlay.model.brushSize/100*Math.min(overlay.width/overlay.crop.w,overlay.height/overlay.crop.h)
        height: width; radius: width/2
        x: input.mouseX-width/2; y: input.mouseY-height/2
        color: "transparent"; border.color: "white"; border.width: 1
    }
    Repeater {
        model: overlay.model.showPins && !overlay.model.tool ? overlay.model.pins : []
        Rectangle {
            required property var modelData
            visible: modelData.index===overlay.model.selected || overlay.model.unselectedPins
            x: overlay.toView(modelData.x,modelData.y).x-6
            y: overlay.toView(modelData.x,modelData.y).y-6
            width: 12; height: 12; radius: 6; z: 2
            color: modelData.index===overlay.model.selected ? overlay.model.overlayColor : "#4b5058"
            border.color: "white"; border.width: 1
            TapHandler { onTapped: overlay.model.select(parent.modelData.index) }
        }
    }
    MouseArea {
        id: input
        objectName: "rawMaskInput"
        anchors.fill: parent; enabled: !overlay.model.ai.busy; acceptedButtons: overlay.model.tool ? Qt.LeftButton : Qt.NoButton; preventStealing: true; hoverEnabled: true
        onContainsMouseChanged: overlay.model.pointer(containsMouse)
        cursorShape: Qt.CrossCursor
        function point(event) { return Qt.point(Math.max(0,Math.min(width,event.x)),Math.max(0,Math.min(height,event.y))) }
        onPressed: (event) => {
            if(overlay.model.tool === "object") {overlay.model.ai.click(event.x/width,event.y/height,(event.modifiers & Qt.ControlModifier)!==0);return}
            overlay.forceActiveFocus();overlay.dragging=true;overlay.start=point(event);overlay.end=overlay.start
            overlay.points=[[overlay.start.x/width,overlay.start.y/height]];overlay.addSample=(event.modifiers & Qt.ShiftModifier)!==0;guides.requestPaint()
        }
        onPositionChanged: (event) => {
            if(!pressed || !overlay.dragging)return
            overlay.end=point(event)
            if(overlay.points.length<4096)overlay.points=overlay.points.concat([[overlay.end.x/width,overlay.end.y/height]])
            guides.requestPaint()
        }
        onReleased: {
            if(!overlay.dragging)return
            overlay.dragging=false
            if(overlay.model.tool==="brush")overlay.model.stroke(overlay.points)
            else if(overlay.model.tool === "colorRange" || overlay.model.tool === "luminanceRange") overlay.model.sampleRange(overlay.start.x/width,overlay.start.y/height,overlay.end.x/width,overlay.end.y/height,overlay.addSample)
            else overlay.model.draw(overlay.start.x/width,overlay.start.y/height,overlay.end.x/width,overlay.end.y/height)
            overlay.points=[];guides.requestPaint()
        }
        onCanceled: { overlay.dragging=false;overlay.points=[];guides.requestPaint() }
    }
}
