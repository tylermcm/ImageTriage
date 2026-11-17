import QtQuick

Item {
    id: crop
    required property var model
    property real cx: 0
    property real cy: 0
    property real cw: 1
    property real ch: 1
    property bool dragging: false
    property string kind: "draw"
    property point origin: Qt.point(0, 0)
    property point lastPoint: Qt.point(0, 0)
    property var startRect: ({x: 0, y: 0, w: 1, h: 1})
    property bool free: false
    // Upright > Guided: drags draw guides on lines that should be vertical or horizontal, instead of a crop
    readonly property bool guiding: model.values.hostCrop.upright === "guided"
    property point guideEnd: Qt.point(0, 0)
    readonly property string preset: model.values.hostCrop.ratio
    readonly property real ratio: preset === "Free" ? 0 : preset === "Original" ? width / Math.max(1, height) : Number(preset.split(":")[0]) / Number(preset.split(":")[1])
    function sync() {
        if (dragging) return
        var r = model.cropView       // the rectangle over the frame as it is shown (after quarter-turns and mirrors)
        cx = r.x; cy = r.y; cw = r.w; ch = r.h
    }
    Component.onCompleted: sync()
    Connections { target: crop.model; function onChanged() { crop.sync() } }
    function updateRect(point, unconstrained) {
        lastPoint = point
        if (kind === "move") {
            cx = Math.max(0, Math.min(1-startRect.w, startRect.x + (point.x-origin.x)/width))
            cy = Math.max(0, Math.min(1-startRect.h, startRect.y + (point.y-origin.y)/height))
            return
        }
        var dx = point.x-origin.x, dy = point.y-origin.y
        var sx = dx < 0 ? -1 : 1, sy = dy < 0 ? -1 : 1
        var w = Math.abs(dx), h = Math.abs(dy)
        if (!unconstrained && ratio > 0) {
            if (w / Math.max(1,h) > ratio) h = w/ratio
            else w = h*ratio
        }
        var maxW = sx < 0 ? origin.x : width-origin.x
        var maxH = sy < 0 ? origin.y : height-origin.y
        var scale = Math.min(1, maxW/Math.max(1,w), maxH/Math.max(1,h))
        w *= scale; h *= scale
        cx = (sx < 0 ? origin.x-w : origin.x)/width
        cy = (sy < 0 ? origin.y-h : origin.y)/height
        cw = w/width; ch = h/height
    }
    Keys.onPressed: (event) => {
        if (event.key === Qt.Key_Control && dragging) { free = true; updateRect(lastPoint, true); event.accepted = true }
        else if (event.key === Qt.Key_Escape && dragging) { dragging = false; sync(); event.accepted = true }
        else event.accepted = false
    }
    Keys.onReleased: (event) => {
        if (event.key === Qt.Key_Control && dragging) { free = false; updateRect(lastPoint, false); event.accepted = true }
        else event.accepted = false
    }
    Rectangle { x: 0; y: 0; width: parent.width; height: box.y; color: "#88000000" }
    Rectangle { x: 0; y: box.y+box.height; width: parent.width; height: Math.max(0,parent.height-y); color: "#88000000" }
    Rectangle { x: 0; y: box.y; width: box.x; height: box.height; color: "#88000000" }
    Rectangle { x: box.x+box.width; y: box.y; width: Math.max(0,parent.width-x); height: box.height; color: "#88000000" }
    Rectangle {
        id: box
        objectName: "rawCropRectangle"
        x: crop.cx*crop.width; y: crop.cy*crop.height; width: crop.cw*crop.width; height: crop.ch*crop.height
        color: "transparent"; border.color: "white"; border.width: 1
        Repeater { model: [1/3, 2/3]
            Item { required property real modelData; anchors.fill: parent
                Rectangle { x: parent.width*parent.modelData; width: 1; height: parent.height; color: "#88ffffff" }
                Rectangle { y: parent.height*parent.modelData; height: 1; width: parent.width; color: "#88ffffff" }
            }
        }
        Repeater { model: [[0,0],[1,0],[0,1],[1,1]]
            Rectangle {
                required property var modelData
                objectName: "rawCropHandle_" + modelData[0] + "_" + modelData[1]
                x: box.width*modelData[0]-width/2
                y: box.height*modelData[1]-height/2
                width: 8; height: 8; color: "white"; border.color: "#333333"
            }
        }
    }
    Repeater {
        model: crop.guiding ? crop.model.uprightGuides : []
        delegate: Item {
            required property var modelData
            anchors.fill: parent
            Rectangle {
                x: modelData[0] * crop.width; y: modelData[1] * crop.height
                width: Math.hypot((modelData[2] - modelData[0]) * crop.width, (modelData[3] - modelData[1]) * crop.height); height: 2
                transformOrigin: Item.Left
                rotation: Math.atan2((modelData[3] - modelData[1]) * crop.height, (modelData[2] - modelData[0]) * crop.width) * 180 / Math.PI
                color: "#ffd84a"
            }
        }
    }
    Rectangle {
        objectName: "rawGuideDraft"
        visible: crop.guiding && crop.dragging
        x: crop.origin.x; y: crop.origin.y; height: 2; transformOrigin: Item.Left; color: "#ffd84a"
        width: Math.hypot(crop.guideEnd.x - crop.origin.x, crop.guideEnd.y - crop.origin.y)
        rotation: Math.atan2(crop.guideEnd.y - crop.origin.y, crop.guideEnd.x - crop.origin.x) * 180 / Math.PI
    }
    MouseArea {
        objectName: "rawGuideInput"
        visible: crop.guiding; enabled: crop.guiding
        anchors.fill: parent; acceptedButtons: Qt.LeftButton; preventStealing: true; cursorShape: Qt.CrossCursor; z: 2
        onPressed: (event) => { crop.origin = Qt.point(event.x, event.y); crop.guideEnd = crop.origin; crop.dragging = true }
        onPositionChanged: (event) => { if (pressed) crop.guideEnd = Qt.point(Math.max(0, Math.min(crop.width, event.x)), Math.max(0, Math.min(crop.height, event.y))) }
        onReleased: {
            crop.dragging = false
            crop.model.addGuide(crop.origin.x / crop.width, crop.origin.y / crop.height, crop.guideEnd.x / crop.width, crop.guideEnd.y / crop.height)
        }
        onCanceled: crop.dragging = false
    }
    MouseArea {
        objectName: "rawCropInput"
        enabled: !crop.guiding
        anchors.fill: parent; anchors.margins: -4; acceptedButtons: Qt.LeftButton; preventStealing: true; hoverEnabled: true
        cursorShape: crop.dragging && crop.kind === "move" ? Qt.ClosedHandCursor : Qt.CrossCursor
        onPressed: (event) => {
            var px = Math.max(0,Math.min(crop.width,event.x-4))
            var py = Math.max(0,Math.min(crop.height,event.y-4))
            crop.forceActiveFocus()
            crop.startRect = {x: crop.cx, y: crop.cy, w: crop.cw, h: crop.ch}
            crop.origin = Qt.point(px,py); crop.lastPoint = crop.origin
            crop.free = (event.modifiers & Qt.ControlModifier) !== 0
            crop.kind = "draw"
            var left = box.x, right = box.x+box.width, top = box.y, bottom = box.y+box.height
            var nearL = Math.abs(px-left) <= 12, nearR = Math.abs(px-right) <= 12
            var nearT = Math.abs(py-top) <= 12, nearB = Math.abs(py-bottom) <= 12
            if ((nearL || nearR) && (nearT || nearB)) {
                crop.kind = "resize"
                crop.origin = Qt.point(nearL ? right : left, nearT ? bottom : top)
            } else if ((crop.cw < .999 || crop.ch < .999) && px > left && px < right && py > top && py < bottom) crop.kind = "move"
            crop.dragging = true
        }
        onPositionChanged: (event) => {
            if (pressed && crop.dragging) crop.updateRect(Qt.point(Math.max(0,Math.min(crop.width,event.x-4)),Math.max(0,Math.min(crop.height,event.y-4))), crop.free || ((event.modifiers & Qt.ControlModifier) !== 0))
        }
        onReleased: {
            if (!crop.dragging) return
            crop.dragging = false
            if (crop.cw*crop.width >= 4 && crop.ch*crop.height >= 4) crop.model.setCropView(crop.cx,crop.cy,crop.cw,crop.ch)
            crop.sync()
        }
        onCanceled: { crop.dragging = false; crop.sync() }
    }
}
