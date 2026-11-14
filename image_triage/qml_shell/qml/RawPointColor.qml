import QtQuick
import QtQuick.Controls

// Point Color: pick a color in the photo, then shift that color alone. Up to eight samples.
Column {
    id: point
    required property var model
    required property var raw
    readonly property bool editable: raw.loaded && !raw.saving
    readonly property var cur: model.current
    readonly property bool has: model.selected >= 0
    spacing: 6
    enabled: editable

    Row {
        width: parent.width; height: 30; spacing: 6
        Rectangle {
            id: pickButton
            objectName: "pointColorPick"
            width: 30; height: 30; radius: 8
            color: point.model.picking ? Qt.alpha(theme.accent, 0.22) : pickHover.hovered || activeFocus ? theme.s3 : theme.s2
            border.color: point.model.picking ? Qt.alpha(theme.accent, 0.5) : activeFocus ? theme.accent : theme.lineSoft
            opacity: point.model.full && !point.model.picking ? 0.4 : 1
            activeFocusOnTab: true
            function toggle() { point.model.setPicking(!point.model.picking) }
            Icon { anchors.centerIn: parent; size: 16; color: point.model.picking ? theme.accentFg : theme.fg2
                   path: "m14 4 6 6m-9-3 6 6M4 16l9-9 4 4-9 9H4zM15 5l2-2 4 4-2 2" }
            HoverHandler { id: pickHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: pickButton.toggle() }
            Keys.onReturnPressed: toggle()
            Keys.onSpacePressed: toggle()
            ToolTip.text: point.model.full ? "Eight samples is the most a photo can have" : point.model.picking ? "Click a color in the photo. Escape cancels." : "Pick a color from the photo"
            ToolTip.visible: pickHover.hovered; ToolTip.delay: 500
            Accessible.role: Accessible.Button; Accessible.name: "Pick a color from the photo"
            Accessible.onPressAction: toggle()
        }
        Repeater {
            model: point.model.rows
            delegate: Rectangle {
                id: chip
                required property var modelData
                required property int index
                objectName: "pointColorSample_" + index
                width: 24; height: 24; radius: 12; anchors.verticalCenter: parent.verticalCenter
                color: modelData.swatch
                border.width: point.model.selected === index ? 2 : 1
                border.color: point.model.selected === index ? theme.fg : theme.line
                activeFocusOnTab: true
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: point.model.select(chip.index) }
                Keys.onReturnPressed: point.model.select(index)
                Keys.onSpacePressed: point.model.select(index)
                Keys.onDeletePressed: { point.model.select(index); point.model.remove() }
                Accessible.role: Accessible.Button; Accessible.name: "Color sample " + (index + 1)
                Accessible.onPressAction: point.model.select(index)
            }
        }
    }
    Text {
        objectName: "pointColorHint"
        visible: !point.has
        width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 11
        text: point.model.picking ? "Click a color in the photo."
            : point.model.rows.length ? "Choose a sample to adjust it."
            : "Pick a color from the photo with the dropper, then change only that color."
    }
    Column {
        visible: point.has
        width: parent.width; spacing: 3
        Repeater {
            model: [
                {label: "Hue", key: "hue", low: -100, colors: ["#d16eba", "#a7a7a0", "#58b96f"]},
                {label: "Saturation", key: "saturation", low: -100, colors: ["#858a91", "#adac75", "#d06d65"]},
                {label: "Luminance", key: "luminance", low: -100, colors: ["#20242a", "#808890", "#e8eaee"]}
            ]
            delegate: RawColorSlider {
                required property var modelData
                objectName: "pointColor_" + modelData.key
                editModel: point.raw; width: parent.width; controlHeight: 32
                label: modelData.label; from: modelData.low; to: 100; colors: modelData.colors
                value: point.cur[modelData.key] === undefined ? 0 : point.cur[modelData.key]
                onCommitted: (next) => point.model.adjust(modelData.key, next)
            }
        }
        Text { text: "WHICH COLORS"; topPadding: 6; color: theme.fg3; font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 1.2 }
        RawColorSlider {
            objectName: "pointColor_range"
            editModel: point.raw; width: parent.width; controlHeight: 32
            label: "Range"; from: 0; to: 100
            value: point.cur.range === undefined ? 50 : point.cur.range
            onCommitted: (next) => point.model.adjust("range", next)
        }
        RawColorSlider {
            objectName: "pointColor_variance"
            editModel: point.raw; width: parent.width; controlHeight: 32
            label: "Variance"; from: -100; to: 100
            value: point.cur.variance === undefined ? 0 : point.cur.variance
            onCommitted: (next) => point.model.adjust("variance", next)
        }
        Text {
            width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 11
            text: "Range is how far from the picked color the change reaches. Variance evens out (below zero) or spreads (above) the colors inside it."
        }
        Row {
            spacing: 12; topPadding: 4
            Text {
                objectName: "pointColorDelete"
                text: "Delete sample"; color: theme.accentFg; font.pixelSize: 12
                activeFocusOnTab: true
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: point.model.remove() }
                Keys.onReturnPressed: point.model.remove()
                Accessible.role: Accessible.Button; Accessible.name: text
                Accessible.onPressAction: point.model.remove()
            }
            Text {
                visible: point.model.rows.length > 1
                text: "Delete all"; color: theme.fg3; font.pixelSize: 12
                activeFocusOnTab: true
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: point.model.removeAll() }
                Keys.onReturnPressed: point.model.removeAll()
                Accessible.role: Accessible.Button; Accessible.name: text
                Accessible.onPressAction: point.model.removeAll()
            }
        }
    }
}
