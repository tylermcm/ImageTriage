import QtQuick
import QtQuick.Controls

// Light Depth: light a photo near and far apart. The effect lives in this section only: it is adjusted here, applied live,
// and is not a mask in the Mask tab. The depth is chosen by clicking the photo with the dropper (or with the Depth slider, or
// by dragging in the depth picture); the handles for position, size and wrap are drawn on the photo while the section is open.
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
        // 0 is the nearest thing in the photo, 100 the farthest: the subject sits at this depth and is left alone.
        value: (1 - light.values.depth) * 100
        enabled: !light.model.unavailable
        onCommitted: (next) => light.model.adjust("depth", 1 - next / 100)
    }

    // The depth picture is the secondary view: the same depth, as a plane drawn across the photo's depth map.
    Rectangle {
        id: pad
        objectName: "lightDepthPad"
        readonly property real aspect: light.raw.originalWidth > 0 ? light.raw.originalHeight / light.raw.originalWidth : 2 / 3
        width: aspect > 1 ? Math.round(Math.min(parent.width, 230 / aspect)) : parent.width
        height: Math.round(width * aspect)
        anchors.horizontalCenter: parent.horizontalCenter
        radius: 8; color: theme.base; border.color: pad.activeFocus ? theme.accent : theme.line
        clip: true
        activeFocusOnTab: true
        function nudge(delta) { light.model.adjust("depth", light.values.depth + delta); light.model.flush() }
        Keys.onUpPressed: nudge(0.02)
        Keys.onDownPressed: nudge(-0.02)
        Accessible.role: Accessible.Pane
        Accessible.name: "Depth of the split between near and far"
        Accessible.description: "Drag up or down, or use the arrow keys, to change the depth."
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
        Rectangle {   // the plane: near (bright) below it, far above it
            visible: light.model.ready
            x: 0; width: parent.width; height: 2; y: (1 - light.values.depth) * pad.height - 1
            color: "#f4f4f4"; opacity: 0.8
        }
        Rectangle {
            id: handle
            objectName: "lightDepthPadHandle"
            visible: light.model.ready
            x: pad.width / 2 - width / 2; y: (1 - light.values.depth) * pad.height - height / 2
            width: 20; height: 20; radius: 10
            color: "#f4f4f4"; border.color: "#202020"; border.width: 1
            Icon { anchors.centerIn: parent; size: 12; path: "M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4"; color: "#202020" }
        }
        MouseArea {
            objectName: "lightDepthPadDrag"
            anchors.fill: parent
            enabled: light.model.ready && light.editable
            cursorShape: Qt.SizeVerCursor
            function put(mouse) { light.model.adjust("depth", 1 - Math.max(0, Math.min(1, mouse.y / pad.height))) }
            onPressed: (mouse) => { pad.forceActiveFocus(); light.raw.beginGesture(); put(mouse) }
            onPositionChanged: (mouse) => { if (pressed) put(mouse) }
            onReleased: light.raw.endGesture()
            onCanceled: light.raw.endGesture()
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
        Repeater {
            model: [
                {key: "brightnessNear", label: "Brightness Near", from: -100, to: 100, colors: null},
                {key: "warmthNear", label: "Warmth Near", from: -100, to: 100, colors: ["#608fc3", "#a7a7a0", "#d3bd64"]},
                {key: "brightnessFar", label: "Brightness Far", from: -100, to: 100, colors: null},
                {key: "warmthFar", label: "Warmth Far", from: -100, to: 100, colors: ["#608fc3", "#a7a7a0", "#d3bd64"]},
                {key: "size", label: "Size", from: 0, to: 100, colors: null},
                {key: "wrap", label: "Wrap", from: 0, to: 100, colors: null}
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
            text: "What lies at the depth is left alone; Brightness and Warmth change what is nearer (Near) and what is farther (Far). Wrap is how wide a slab around the depth is left alone. Size narrows the effect to a pool around the handle on the photo (100 is the whole photo). Drag the handles on the photo to change position, size and wrap."
        }
    }
}
