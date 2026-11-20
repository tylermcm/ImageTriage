import QtQuick
import QtQuick.Controls

// Bokeh: a depth-of-field blur laid over the photo by distance. The effect lives in this section only: it is adjusted here, applied
// live, and is not a mask in the Mask tab. Blur is how strong, Focus Distance is where the sharp part lies (tap the picture to focus
// on a place), Sharp Range is how wide that part is, and the shape is the shape of the out-of-focus lights.
Column {
    id: bk
    required property var model
    required property var raw
    property bool advanced: false
    readonly property bool editable: raw.loaded && !raw.saving
    readonly property var values: model.settings
    spacing: 4
    enabled: editable
    Component.onCompleted: { model.refresh(); if (!model.unavailable && raw.loaded) model.prepare() }
    Connections {
        target: bk.raw
        function onChanged() { if (bk.raw.loaded && !bk.model.ready && !bk.model.busy && !bk.model.unavailable && !bk.model.error) bk.model.prepare() }
    }

    component Glyph: Item {
        id: glyph
        property string name: ""
        property string label: ""
        property bool lit: false
        signal activated()
        width: 26; height: 26
        activeFocusOnTab: true
        Rectangle { anchors.fill: parent; radius: 6; color: glyphHover.hovered || glyph.activeFocus ? theme.s3 : "transparent"; border.color: glyph.activeFocus ? theme.accent : "transparent" }
        Icon { anchors.centerIn: parent; size: 15; name: glyph.name; color: glyph.lit ? theme.accentFg : theme.fg2 }
        HoverHandler { id: glyphHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: glyph.activated() }
        Keys.onReturnPressed: glyph.activated()
        Keys.onSpacePressed: glyph.activated()
        ToolTip.text: glyph.label; ToolTip.visible: glyphHover.hovered; ToolTip.delay: 500
        Accessible.role: Accessible.Button; Accessible.name: glyph.label
        Accessible.onPressAction: glyph.activated()
    }

    Item {
        width: parent.width; height: 30
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: bk.model.busy ? bk.model.message : bk.model.unavailable ? "" : "Raise Blur, then tap the picture to focus."
            width: parent.width - 62; elide: Text.ElideRight
            color: theme.fg2; font.pixelSize: 12
        }
        Row {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 4
            Glyph {
                objectName: "bokehShown"; name: "eye"; lit: bk.model.shown
                label: bk.model.shown ? "Hide the blur" : "Show the blur"
                enabled: bk.model.active; opacity: enabled ? 1 : 0.35
                onActivated: bk.model.toggleShown()
            }
            Glyph {
                objectName: "bokehRemove"; name: "undo"; label: "Remove Bokeh"
                enabled: bk.model.active; opacity: enabled ? 1 : 0.35
                onActivated: bk.model.remove()
            }
        }
    }

    Text {
        objectName: "bokehUnavailable"
        visible: !!bk.model.unavailable
        width: parent.width; wrapMode: Text.WordWrap
        text: bk.model.unavailable; color: theme.fg3; font.pixelSize: 11; bottomPadding: 6
    }

    // the shape of the out-of-focus lights
    Row {
        objectName: "bokehShapes"
        width: parent.width; spacing: 5
        Repeater {
            model: bk.model.shapes
            delegate: Item {
                id: tile
                required property string modelData
                required property int index
                objectName: "bokehShape_" + index
                readonly property bool selected: bk.model.selectedShape === index
                width: Math.floor((bk.width - 25) / 6); height: width + 16
                activeFocusOnTab: true
                Rectangle {
                    id: art
                    width: parent.width; height: parent.width; radius: 8; clip: true
                    color: theme.base
                    border.color: tile.selected ? theme.accent : tileHover.hovered ? theme.line : theme.lineSoft; border.width: tile.selected ? 2 : 1
                    Canvas {
                        anchors.fill: parent
                        property color ink: tile.selected ? theme.accentFg : theme.fg2
                        onInkChanged: requestPaint()
                        onWidthChanged: requestPaint()
                        onPaint: {
                            var ctx = getContext("2d"); ctx.reset(); ctx.clearRect(0, 0, width, height)
                            var cx = width / 2, cy = height / 2, r = width * 0.30
                            ctx.fillStyle = ink; ctx.strokeStyle = ink; ctx.lineWidth = 1.5
                            ctx.beginPath()
                            if (tile.index === 0) {                      // ring
                                ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.globalAlpha = 0.25; ctx.fill(); ctx.globalAlpha = 1
                                ctx.lineWidth = 2.5; ctx.stroke(); return
                            } else if (tile.index === 1) {
                                ctx.arc(cx, cy, r, 0, 2 * Math.PI)
                            } else if (tile.index === 2) {
                                ctx.ellipse(cx - r * 1.1, cy - r * 0.72, r * 2.2, r * 1.44)
                            } else if (tile.index === 3) {               // hexagon
                                for (var i = 0; i < 6; i++) { var a = Math.PI / 6 + i * Math.PI / 3; var px = cx + r * 1.05 * Math.cos(a), py = cy + r * 1.05 * Math.sin(a); if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py) }
                                ctx.closePath()
                            } else if (tile.index === 4) {               // heart
                                ctx.moveTo(cx, cy + r * 1.0)
                                ctx.bezierCurveTo(cx - r * 1.5, cy - r * 0.1, cx - r * 0.9, cy - r * 1.1, cx, cy - r * 0.4)
                                ctx.bezierCurveTo(cx + r * 0.9, cy - r * 1.1, cx + r * 1.5, cy - r * 0.1, cx, cy + r * 1.0)
                            } else {                                      // star
                                for (var k = 0; k < 10; k++) { var b = -Math.PI / 2 + k * Math.PI / 5; var rr = k % 2 === 0 ? r * 1.15 : r * 0.5; var sx = cx + rr * Math.cos(b), sy = cy + rr * Math.sin(b); if (k === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy) }
                                ctx.closePath()
                            }
                            ctx.fill()
                        }
                    }
                }
                Text {
                    y: art.height + 2; width: parent.width; horizontalAlignment: Text.AlignHCenter
                    text: tile.modelData; color: tile.selected ? theme.accentFg : theme.fg2; font.pixelSize: 9; elide: Text.ElideRight
                }
                HoverHandler { id: tileHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: bk.model.chooseShape(tile.index) }
                Keys.onReturnPressed: bk.model.chooseShape(index)
                Keys.onSpacePressed: bk.model.chooseShape(index)
                Accessible.role: Accessible.RadioButton; Accessible.name: modelData; Accessible.checked: selected
                Accessible.onPressAction: bk.model.chooseShape(index)
            }
        }
    }

    RawColorSlider {
        objectName: "bokeh_amount"
        editModel: bk.raw; width: parent.width; controlHeight: 32
        label: "Blur"; from: 0; to: 100
        value: bk.values.amount
        enabled: !bk.model.unavailable
        onCommitted: (next) => bk.model.adjust("amount", next)
    }

    // the photo's depth, with the part that stays sharp tinted; tap a place to focus on it
    Rectangle {
        id: picture
        objectName: "bokehPicture"
        readonly property real aspect: bk.raw.originalWidth > 0 ? bk.raw.originalHeight / bk.raw.originalWidth : 2 / 3
        width: aspect > 1 ? Math.round(Math.min(parent.width, 230 / aspect)) : parent.width
        height: Math.round(width * aspect)
        anchors.horizontalCenter: parent.horizontalCenter
        radius: 8; color: theme.base; border.color: theme.line; clip: true
        Image {
            anchors.fill: parent; anchors.margins: 1
            source: bk.model.picture; cache: false; smooth: true
            visible: bk.model.ready
        }
        MouseArea {
            objectName: "bokehPictureFocus"
            anchors.fill: parent
            enabled: bk.editable && bk.model.ready
            cursorShape: Qt.CrossCursor
            onClicked: (mouse) => bk.model.focusAt(mouse.x / picture.width, mouse.y / picture.height)
        }
        Text {
            objectName: "bokehStatus"
            visible: !bk.model.ready
            anchors.centerIn: parent; width: parent.width - 24
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
            color: bk.model.error ? theme.rej : theme.fg3; font.pixelSize: 11
            text: bk.model.error || (bk.model.busy || bk.model.waiting ? bk.model.message : bk.model.unavailable ? "Depth model not installed" : "The focus is shown here.")
        }
        Rectangle {
            objectName: "bokehCreate"
            visible: !bk.model.ready && !bk.model.busy && !bk.model.waiting && !bk.model.unavailable
            anchors.horizontalCenter: parent.horizontalCenter; y: parent.height / 2 + 14
            width: createLabel.implicitWidth + 28; height: 28; radius: 9; color: createHover.hovered ? Qt.lighter(theme.accent, 1.1) : theme.accent
            Text { id: createLabel; anchors.centerIn: parent; text: bk.model.error ? "Try again" : "Read the depth"; color: theme.onAccent; font.pixelSize: 12; font.weight: Font.Medium }
            HoverHandler { id: createHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: bk.model.prepare() }
            Accessible.role: Accessible.Button; Accessible.name: createLabel.text
            Accessible.onPressAction: bk.model.prepare()
        }
        Text {
            objectName: "bokehHint"
            visible: bk.model.ready && bk.values.amount <= 0
            anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 8
            width: parent.width - 24; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
            text: "Raise Blur to blur what is not in focus."; color: "#f4f4f4"; style: Text.Outline; styleColor: "#000000"; font.pixelSize: 11
        }
    }
    AiFixButton { width: parent.width; raw: bk.raw; error: bk.model.error }

    RawColorSlider {
        objectName: "bokeh_focus"
        editModel: bk.raw; width: parent.width; controlHeight: 32
        label: "Focus Distance"; from: 0; to: 100
        value: bk.values.focus
        enabled: !bk.model.unavailable
        onCommitted: (next) => bk.model.adjust("focus", next)
    }

    RawColorSlider {
        objectName: "bokeh_range"
        editModel: bk.raw; width: parent.width; controlHeight: 32
        label: "Sharp Range"; from: 0; to: 100
        value: bk.values.range
        enabled: !bk.model.unavailable
        onCommitted: (next) => bk.model.adjust("range", next)
    }

    Item {
        objectName: "bokehAdvanced"
        width: parent.width; height: 30
        activeFocusOnTab: true
        Text { anchors.verticalCenter: parent.verticalCenter; text: "Advanced"; color: theme.fg2; font.pixelSize: 12 }
        Icon { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; size: 12; name: bk.advanced ? "chevUp" : "chevDown"; color: theme.fg2 }
        TapHandler { onTapped: bk.advanced = !bk.advanced }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Keys.onReturnPressed: bk.advanced = !bk.advanced
        Keys.onSpacePressed: bk.advanced = !bk.advanced
        Accessible.role: Accessible.Button; Accessible.name: "Advanced"
        Accessible.onPressAction: bk.advanced = !bk.advanced
    }
    Column {
        visible: bk.advanced
        width: parent.width; spacing: 4
        RawColorSlider {
            objectName: "bokeh_highlights"
            editModel: bk.raw; width: parent.width; controlHeight: 32
            label: "Highlights"; from: 0; to: 100
            value: bk.values.highlights
            enabled: !bk.model.unavailable
            onCommitted: (next) => bk.model.adjust("highlights", next)
        }
        Text {
            width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 11
            text: "Focus Distance is where the sharp part lies, from near to far; tap the picture to focus on a place. Sharp Range is how wide that part is. Highlights lifts the brightest lights before they are blurred, so they become bright discs in the shape you chose."
        }
    }
}
