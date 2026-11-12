import QtQuick

Column {
    id: wheel
    required property var model
    required property string setting
    property string label: ""
    property int diameter: 110
    property real draftHue: 0
    property real draftSat: 0
    readonly property var values: model.values[setting]
    readonly property real hue: pointer.pressed ? draftHue : values.hue
    readonly property real saturation: pointer.pressed ? draftSat : values.sat
    spacing: 6

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: wheel.label; color: theme.fg2
        font.pixelSize: 12
    }
    Item {
        anchors.horizontalCenter: parent.horizontalCenter
        width: wheel.diameter; height: width
        Canvas {
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d")
                var size = Math.ceil(width), radius = size / 2
                ctx.clearRect(0, 0, width, height)
                for (var y = 0; y < size; ++y) for (var x = 0; x < size; ++x) {
                    var dx = (x + 0.5 - radius) / radius
                    var dy = (radius - y - 0.5) / radius
                    var saturation = Math.sqrt(dx * dx + dy * dy)
                    if (saturation > 1) continue
                    var hue = ((Math.atan2(dy, dx) / (2 * Math.PI) + 1) % 1) * 6
                    var sector = Math.floor(hue), fraction = hue - sector
                    var value = 0.82, low = value * (1 - saturation)
                    var falling = value * (1 - fraction * saturation)
                    var rising = value * (1 - (1 - fraction) * saturation)
                    var rgb = [[value,rising,low], [falling,value,low], [low,value,rising],
                               [low,falling,value], [rising,low,value], [value,low,falling]][sector]
                    ctx.fillStyle = "rgb(" + Math.round(rgb[0] * 255) + "," + Math.round(rgb[1] * 255) + "," + Math.round(rgb[2] * 255) + ")"
                    ctx.globalAlpha = Math.min(1, (1 - saturation) * radius)
                    ctx.fillRect(x, y, 1, 1)
                }
                ctx.globalAlpha = 1

            }
        }
        Rectangle {
            x: parent.width / 2 + Math.cos(wheel.hue * Math.PI / 180) * wheel.saturation / 100 * (parent.width / 2 - 3) - 5
            y: parent.height / 2 - Math.sin(wheel.hue * Math.PI / 180) * wheel.saturation / 100 * (parent.height / 2 - 3) - 5
            width: 10; height: 10; radius: 5
            color: "transparent"; border.color: "#20242b"; border.width: 2
            Rectangle { anchors.centerIn: parent; width: 3; height: 3; radius: 1.5; color: "#c4cbd4" }
        }
        MouseArea {
            id: pointer
            objectName: "rawWheel_" + wheel.setting
            anchors.fill: parent
            enabled: wheel.model.loaded && !wheel.model.saving
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: Qt.CrossCursor
            function update(mouse) {
                var dx = mouse.x - width / 2
                var dy = height / 2 - mouse.y
                wheel.draftHue = (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360
                wheel.draftSat = Math.min(100, Math.sqrt(dx * dx + dy * dy) / (width / 2 - 3) * 100)
                if (mouse.button === Qt.RightButton) { wheel.draftHue = 0; wheel.draftSat = 0 }
            }
            onPressed: (mouse) => { wheel.model.beginGesture(); update(mouse) }
            onPositionChanged: (mouse) => {
                if (pressed) {
                    update(mouse)
                    wheel.model.setGrading(wheel.setting, wheel.draftHue, wheel.draftSat, wheel.values.lum)
                }
            }
            onReleased: { wheel.model.setGrading(wheel.setting, wheel.draftHue, wheel.draftSat, wheel.values.lum); wheel.model.endGesture() }
            onCanceled: wheel.model.endGesture()
            Accessible.role: Accessible.Slider
            Accessible.name: wheel.label + " hue and saturation. Right-click to reset."
        }
    }
    RawColorSlider {
        editModel: wheel.model
        anchors.horizontalCenter: parent.horizontalCenter
        width: wheel.diameter; compact: true
        label: wheel.label + " luminance"
        enabled: wheel.model.loaded && !wheel.model.saving
        value: wheel.values.lum
        colors: ["#292e35", "#717985", "#d9dee5"]
        onCommitted: (next) => wheel.model.setGrading(wheel.setting, wheel.values.hue, wheel.values.sat, next)
    }
}
