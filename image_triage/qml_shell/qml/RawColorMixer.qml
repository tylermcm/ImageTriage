import QtQuick
import QtQuick.Controls

Column {
    id: mixer
    required property var model
    property int channel: 0
    property string mode: "Color"
    property string page: "mixer"                 // "mixer" or "point"
    // Point Color belongs to the whole photo. The same mixer adjusts a mask's colours, where there is no such page.
    readonly property bool hasPoint: !!model && model.pointColor !== undefined
    signal pickRequested(bool on)                 // the panel arms the picker over the photo
    readonly property var names: ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]
    readonly property var hues: ["#e5635b", "#e99154", "#d8c34d", "#58b96f", "#58bfb7", "#5999ed", "#aa72dc", "#d16eba"]
    spacing: 8

    Row {
        height: 29; spacing: 16
        Repeater {
            model: mixer.hasPoint ? [["mixer", "Mixer"], ["point", "Point Color"]] : [["mixer", "Mixer"]]
            delegate: Text {
                id: tabText
                required property var modelData
                objectName: "rawMixerTab_" + modelData[0]
                text: modelData[1]; font.pixelSize: 12; bottomPadding: 8
                color: mixer.page === modelData[0] ? theme.fg : theme.fg3
                activeFocusOnTab: true
                Rectangle { visible: mixer.page === tabText.modelData[0]; anchors.bottom: parent.bottom; width: parent.width; height: 2; color: theme.accent }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: mixer.page = tabText.modelData[0] }
                Keys.onReturnPressed: mixer.page = modelData[0]
                Keys.onSpacePressed: mixer.page = modelData[0]
                Accessible.role: Accessible.PageTab; Accessible.name: modelData[1]
                Accessible.onPressAction: mixer.page = modelData[0]
            }
        }
    }
    Loader {
        active: mixer.hasPoint
        visible: active && mixer.page === "point"; width: parent.width
        sourceComponent: RawPointColor { objectName: "rawPointColor"; model: mixer.model.pointColor; raw: mixer.model }
    }
    Row {
        visible: mixer.page === "mixer"
        width: parent.width; height: 28; spacing: 8
        Text { text: "Adjust"; color: theme.fg2; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
        ComboBox {
            objectName: "rawMixerMode"
            width: parent.width - 48; height: 27
            model: ["Color", "Hue", "Saturation", "Luminance"]
            onActivated: mixer.mode = currentText
            contentItem: Text { text: parent.displayText; color: theme.fg; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter; leftPadding: 8 }
            background: Rectangle { radius: 6; color: theme.s2; border.color: theme.lineSoft }
        }
    }
    Row {
        visible: mixer.page === "mixer"
        width: parent.width; height: 30
        spacing: Math.max(1, (width - 9 * 20) / 8)
        Repeater {
            model: 9
            delegate: Item {
                id: color
                required property int index
                width: 20; height: 26
                Rectangle {
                    anchors.centerIn: parent; width: 17; height: 17; radius: 8.5
                    color: mixer.channel === color.index ? (color.index < 8 ? mixer.hues[color.index] : theme.accent) : "transparent"
                    border.width: 2
                    border.color: color.index < 8 ? mixer.hues[color.index] : "transparent"
                    Canvas {
                        visible: color.index === 8
                        anchors.fill: parent
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.clearRect(0, 0, width, height); ctx.lineWidth = 2
                            for (var i = 0; i < 8; ++i) {
                                ctx.beginPath()
                                ctx.arc(width / 2, height / 2, width / 2 - 1, i * Math.PI / 4, (i + 1) * Math.PI / 4)
                                ctx.strokeStyle = mixer.hues[i]; ctx.stroke()
                            }
                        }
                    }
                    Rectangle { visible: mixer.channel === color.index; anchors.centerIn: parent; width: 4; height: 4; radius: 2; color: theme.fg }
                }
                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: mixer.channel = color.index }
                ToolTip.text: color.index < 8 ? mixer.names[color.index] : "All colors"
                ToolTip.visible: hover.hovered
                Accessible.role: Accessible.RadioButton
                Accessible.name: index < 8 ? mixer.names[index] : "All colors"
                Accessible.checked: mixer.channel === index
                Accessible.onPressAction: mixer.channel = index
            }
        }
    }
    Column {
        visible: mixer.page === "mixer" && mixer.mode === "Color"
        width: parent.width
        Repeater {
            model: [{label: "Hue", key: "hslHue"}, {label: "Saturation", key: "hslSat"}, {label: "Luminance", key: "hslLum"}]
            delegate: RawColorSlider {
        editModel: mixer.model
                required property var modelData
                width: parent.width
                objectName: "rawMixer_" + modelData.key
                label: modelData.label
                enabled: mixer.model.loaded && !mixer.model.saving
                value: mixer.model.values[modelData.key][mixer.channel < 8 ? mixer.channel : 0]
                colors: modelData.key === "hslHue" ? ["#a46e9c", "#ad9b72", "#91b69d"]
                    : modelData.key === "hslSat" ? ["#646a73", "#88858a", mixer.hues[Math.min(7, mixer.channel)]]
                    : ["#282b31", "#80858d", "#d9dee5"]
                onCommitted: (next) => mixer.model.setMixer(mixer.channel === 8 ? -1 : mixer.channel, modelData.key, next)
            }
        }
    }
    Column {
        visible: mixer.page === "mixer" && mixer.mode !== "Color"
        width: parent.width
        Repeater {
            model: 8
            delegate: RawColorSlider {
        editModel: mixer.model
                required property int index
                readonly property string componentKey: mixer.mode === "Hue" ? "hslHue" : mixer.mode === "Saturation" ? "hslSat" : "hslLum"
                width: parent.width
                label: mixer.names[index]
                enabled: mixer.model.loaded && !mixer.model.saving
                colors: ["#414750", "#777e88", mixer.hues[index]]
                value: mixer.model.values[componentKey][index]
                onCommitted: (next) => mixer.model.setMixer(index, componentKey, next)
            }
        }
    }
}
