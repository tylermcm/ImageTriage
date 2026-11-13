import QtQuick
import QtQuick.Controls

// Light Depth: a light placed inside the scene. The pad is the photo's depth map (near is bright); the handle is the
// light. Where it falls becomes an ordinary mask named "Light Depth", so it can be refined in the Mask tab.
Column {
    id: light
    required property var model
    required property var raw
    property bool advanced: false
    readonly property bool editable: raw.loaded && !raw.saving
    readonly property var values: model.settings
    spacing: 4
    enabled: editable
    Component.onCompleted: { model.refresh(); if (!model.unavailable && raw.loaded) model.prepare() }
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
        width: parent.width; height: 28
        Text {
            anchors.verticalCenter: parent.verticalCenter; width: parent.width - 62
            text: "Place a light inside the scene."; color: theme.fg2; font.pixelSize: 12; elide: Text.ElideRight
        }
        Row {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 4
            Glyph {
                objectName: "lightDepthShown"; name: "eye"; lit: light.model.shown
                label: light.model.shown ? "Hide the light" : "Show the light"
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
        function nudge(dx, dy) {
            light.model.place(light.values.x + dx, light.values.y + dy)
        }
        Keys.onLeftPressed: nudge(-0.02, 0)
        Keys.onRightPressed: nudge(0.02, 0)
        Keys.onUpPressed: nudge(0, -0.02)
        Keys.onDownPressed: nudge(0, 0.02)
        Accessible.role: Accessible.Pane
        Accessible.name: "Light position in the scene"
        Accessible.description: "Drag, or use the arrow keys, to move the light. It takes the distance of the point it is on."
        Image {
            anchors.fill: parent; anchors.margins: 1
            source: light.model.pad; cache: false; smooth: true
            visible: light.model.ready
        }
        Text {
            objectName: "lightDepthStatus"
            visible: !light.model.ready
            anchors.centerIn: parent; width: parent.width - 24
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
            color: light.model.error ? theme.rej : theme.fg3; font.pixelSize: 11
            text: light.model.error || (light.model.busy ? light.model.message : light.model.unavailable ? "Depth model not installed" : "The depth map appears here.")
        }
        Rectangle {
            id: handle
            visible: light.model.ready
            x: light.values.x * pad.width - width / 2; y: light.values.y * pad.height - height / 2
            width: 20; height: 20; radius: 10
            color: "#f4f4f4"; border.color: "#202020"; border.width: 1
            Icon { anchors.centerIn: parent; size: 12; path: "M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4"; color: "#202020" }
        }
        MouseArea {
            anchors.fill: parent
            enabled: light.model.ready && light.editable
            cursorShape: Qt.CrossCursor
            function put(mouse) { light.model.place(mouse.x / pad.width, mouse.y / pad.height) }
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
        RawColorSlider {
            objectName: "lightDepth_depth"
            editModel: light.raw; width: parent.width; controlHeight: 32
            label: "Distance"; from: 0; to: 100
            // 0 is the nearest thing in the photo, 100 the farthest.
            value: (1 - light.values.depth) * 100
            enabled: !light.model.unavailable
            onCommitted: (next) => light.model.adjust("depth", 1 - next / 100)
        }
        RawColorSlider {
            objectName: "lightDepth_reach"
            editModel: light.raw; width: parent.width; controlHeight: 32
            label: "Reach"; from: 0; to: 100
            value: light.values.reach
            enabled: !light.model.unavailable
            onCommitted: (next) => light.model.adjust("reach", next)
        }
        RawColorSlider {
            objectName: "lightDepth_focus"
            editModel: light.raw; width: parent.width; controlHeight: 32
            label: "Focus"; from: 0; to: 100
            value: light.values.focus
            enabled: !light.model.unavailable
            onCommitted: (next) => light.model.adjust("focus", next)
        }
        Text {
            width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 11
            text: "Reach is how far through the scene the light carries. Focus narrows it to a pool around the handle. The light is a mask named “Light Depth”: refine it in the Mask tab."
        }
    }
}
