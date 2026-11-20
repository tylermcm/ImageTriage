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
    // ---- the brush, as Camera Raw's: the cursor shows Size (outer circle) and Feather (inner circle: the full-strength part, half
    // the size at 100); right-drag sideways sizes it, Shift+right-drag sets its feather; Alt erases while held; Caps Lock leaves
    // crosshairs only (nothing else changes).
    property bool caps: false
    property bool strokeErase: false        // the stroke being painted erases (Alt held, or the Erase button)
    property bool resizing: false           // a right-drag is sizing the brush (or its feather, with Shift)
    property bool resizeFeather: false
    property point resizeAnchor: Qt.point(0, 0)
    property real resizeFromX: 0
    property real resizeFromSize: 0
    property real resizeFromFeather: 0
    readonly property real brushPixels: Math.min(width / Math.max(crop.w, 0.001), height / Math.max(crop.h, 0.001)) / 100   // view pixels per Size unit (of the diameter)
    readonly property bool brushArmed: model.tool === "brush"
    function syncKeys(modifiers) {
        if (!brushArmed) return
        model.setAltErase((modifiers & Qt.AltModifier) !== 0)
        caps = model.capsLock()
    }
    Keys.onPressed: (event) => {
        if (!overlay.brushArmed) return
        if (event.key === Qt.Key_Alt) { overlay.model.setAltErase(true); event.accepted = true }
        else if (event.key === Qt.Key_CapsLock) overlay.caps = overlay.model.capsLock()
    }
    Keys.onReleased: (event) => {
        if (!overlay.brushArmed) return
        if (event.key === Qt.Key_Alt) { overlay.model.setAltErase(false); event.accepted = true }
        else if (event.key === Qt.Key_CapsLock) overlay.caps = overlay.model.capsLock()
    }
    onBrushArmedChanged: { if (!brushArmed) model.setAltErase(false); else caps = model.capsLock() }
    readonly property var current: model.current
    readonly property var crop: raw.values.hostCrop
    function toView(x,y) { return Qt.point((x-crop.x)/crop.w*width,(y-crop.y)/crop.h*height) }
    Connections { target: overlay.model; function onChanged() { guides.requestPaint() } }
    Connections { target: overlay.model.ai; function onChanged() { guides.requestPaint() } }
    onWidthChanged: guides.requestPaint()
    onHeightChanged: guides.requestPaint()
    onVisibleChanged: { if (!visible) { dragging=false; points=[];model.pointer(false) } }
    Keys.onEscapePressed: (event) => { dragging=false; points=[]; model.stop(); guides.requestPaint(); event.accepted=true }
    Image { anchors.fill: parent; visible: overlay.showCoverage && !(overlay.model.autoOverlay && overlay.model.adjusting && !overlay.model.refining) && !overlay.gradientLive; source: overlay.visible ? overlay.model.overlay : ""; fillMode: Image.Stretch; asynchronous: true; cache: false }

    // ---- a linear gradient, as Photoshop draws one: three parallel lines (full effect, halfway, none) across the picture, the
    // stem between them with its handles, and a rotation handle below the last line. Editable whenever the gradient is selected.
    readonly property var gradient: model.gradient
    readonly property bool gradientEditable: model.showPins && !model.tool && gradient.x0 !== undefined
    readonly property real rotateReach: 34          // the rotation handle's distance past the bottom line, in pixels
    property string grab: ""                        // what a drag on the gradient holds (gradientInput)
    // While a handle is dragged the gradient is drawn from the pointer at once (liveA, liveB); the model hears of it at most every
    // 40 ms (and on release), so the photo keeps up without a backlog. A mask that is only this gradient is drawn live too.
    property point liveA: Qt.point(0, 0)
    property point liveB: Qt.point(0, 0)
    readonly property bool gradientLive: grab !== "" && model.components.length === 1
    // The gradient's ends in view pixels: while one is being drawn, the drag; while one is edited, the live shape.
    function gradientEnds() {
        if (overlay.dragging && overlay.model.tool === "linear") return [overlay.start, overlay.end]
        if (overlay.grab !== "") return [overlay.liveA, overlay.liveB]
        if (overlay.gradient.x0 === undefined) return null
        return [Qt.point(overlay.gradient.x0*width, overlay.gradient.y0*height), Qt.point(overlay.gradient.x1*width, overlay.gradient.y1*height)]
    }
    function geometry(a, b) {
        var dx=b.x-a.x, dy=b.y-a.y, len=Math.max(1e-6, Math.hypot(dx,dy))
        var u=Qt.point(dx/len, dy/len)
        return { a: a, b: b, len: len, u: u, n: Qt.point(-u.y, u.x), m: Qt.point((a.x+b.x)/2, (a.y+b.y)/2),
                 r: Qt.point(b.x+u.x*overlay.rotateReach, b.y+u.y*overlay.rotateReach) }
    }
    function drawGradient(ctx, a, b, withHandles, invert) {
        var g=geometry(a,b), far=2*(width+height)
        var fill=ctx.createLinearGradient(a.x,a.y,b.x,b.y)
        fill.addColorStop(0,invert ? "#00ff4d62" : "#55ff4d62")
        fill.addColorStop(1,invert ? "#55ff4d62" : "#00ff4d62")
        if (overlay.dragging || overlay.gradientLive) { ctx.fillStyle=fill; ctx.fillRect(0,0,width,height) }
        function line(p) {                                   // a line through p, across the picture
            ctx.beginPath(); ctx.moveTo(p.x-g.n.x*far, p.y-g.n.y*far); ctx.lineTo(p.x+g.n.x*far, p.y+g.n.y*far); ctx.stroke()
        }
        ctx.save()
        ctx.lineWidth=1.25; ctx.strokeStyle="rgba(0,0,0,0.45)"; ctx.setLineDash([])
        ctx.translate(0.6,0.6); line(g.a); line(g.m); line(g.b); ctx.translate(-0.6,-0.6)     // a soft shadow, so the lines read on white
        ctx.strokeStyle="white"; line(g.a); line(g.m); line(g.b)
        if (withHandles) {
            ctx.beginPath(); ctx.moveTo(g.a.x,g.a.y); ctx.lineTo(g.r.x,g.r.y); ctx.stroke()          // the stem, through all three lines
            ctx.lineWidth=1.5
            ctx.fillStyle=overlay.model.overlayColor
            ctx.beginPath(); ctx.arc(g.a.x,g.a.y,6,0,Math.PI*2); ctx.fill(); ctx.stroke()          // the full-effect end
            ctx.fillStyle="#589fff"
            ctx.save(); ctx.translate(g.m.x,g.m.y); ctx.rotate(Math.atan2(g.u.y,g.u.x))
            ctx.fillRect(-5,-5,10,10); ctx.strokeRect(-5,-5,10,10); ctx.restore()                   // the middle
            ctx.fillStyle="white"
            ctx.beginPath(); ctx.arc(g.b.x,g.b.y,5,0,Math.PI*2); ctx.fill(); ctx.stroke()          // the no-effect end
            ctx.fillStyle="#1b1e23"
            ctx.beginPath(); ctx.arc(g.r.x,g.r.y,4.5,0,Math.PI*2); ctx.fill(); ctx.stroke()         // the rotation handle
        }
        ctx.restore()
    }
    // What a point (view pixels) on the gradient takes hold of; "" when it is on none of it.
    function hitGradient(p) {
        var ends=gradientEnds(); if (!ends) return ""
        var g=geometry(ends[0],ends[1]), near=8
        function dist(q) { return Math.hypot(p.x-q.x, p.y-q.y) }
        if (dist(g.a) <= near+2) return "top"
        if (dist(g.b) <= near+2) return "bottom"
        if (dist(g.r) <= near+2) return "rotate"
        var along=(p.x-g.a.x)*g.u.x+(p.y-g.a.y)*g.u.y, across=Math.abs((p.x-g.a.x)*g.n.x+(p.y-g.a.y)*g.n.y)
        if (across <= 6 && along >= 0 && along <= g.len) return "move"          // the stem between the two ends, the middle square too
        if (across <= 6 && along > g.len && along < g.len+overlay.rotateReach) return ""      // the stem's tail does nothing
        if (Math.abs(along) <= 6) return "topLine"
        if (Math.abs(along-g.len) <= 6) return "bottomLine"
        if (Math.abs(along-g.len/2) <= 6) return "centerLine"
        return ""
    }

    Canvas {
        id: guides
        width: overlay.visible && visible ? overlay.width : 0
        height: overlay.visible && visible ? overlay.height : 0
        visible: overlay.model.showPins && (!!overlay.model.tool || overlay.dragging || overlay.gradientEditable)
        onPaint: {
            if (width < 1 || height < 1) return
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
                ctx.strokeStyle=overlay.strokeErase ? "#88111418" : "#77ff4d62";ctx.lineWidth=overlay.model.brushSize/100*Math.min(width/overlay.crop.w,height/overlay.crop.h)
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
            if (overlay.dragging && kind === "linear") {
                overlay.drawGradient(ctx, overlay.start, overlay.end, true, false); return
            }
            if (!overlay.dragging && overlay.gradientEditable) {
                var ends=overlay.gradientEnds()
                overlay.drawGradient(ctx, ends[0], ends[1], true, overlay.gradient.invert); return
            }
            if(kind!=="radial")return
            var a,b
            if(overlay.dragging) { a=overlay.start;b=overlay.end }
            else { a=overlay.toView(shape.cx-shape.rx,shape.cy-shape.ry);b=overlay.toView(shape.cx+shape.rx,shape.cy+shape.ry) }
            ctx.beginPath()
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
    Canvas {
        id: brushCursor
        objectName: "rawMaskBrushCursor"
        visible: overlay.model.showPins && (input.containsMouse || overlay.resizing) && overlay.brushArmed
        readonly property real outer: Math.min(2*Math.max(overlay.width, overlay.height), overlay.model.brushSize*overlay.brushPixels) / 2
        readonly property real inner: outer * (1 - overlay.model.brushFeather / 200)
        readonly property point centre: overlay.resizing ? overlay.resizeAnchor : Qt.point(input.mouseX, input.mouseY)
        width: overlay.visible && visible ? 2*outer + 16 : 0; height: width
        x: centre.x - width/2; y: centre.y - height/2
        onOuterChanged: requestPaint()
        onInnerChanged: requestPaint()
        Connections { target: overlay; function onCapsChanged() { brushCursor.requestPaint() } }
        Connections { target: overlay.model; function onChanged() { brushCursor.requestPaint() } }
        onPaint: {
            var ctx=getContext("2d"); ctx.reset(); ctx.clearRect(0,0,width,height)
            var c=width/2, R=outer, tick=Math.max(4, Math.min(8, R*0.25)), erasing=overlay.model.erasing
            function shapes(colour, lw) {
                ctx.strokeStyle=colour; ctx.lineWidth=lw
                if (!overlay.caps) {
                    ctx.beginPath(); ctx.arc(c,c,R,0,Math.PI*2); ctx.stroke()
                    if (inner > 0.5 && inner < R - 0.5) { ctx.beginPath(); ctx.arc(c,c,inner,0,Math.PI*2); ctx.stroke() }
                }
                ctx.beginPath()                                   // the ticks where the size circle crosses its axes
                ctx.moveTo(c, c-R-tick/2); ctx.lineTo(c, c-R+tick/2); ctx.moveTo(c, c+R-tick/2); ctx.lineTo(c, c+R+tick/2)
                ctx.moveTo(c-R-tick/2, c); ctx.lineTo(c-R+tick/2, c); ctx.moveTo(c+R-tick/2, c); ctx.lineTo(c+R+tick/2, c)
                ctx.moveTo(c-4, c); ctx.lineTo(c+4, c)            // the centre: + to paint, minus to erase
                if (!erasing) { ctx.moveTo(c, c-4); ctx.lineTo(c, c+4) }
                ctx.stroke()
            }
            shapes("rgba(0,0,0,0.55)", 2.6)                       // a dark edge, so the cursor reads on light and dark
            shapes("white", 1.1)
        }
    }
    Repeater {
        model: overlay.model.showPins && !overlay.model.tool ? overlay.model.pins : []
        Rectangle {
            required property var modelData
            // the selected gradient's own handles stand for its pin (the pin covered the middle and took its drags)
            visible: (modelData.index===overlay.model.selected || overlay.model.unselectedPins) && !(modelData.index===overlay.model.selected && overlay.gradientEditable)
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
        anchors.fill: parent; enabled: !overlay.model.ai.busy
        acceptedButtons: overlay.brushArmed ? (Qt.LeftButton | Qt.RightButton) : overlay.model.tool ? Qt.LeftButton : Qt.NoButton
        preventStealing: true; hoverEnabled: true
        onContainsMouseChanged: {
            overlay.model.pointer(containsMouse)
            if (containsMouse && overlay.brushArmed) { overlay.forceActiveFocus(); overlay.caps = overlay.model.capsLock() }
            else if (!containsMouse && !pressed) overlay.model.setAltErase(false)
        }
        cursorShape: overlay.brushArmed ? Qt.BlankCursor : Qt.CrossCursor
        function point(event) { return Qt.point(Math.max(0,Math.min(width,event.x)),Math.max(0,Math.min(height,event.y))) }
        onPressed: (event) => {
            if(overlay.model.tool === "object") {overlay.model.ai.click(event.x/width,event.y/height,(event.modifiers & Qt.ControlModifier)!==0);return}
            if (overlay.brushArmed && event.button === Qt.RightButton) {        // sizing the brush, or its feather with Shift
                overlay.resizing = true; overlay.resizeFeather = (event.modifiers & Qt.ShiftModifier) !== 0
                overlay.resizeAnchor = Qt.point(event.x, event.y); overlay.resizeFromX = event.x
                overlay.resizeFromSize = overlay.model.brushSize; overlay.resizeFromFeather = overlay.model.brushFeather
                overlay.model.setRefining(true); return
            }
            overlay.strokeErase = overlay.brushArmed && ((event.modifiers & Qt.AltModifier) !== 0 || overlay.model.eraseButton)
            overlay.forceActiveFocus();overlay.dragging=true;overlay.start=point(event);overlay.end=overlay.start
            overlay.points=[[overlay.start.x/width,overlay.start.y/height]];overlay.addSample=(event.modifiers & Qt.ShiftModifier)!==0;guides.requestPaint()
        }
        onPositionChanged: (event) => {
            if (overlay.resizing) {
                var dx = event.x - overlay.resizeFromX
                if (overlay.resizeFeather) overlay.model.setBrushValue("feather", overlay.resizeFromFeather + dx * 0.5)     // right: softer
                else overlay.model.setBrushValue("size", (overlay.resizeFromSize * overlay.brushPixels + 2 * dx) / overlay.brushPixels)   // the edge follows the pointer
                return
            }
            if (!pressed) overlay.syncKeys(event.modifiers)
            if(!pressed || !overlay.dragging)return
            overlay.end=point(event)
            if(overlay.points.length<4096)overlay.points=overlay.points.concat([[overlay.end.x/width,overlay.end.y/height]])
            guides.requestPaint()
        }
        onReleased: (event) => {
            if (overlay.resizing) { overlay.resizing = false; overlay.model.setRefining(false); return }
            if(!overlay.dragging)return
            overlay.dragging=false
            if(overlay.model.tool==="brush")overlay.model.strokeWith(overlay.points, overlay.strokeErase)
            else if(overlay.model.tool === "colorRange" || overlay.model.tool === "luminanceRange") overlay.model.sampleRange(overlay.start.x/width,overlay.start.y/height,overlay.end.x/width,overlay.end.y/height,overlay.addSample)
            else overlay.model.draw(overlay.start.x/width,overlay.start.y/height,overlay.end.x/width,overlay.end.y/height)
            overlay.points=[];guides.requestPaint()
        }
        onCanceled: { if (overlay.resizing) { overlay.resizing=false; overlay.model.setRefining(false) } overlay.dragging=false;overlay.points=[];guides.requestPaint() }
    }
    // Editing a selected linear gradient (the overlay's own input is off while no tool is armed). A press that is not on the
    // gradient passes through to what is under it.
    MouseArea {
        id: gradientInput
        objectName: "rawGradientInput"
        anchors.fill: parent
        enabled: overlay.gradientEditable && !overlay.model.ai.busy && overlay.visible
        hoverEnabled: true; preventStealing: true
        property var a0; property var b0; property point press
        property string hover: ""
        cursorShape: { var h = pressed ? overlay.grab : hover
                       return h === "move" ? Qt.SizeAllCursor : h === "" ? Qt.ArrowCursor : h === "topLine" || h === "bottomLine" ? Qt.SizeVerCursor : Qt.PointingHandCursor }
        onPositionChanged: (event) => {
            if (!pressed) { hover = overlay.hitGradient(Qt.point(event.x, event.y)); return }
            if (overlay.grab === "") return
            var p=Qt.point(event.x, event.y), a=a0, b=b0, g=overlay.geometry(a0, b0)
            var d=Qt.point(p.x-press.x, p.y-press.y)
            function rotateAbout(q, c, ang) {
                var cs=Math.cos(ang), sn=Math.sin(ang), x=q.x-c.x, y=q.y-c.y
                return Qt.point(c.x+x*cs-y*sn, c.y+x*sn+y*cs)
            }
            if (overlay.grab === "top") a=p                                       // rotate and extend about the other end
            else if (overlay.grab === "bottom") b=p
            else if (overlay.grab === "rotate") {                                 // only rotates, about the middle
                var dir=Math.hypot(p.x-g.m.x, p.y-g.m.y) > 1 ? Qt.point((p.x-g.m.x)/Math.hypot(p.x-g.m.x, p.y-g.m.y), (p.y-g.m.y)/Math.hypot(p.x-g.m.x, p.y-g.m.y)) : g.u
                a=Qt.point(g.m.x-dir.x*g.len/2, g.m.y-dir.y*g.len/2); b=Qt.point(g.m.x+dir.x*g.len/2, g.m.y+dir.y*g.len/2)
            } else if (overlay.grab === "centerLine") {                           // rotates about the middle
                var turn=Math.atan2(p.y-g.m.y, p.x-g.m.x)-Math.atan2(press.y-g.m.y, press.x-g.m.x)
                a=rotateAbout(a0, g.m, turn); b=rotateAbout(b0, g.m, turn)
            } else if (overlay.grab === "topLine" || overlay.grab === "bottomLine") {   // that line moves; the other stays; the middle stays halfway
                var shift=d.x*g.u.x+d.y*g.u.y
                if (overlay.grab === "topLine") { shift=Math.min(shift, g.len-6); a=Qt.point(a0.x+g.u.x*shift, a0.y+g.u.y*shift) }
                else { shift=Math.max(shift, 6-g.len); b=Qt.point(b0.x+g.u.x*shift, b0.y+g.u.y*shift) }
            } else if (overlay.grab === "move") { a=Qt.point(a0.x+d.x, a0.y+d.y); b=Qt.point(b0.x+d.x, b0.y+d.y) }
            if (Math.hypot(b.x-a.x, b.y-a.y) < 6) return
            overlay.liveA = a; overlay.liveB = b; guides.requestPaint()
            if (!commitTimer.running) commitTimer.start()
        }
        Timer { id: commitTimer; interval: 40; onTriggered: gradientInput.commit() }
        function commit() {
            overlay.model.reshapeGradient(overlay.liveA.x/overlay.width, overlay.liveA.y/overlay.height, overlay.liveB.x/overlay.width, overlay.liveB.y/overlay.height)
        }
        function finish() {
            if (overlay.grab === "") return
            commitTimer.stop(); commit()
            overlay.grab=""; overlay.model.setRefining(false); overlay.raw.endGesture(); guides.requestPaint()
        }
        onPressed: (event) => {
            var hit=overlay.hitGradient(Qt.point(event.x, event.y))
            if (hit === "") { event.accepted=false; return }
            var ends=overlay.gradientEnds()
            a0=ends[0]; b0=ends[1]; press=Qt.point(event.x, event.y)
            overlay.liveA=a0; overlay.liveB=b0; overlay.grab=hit
            overlay.model.setRefining(true); overlay.raw.beginGesture()
        }
        onReleased: finish()
        onCanceled: finish()
    }
}
