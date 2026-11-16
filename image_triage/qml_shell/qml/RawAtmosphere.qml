import QtQuick
import QtQuick.Controls

// Atmosphere: fog laid over the photo by distance. The effect lives in this section only: it is adjusted here, applied live,
// and is not a mask in the Mask tab. A preset sets the kind of fog; Amount, Height and the Advanced settings shape it. The
// picture shows where the fog lies on the photo, and the pad moves its patches about (Luminar's "Clouds Drift").
Column {
    id: air
    required property var model
    required property var raw
    property bool advanced: false
    readonly property bool editable: raw.loaded && !raw.saving
    readonly property var values: model.settings
    spacing: 4
    enabled: editable
    Component.onCompleted: { model.refresh(); if (!model.unavailable && raw.loaded) model.prepare() }
    Connections {
        target: air.raw
        function onChanged() { if (air.raw.loaded && !air.model.ready && !air.model.busy && !air.model.unavailable && !air.model.error) air.model.prepare() }
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
            text: air.model.busy ? air.model.message : air.model.unavailable ? "" : "Choose a fog, then set its Amount."
            width: parent.width - 62; elide: Text.ElideRight
            color: theme.fg2; font.pixelSize: 12
        }
        Row {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 4
            Glyph {
                objectName: "atmosphereShown"; name: "eye"; lit: air.model.shown
                label: air.model.shown ? "Hide the fog" : "Show the fog"
                enabled: air.model.active; opacity: enabled ? 1 : 0.35
                onActivated: air.model.toggleShown()
            }
            Glyph {
                objectName: "atmosphereRemove"; name: "undo"; label: "Remove Atmosphere"
                enabled: air.model.active; opacity: enabled ? 1 : 0.35
                onActivated: air.model.remove()
            }
        }
    }

    Text {
        objectName: "atmosphereUnavailable"
        visible: !!air.model.unavailable
        width: parent.width; wrapMode: Text.WordWrap
        text: air.model.unavailable; color: theme.fg3; font.pixelSize: 11; bottomPadding: 6
    }

    // the three kinds of fog
    Row {
        objectName: "atmospherePresets"
        width: parent.width; spacing: 8
        Repeater {
            model: air.model.presets
            delegate: Item {
                id: tile
                required property string modelData
                required property int index
                objectName: "atmospherePreset_" + index
                readonly property bool selected: air.model.selectedPreset === index
                width: Math.floor((air.width - 16) / 3); height: width + 22
                activeFocusOnTab: true
                Rectangle {
                    id: art
                    width: parent.width; height: parent.width; radius: 8; clip: true
                    border.color: tile.selected ? theme.accent : tileHover.hovered ? theme.line : theme.lineSoft; border.width: tile.selected ? 2 : 1
                    gradient: Gradient {
                        GradientStop { position: 0; color: tile.index === 2 ? "#2a2c30" : tile.index === 0 ? "#3a3d42" : "#55585d" }
                        GradientStop { position: tile.index === 2 ? 0.55 : 0.6; color: tile.index === 2 ? "#3b3e43" : tile.index === 0 ? "#8c9096" : "#7d8086" }
                        GradientStop { position: tile.index === 2 ? 0.78 : 1; color: tile.index === 2 ? "#b9bcc0" : tile.index === 0 ? "#c9ccd0" : "#aeb1b5" }
                        GradientStop { position: 1; color: tile.index === 2 ? "#e0e2e4" : tile.index === 0 ? "#c9ccd0" : "#aeb1b5" }
                    }
                }
                Text {
                    y: art.height + 4; width: parent.width; horizontalAlignment: Text.AlignHCenter
                    text: tile.modelData; color: tile.selected ? theme.accentFg : theme.fg2; font.pixelSize: 11; elide: Text.ElideRight
                }
                HoverHandler { id: tileHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: air.model.choosePreset(tile.index) }
                Keys.onReturnPressed: air.model.choosePreset(index)
                Keys.onSpacePressed: air.model.choosePreset(index)
                Accessible.role: Accessible.RadioButton; Accessible.name: modelData; Accessible.checked: selected
                Accessible.onPressAction: air.model.choosePreset(index)
            }
        }
    }

    RawColorSlider {
        objectName: "atmosphere_amount"
        editModel: air.raw; width: parent.width; controlHeight: 32
        label: "Amount"; from: 0; to: 100
        value: air.values.amount
        enabled: !air.model.unavailable
        onCommitted: (next) => air.model.adjust("amount", next)
    }

    // where the fog lies on the photo
    Rectangle {
        id: picture
        objectName: "atmospherePicture"
        readonly property real aspect: air.raw.originalWidth > 0 ? air.raw.originalHeight / air.raw.originalWidth : 2 / 3
        width: aspect > 1 ? Math.round(Math.min(parent.width, 230 / aspect)) : parent.width
        height: Math.round(width * aspect)
        anchors.horizontalCenter: parent.horizontalCenter
        radius: 8; color: theme.base; border.color: theme.line; clip: true
        Image {
            anchors.fill: parent; anchors.margins: 1
            source: air.model.picture; cache: false; smooth: true
            visible: air.model.ready
        }
        Text {
            objectName: "atmosphereStatus"
            visible: !air.model.ready
            anchors.centerIn: parent; width: parent.width - 24
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
            color: air.model.error ? theme.rej : theme.fg3; font.pixelSize: 11
            text: air.model.error || (air.model.busy || air.model.waiting ? air.model.message : air.model.unavailable ? "Depth model not installed" : "The fog is shown here.")
        }
        Rectangle {
            objectName: "atmosphereCreate"
            visible: !air.model.ready && !air.model.busy && !air.model.waiting && !air.model.unavailable
            anchors.horizontalCenter: parent.horizontalCenter; y: parent.height / 2 + 14
            width: createLabel.implicitWidth + 28; height: 28; radius: 9; color: createHover.hovered ? Qt.lighter(theme.accent, 1.1) : theme.accent
            Text { id: createLabel; anchors.centerIn: parent; text: air.model.error ? "Try again" : "Read the depth"; color: theme.onAccent; font.pixelSize: 12; font.weight: Font.Medium }
            HoverHandler { id: createHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: air.model.prepare() }
            Accessible.role: Accessible.Button; Accessible.name: createLabel.text
            Accessible.onPressAction: air.model.prepare()
        }
        Text {
            objectName: "atmosphereHint"
            visible: air.model.ready && air.values.amount <= 0
            anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 8
            width: parent.width - 24; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
            text: "Raise Amount to add the fog."; color: "#f4f4f4"; style: Text.Outline; styleColor: "#000000"; font.pixelSize: 11
        }
    }

    // Clouds Drift: slide the patches of fog about (the fog stays the same, it only lies elsewhere)
    Item {
        width: parent.width; height: 20
        Text { anchors.verticalCenter: parent.verticalCenter; text: "Clouds Drift"; color: theme.fg2; font.pixelSize: 12 }
    }
    Rectangle {
        id: drift
        objectName: "atmosphereDrift"
        width: parent.width; height: 64
        radius: 8; color: theme.base; border.color: drift.activeFocus ? theme.accent : theme.line; clip: true
        activeFocusOnTab: true
        function nudge(dx, dy) { air.model.drift(air.values.driftX + dx, air.values.driftY + dy); air.model.flush() }
        Keys.onLeftPressed: nudge(-0.05, 0)
        Keys.onRightPressed: nudge(0.05, 0)
        Keys.onUpPressed: nudge(0, -0.05)
        Keys.onDownPressed: nudge(0, 0.05)
        Accessible.role: Accessible.Pane
        Accessible.name: "Clouds Drift"
        Accessible.description: "Drag, or use the arrow keys, to move the patches of fog."
        Canvas {   // a grid of dots, as a place to drag on
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d"); ctx.reset(); ctx.clearRect(0, 0, width, height)
                ctx.fillStyle = "#33ffffff"
                for (var x = 8; x < width; x += 14) for (var y = 8; y < height; y += 14) ctx.fillRect(x, y, 2, 2)
            }
            onWidthChanged: requestPaint()
        }
        Rectangle {
            objectName: "atmosphereDriftHandle"
            x: air.values.driftX * (drift.width - 2 * 9) + 9 - width / 2
            y: air.values.driftY * (drift.height - 2 * 9) + 9 - height / 2
            width: 18; height: 18; radius: 9
            color: "#f4f4f4"; border.color: "#202020"; border.width: 1
            Icon { anchors.centerIn: parent; size: 12; path: "M12 3v18M3 12h18M8 7l4-4 4 4M8 17l4 4 4-4M7 8l-4 4 4 4M17 8l4 4-4 4"; color: "#202020" }
        }
        MouseArea {
            objectName: "atmosphereDriftDrag"
            anchors.fill: parent
            enabled: air.editable
            preventStealing: true          // the panel scrolls vertically: it must not take a vertical drag away
            cursorShape: Qt.SizeAllCursor
            function put(mouse) {
                air.model.drift(Math.max(0, Math.min(1, (mouse.x - 9) / (drift.width - 18))), Math.max(0, Math.min(1, (mouse.y - 9) / (drift.height - 18))))
            }
            onPressed: (mouse) => { drift.forceActiveFocus(); air.raw.beginGesture(); put(mouse) }
            onPositionChanged: (mouse) => { if (pressed) put(mouse) }
            onReleased: air.raw.endGesture()
            onCanceled: air.raw.endGesture()
        }
    }

    RawColorSlider {
        objectName: "atmosphere_height"
        editModel: air.raw; width: parent.width; controlHeight: 32
        label: "Height"; from: 0; to: 100
        value: air.values.height
        enabled: !air.model.unavailable
        onCommitted: (next) => air.model.adjust("height", next)
    }

    Item {
        objectName: "atmosphereAdvanced"
        width: parent.width; height: 30
        activeFocusOnTab: true
        Text { anchors.verticalCenter: parent.verticalCenter; text: "Advanced"; color: theme.fg2; font.pixelSize: 12 }
        Icon { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; size: 12; name: air.advanced ? "chevUp" : "chevDown"; color: theme.fg2 }
        TapHandler { onTapped: air.advanced = !air.advanced }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Keys.onReturnPressed: air.advanced = !air.advanced
        Keys.onSpacePressed: air.advanced = !air.advanced
        Accessible.role: Accessible.Button; Accessible.name: "Advanced"
        Accessible.onPressAction: air.advanced = !air.advanced
    }
    Column {
        visible: air.advanced
        width: parent.width; spacing: 4
        Repeater {
            model: [
                {key: "patchiness", label: "Patchiness"},
                {key: "cloudSize", label: "Cloud Size"},
                {key: "vibrance", label: "Vibrance"},
                {key: "start", label: "Depth"}
            ]
            delegate: RawColorSlider {
                required property var modelData
                objectName: "atmosphere_" + modelData.key
                editModel: air.raw; width: parent.width; controlHeight: 32
                label: modelData.label; from: 0; to: 100
                value: air.values[modelData.key]
                enabled: !air.model.unavailable
                onCommitted: (next) => air.model.adjust(modelData.key, next)
            }
        }
        Text {
            width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 11
            text: "Patchiness breaks the fog into clumps and Cloud Size sets how big they are. Vibrance is how much of the photo's own colour the fog keeps. Depth is how far from the camera the fog begins: raise it to keep the foreground clear. Height is how far up the frame the fog reaches."
        }
    }
}
