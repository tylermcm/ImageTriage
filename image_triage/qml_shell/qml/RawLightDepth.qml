import QtQuick
import QtQuick.Controls

// Light Depth: light a photo near and far apart. The effect lives in this section only: it is adjusted here, applied live,
// and is not a mask in the Mask tab. The depth is chosen by clicking the photo with the dropper (or with the Depth slider, or
// by dragging in the depth picture); the handles for position, size and feather are drawn on the photo while the section is open.
Column {
    id: light
    required property var model
    required property var raw
    property bool advanced: false
    readonly property bool editable: raw.loaded && !raw.saving
    readonly property var values: model.settings
    spacing: 4
    enabled: editable
    Component.onCompleted: { model.setOpen(true); model.refresh(); if (!model.unavailable && raw.loaded) model.prepare() }
    Component.onDestruction: model.setOpen(false)
    Connections {
        target: light.raw
        function onChanged() { if (light.raw.loaded && !light.model.ready && !light.model.busy && !light.model.unavailable && !light.model.error) light.model.prepare() }
    }

    component Glyph: Item {
        id: glyph
        property string path: ""
        property string name: ""
        property string label: ""
        property bool lit: false
        signal activated()
        width: 26; height: 26
        activeFocusOnTab: true
        Rectangle { anchors.fill: parent; radius: 6; color: glyphHover.hovered || glyph.activeFocus ? theme.s3 : "transparent"; border.color: glyph.activeFocus ? theme.accent : "transparent" }
        Icon { anchors.centerIn: parent; size: 15; path: glyph.path; name: glyph.name; color: glyph.lit ? theme.accentFg : theme.fg2 }
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
        Rectangle {
            id: pickButton
            objectName: "lightDepthPick"
            width: 30; height: 30; radius: 8
            color: light.model.picking ? Qt.alpha(theme.accent, 0.22) : pickHover.hovered || activeFocus ? theme.s3 : theme.s2
            border.color: light.model.picking ? Qt.alpha(theme.accent, 0.5) : activeFocus ? theme.accent : theme.lineSoft
            activeFocusOnTab: true
            function toggle() { light.model.setPicking(!light.model.picking) }
            Icon { anchors.centerIn: parent; size: 16; color: light.model.picking ? theme.accentFg : theme.fg2
                   path: "m14 4 6 6m-9-3 6 6M4 16l9-9 4 4-9 9H4zM15 5l2-2 4 4-2 2" }
            HoverHandler { id: pickHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: pickButton.toggle() }
            Keys.onReturnPressed: toggle()
            Keys.onSpacePressed: toggle()
            ToolTip.text: light.model.picking ? "Click the photo where the light should divide near from far. Escape cancels." : "Pick the depth by clicking the photo"
            ToolTip.visible: pickHover.hovered; ToolTip.delay: 500
            Accessible.role: Accessible.Button; Accessible.name: "Pick the depth from the photo"
            Accessible.onPressAction: toggle()
        }
        Text {
            objectName: "lightDepthPickHint"
            anchors.verticalCenter: parent.verticalCenter; anchors.left: pickButton.right; anchors.leftMargin: 8
            width: parent.width - 30 - 8 - 62
            text: light.model.picking ? "Click the photo to pick the depth." : "Pick the depth from the photo."
            color: theme.fg2; font.pixelSize: 12; elide: Text.ElideRight
        }
        Row {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 4
            Glyph {
                objectName: "lightDepthShown"; name: "eye"; lit: light.model.shown
                label: light.model.shown ? "Hide the effect" : "Show the effect"
                enabled: light.model.active; opacity: enabled ? 1 : 0.35
                onActivated: light.model.toggleShown()
            }
            Glyph {
                objectName: "lightDepthRemove"; name: "undo"; label: "Remove Light Depth"
                enabled: light.model.active; opacity: enabled ? 1 : 0.35
                onActivated: light.model.remove()
            }
        }
    }

    Text {
        objectName: "lightDepthUnavailable"
        visible: !!light.model.unavailable
        width: parent.width; wrapMode: Text.WordWrap
        text: light.model.unavailable; color: theme.fg3; font.pixelSize: 11; bottomPadding: 6
    }

    RawColorSlider {
        objectName: "lightDepth_amount"
        editModel: light.raw; width: parent.width; controlHeight: 32
        label: "Amount"; from: 0; to: 100
        value: light.values.amount
        enabled: !light.model.unavailable
        onCommitted: (next) => light.model.adjust("amount", next)
    }
    RawColorSlider {
        objectName: "lightDepth_depth"
        editModel: light.raw; width: parent.width; controlHeight: 32
        label: "Depth"; from: 0; to: 100
        // 0 is the nearest thing in the photo, 100 the farthest: what lies at this depth is left as it is.
        value: (1 - light.values.depth) * 100
        enabled: !light.model.unavailable
        onCommitted: (next) => light.model.adjust("depth", 1 - next / 100)
    }

    // The depth picture is the secondary view: the photo's depth map with what lies at the chosen depth tinted amber. Beside it
    // is the depth scale: far at the top, near at the bottom, with how much of the photo lies at each depth drawn along it. The
    // handle on the scale sets the depth; the two markers either side of it are the feather, the depth range over which the effect
    // comes in, as in Luminar: drag either one. A position on the scale is a real depth, whatever the photo looks like.
    Item {
        id: padArea
        readonly property real aspect: light.raw.originalWidth > 0 ? light.raw.originalHeight / light.raw.originalWidth : 2 / 3
        readonly property real scaleWidth: 56
        readonly property real pictureWidth: Math.max(40, Math.min(parent.width - scaleWidth - 8, aspect > 1 ? 230 / aspect : parent.width))
        width: parent.width; height: Math.round(pictureWidth * aspect)

        Rectangle {
            id: picture
            objectName: "lightDepthPicture"
            width: padArea.pictureWidth; height: parent.height
            radius: 8; color: theme.base; border.color: theme.line
            clip: true
            Image {
                anchors.fill: parent; anchors.margins: 1
                source: light.model.pad; cache: false; smooth: true
                visible: light.model.ready
            }
            Text {
                id: status
                objectName: "lightDepthStatus"
                visible: !light.model.ready
                anchors.centerIn: parent; width: parent.width - 24
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                color: light.model.error ? theme.rej : theme.fg3; font.pixelSize: 11
                text: light.model.error || (light.model.busy || light.model.waiting ? light.model.message : light.model.unavailable ? "Depth model not installed" : "The depth map appears here.")
            }
            // The depth map is read when the section opens; this is for when that did not happen (cancelled, failed, or never started).
            Rectangle {
                objectName: "lightDepthCreate"
                visible: !light.model.ready && !light.model.busy && !light.model.waiting && !light.model.unavailable
                anchors.horizontalCenter: parent.horizontalCenter; y: status.y + status.height + 10
                width: createLabel.implicitWidth + 28; height: 28; radius: 9; color: createHover.hovered ? Qt.lighter(theme.accent, 1.1) : theme.accent
                Text { id: createLabel; anchors.centerIn: parent; text: light.model.error ? "Try again" : "Create depth map"; color: theme.onAccent; font.pixelSize: 12; font.weight: Font.Medium }
                HoverHandler { id: createHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: light.model.prepare() }
                Accessible.role: Accessible.Button; Accessible.name: createLabel.text
                Accessible.onPressAction: light.model.prepare()
            }
            Text {
                objectName: "lightDepthHint"
                visible: light.model.ready && light.values.amount <= 0
                anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 8
                width: parent.width - 24; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                text: "Raise Amount to light the photo."; color: "#f4f4f4"; style: Text.Outline; styleColor: "#000000"; font.pixelSize: 11
            }
        }

        Rectangle {
            id: pad
            objectName: "lightDepthPad"
            x: padArea.width - width; width: padArea.scaleWidth; height: parent.height
            radius: 8; border.color: pad.activeFocus ? theme.accent : theme.line
            clip: true
            activeFocusOnTab: true
            // the same brightness ramp the depth picture uses: far dark, near bright
            gradient: Gradient {
                GradientStop { position: 0; color: "#1f1f1f" }
                GradientStop { position: 1; color: "#9e9e9e" }
            }
            function nudge(delta) { light.model.adjust("depth", light.values.depth + delta); light.model.flush() }
            Keys.onUpPressed: nudge(-0.02)
            Keys.onDownPressed: nudge(0.02)
            Accessible.role: Accessible.Slider
            Accessible.name: "Depth that is left as it is"
            Accessible.description: "Drag up (farther) or down (nearer), or use the arrow keys, to change the depth. Drag the markers either side of it to change the feather."
            Canvas {   // how much of the photo lies at each depth
                id: spread
                objectName: "lightDepthSpread"
                anchors.fill: parent
                visible: light.model.ready
                onPaint: {
                    var ctx = getContext("2d"); ctx.reset(); ctx.clearRect(0, 0, width, height)
                    var bins = light.model.histogram, n = bins.length
                    if (n < 1) return
                    ctx.fillStyle = "#66ffffff"
                    for (var i = 0; i < n; i++) {
                        var y = Math.round(i / n * height), h = Math.max(1, Math.round((i + 1) / n * height) - y - 1)
                        ctx.fillRect(0, y, bins[i] * width * 0.62, h)
                    }
                }
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                Connections { target: light.model; function onChanged() { spread.requestPaint() } }
            }
            Text { x: pad.width - implicitWidth - 5; y: 3; visible: light.model.ready; text: "Far"; color: "#d8d8d8"; font.pixelSize: 9 }
            Text { x: pad.width - implicitWidth - 5; y: pad.height - implicitHeight - 3; visible: light.model.ready; text: "Near"; color: "#202020"; font.pixelSize: 9 }
            Rectangle {   // the feather: the depth range the effect comes in over, either side of the chosen depth
                objectName: "lightDepthFeatherBand"
                visible: light.model.ready
                width: parent.width
                y: Math.max(0, (light.values.depth - light.model.featherSpan) * pad.height)
                height: Math.min(pad.height, (light.values.depth + light.model.featherSpan) * pad.height) - y
                color: "#22ffffff"; border.color: "#66ffffff"; border.width: 1
            }
            MouseArea {
                objectName: "lightDepthPadDrag"
                anchors.fill: parent
                enabled: light.model.ready && light.editable
                preventStealing: true          // the panel scrolls vertically: it must not take a vertical drag away
                cursorShape: Qt.SizeVerCursor
                function put(mouse) { light.model.adjust("depth", Math.max(0, Math.min(1, mouse.y / pad.height))) }
                onPressed: (mouse) => { pad.forceActiveFocus(); light.raw.beginGesture(); put(mouse) }
                onPositionChanged: (mouse) => { if (pressed) put(mouse) }
                onReleased: light.raw.endGesture()
                onCanceled: light.raw.endGesture()
            }
            Item {
                id: handle
                objectName: "lightDepthPadHandle"
                visible: light.model.ready
                width: pad.width; height: 20
                y: light.values.depth * pad.height - height / 2
                Rectangle { x: 0; y: parent.height / 2 - 1; width: parent.width; height: 2; color: "#ffb02e" }   // the depth itself
                Rectangle {
                    x: parent.width - width - 4; y: Math.max(-handle.y, Math.min(pad.height - 20 - handle.y, 0)); width: 20; height: 20; radius: 10     // the grip stays whole at either end
                    color: "#f4f4f4"; border.color: "#202020"; border.width: 1
                    Icon { anchors.centerIn: parent; size: 12; path: "M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4"; color: "#202020" }
                }
            }
            component FeatherMarker: Item {
                id: marker
                property var host: light
                property bool far: true       // the far marker (arrow up) or the near one (arrow down)
                readonly property real depthAt: host.values.depth + (far ? -1 : 1) * host.model.featherSpan
                readonly property bool off: depthAt < 0 || depthAt > 1       // beyond the end of the scale: shown at the end
                width: 26; height: 16
                visible: host.model.ready
                opacity: off ? 0.55 : 1
                x: 4
                y: far ? Math.max(0, Math.min(pad.height - height, depthAt * pad.height - height))
                       : Math.max(0, Math.min(pad.height - height, depthAt * pad.height))
                Rectangle { anchors.fill: parent; radius: 4; color: "#f4f4f4"; border.color: "#202020"; border.width: 1 }
                Text {
                    anchors.centerIn: parent
                    text: marker.far ? "\u25B2" : "\u25BC"; font.pixelSize: 9; color: "#202020"
                }
                Accessible.role: Accessible.Slider; Accessible.name: "Feather, " + (marker.far ? "far side" : "near side")
                MouseArea {
                    anchors.fill: parent; anchors.margins: -4
                    enabled: marker.host.editable
                    preventStealing: true; cursorShape: Qt.SizeVerCursor
                    onPressed: { pad.forceActiveFocus(); marker.host.raw.beginGesture() }
                    onPositionChanged: (mouse) => {
                        if (!pressed) return
                        var p = mapToItem(pad, mouse.x, mouse.y)
                        marker.host.model.setFeatherSpan(Math.abs(p.y / pad.height - marker.host.values.depth))
                    }
                    onReleased: marker.host.raw.endGesture()
                    onCanceled: marker.host.raw.endGesture()
                }
            }
            FeatherMarker { objectName: "lightDepthFeatherFar"; far: true }
            FeatherMarker { objectName: "lightDepthFeatherNear"; far: false }
        }
    }
    Item {
        visible: light.model.busy
        width: parent.width; height: visible ? 24 : 0
        Text { anchors.verticalCenter: parent.verticalCenter; width: parent.width - 60; elide: Text.ElideRight; text: light.model.message; color: theme.fg3; font.pixelSize: 11 }
        Text {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            text: "Cancel"; color: theme.accentFg; font.pixelSize: 11
            TapHandler { onTapped: light.model.cancel() }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            Accessible.role: Accessible.Button; Accessible.name: "Cancel reading depth"
            Accessible.onPressAction: light.model.cancel()
        }
    }
    Text {
        visible: light.model.ready && !!light.model.error
        width: parent.width; wrapMode: Text.WordWrap
        text: light.model.error; color: theme.rej; font.pixelSize: 11
    }

    RawColorSlider {
        objectName: "lightDepth_softness"
        editModel: light.raw; width: parent.width; controlHeight: 32
        label: "Softness"; from: 0; to: 100
        value: light.values.softness
        enabled: !light.model.unavailable
        onCommitted: (next) => light.model.adjust("softness", next)
    }
    RawColorSlider {
        objectName: "lightDepth_warmth"
        editModel: light.raw; width: parent.width; controlHeight: 32
        label: "Warmth"; from: -100; to: 100
        colors: ["#608fc3", "#a7a7a0", "#d3bd64"]
        value: light.values.warmth
        enabled: !light.model.unavailable
        onCommitted: (next) => light.model.adjust("warmth", next)
    }

    Item {
        objectName: "lightDepthAdvanced"
        width: parent.width; height: 30
        activeFocusOnTab: true
        Text { anchors.verticalCenter: parent.verticalCenter; text: "Advanced Settings"; color: theme.fg2; font.pixelSize: 12 }
        Icon { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; size: 12; name: light.advanced ? "chevUp" : "chevDown"; color: theme.fg2 }
        TapHandler { onTapped: light.advanced = !light.advanced }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Keys.onReturnPressed: light.advanced = !light.advanced
        Keys.onSpacePressed: light.advanced = !light.advanced
        Accessible.role: Accessible.Button; Accessible.name: "Advanced Settings"
        Accessible.onPressAction: light.advanced = !light.advanced
    }
    Column {
        visible: light.advanced
        width: parent.width; spacing: 4
        Text { text: "Depth model"; color: theme.fg2; font.pixelSize: 12; topPadding: 2 }
        Row {
            spacing: 6
            Repeater {
                model: light.model.models
                delegate: Rectangle {
                    id: choice
                    required property var modelData
                    required property int index
                    objectName: "lightDepthModel_" + modelData.key
                    readonly property bool selected: light.model.selectedModel === index
                    width: Math.floor((light.width - 12) / 3); height: 26; radius: 7
                    color: selected ? Qt.alpha(theme.accent, 0.22) : choiceHover.hovered && modelData.installed ? theme.s3 : theme.s2
                    border.color: selected ? Qt.alpha(theme.accent, 0.6) : theme.lineSoft
                    opacity: modelData.installed ? 1 : 0.4
                    activeFocusOnTab: modelData.installed
                    Text { anchors.centerIn: parent; text: choice.modelData.label; color: choice.selected ? theme.accentFg : theme.fg; font.pixelSize: 12 }
                    HoverHandler { id: choiceHover; cursorShape: choice.modelData.installed ? Qt.PointingHandCursor : Qt.ArrowCursor }
                    TapHandler { enabled: choice.modelData.installed; onTapped: light.model.setModel(choice.index) }
                    Keys.onReturnPressed: if (modelData.installed) light.model.setModel(index)
                    Keys.onSpacePressed: if (modelData.installed) light.model.setModel(index)
                    ToolTip.text: modelData.installed ? modelData.label + " depth model" : modelData.label + " depth model is not installed"
                    ToolTip.visible: choiceHover.hovered; ToolTip.delay: 500
                    Accessible.role: Accessible.RadioButton; Accessible.name: modelData.label + " depth model"
                    Accessible.checked: selected
                    Accessible.onPressAction: if (modelData.installed) light.model.setModel(index)
                }
            }
        }
        Text {
            objectName: "lightDepthModelInfo"
            visible: text.length > 0
            width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 11
            text: light.model.busy ? light.model.message : light.model.modelInfo
        }
        Repeater {
            model: [
                {key: "brightnessNear", label: "Brightness Near", from: -100, to: 100, colors: null},
                {key: "warmthNear", label: "Warmth Near", from: -100, to: 100, colors: ["#608fc3", "#a7a7a0", "#d3bd64"]},
                {key: "brightnessFar", label: "Brightness Far", from: -100, to: 100, colors: null},
                {key: "warmthFar", label: "Warmth Far", from: -100, to: 100, colors: ["#608fc3", "#a7a7a0", "#d3bd64"]},
                {key: "size", label: "Size", from: 0, to: 100, colors: null},
                {key: "feather", label: "Feather", from: 0, to: 100, colors: null}
            ]
            delegate: RawColorSlider {
                required property var modelData
                objectName: "lightDepth_" + modelData.key
                editModel: light.raw; width: parent.width; controlHeight: 32
                label: modelData.label; from: modelData.from; to: modelData.to
                colors: modelData.colors ? modelData.colors : [theme.s3, theme.s3]
                value: light.values[modelData.key]
                enabled: !light.model.unavailable
                onCommitted: (next) => light.model.adjust(modelData.key, next)
            }
        }
        Text {
            width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 11
            text: "What lies at the chosen depth is left as it is; Brightness and Warmth Near / Far change what is nearer or farther. Feather is how far from the depth the effect takes to come in: drag the two markers on the depth picture, or the handle under the position handle on the photo. Size narrows the effect to a pool around the handle on the photo (100 is the whole photo). Drag the handles on the photo to change position, size and feather."
        }
    }
}
