import QtQuick
import QtQuick.Controls

Column {
    id: curve
    required property var model
    property string channel: "pointCurve"
    property bool parametric: false
    property var editingPoints: []
    property int selected: -1
    readonly property var storedPoints: model.values[channel]
    readonly property var points: pointer.pressed ? editingPoints : storedPoints.length ? storedPoints : [[0, 0], [255, 255]]
    readonly property color stroke: channel === "pointCurveRed" ? "#e5635b" : channel === "pointCurveGreen" ? "#58c48e" : channel === "pointCurveBlue" ? "#68a9ff" : "#d8dfe9"
    spacing: 10
    Connections { target: curve.model; function onChanged() { plot.requestPaint() } }
    onChannelChanged: { selected = -1; plot.requestPaint() }
    onParametricChanged: plot.requestPaint()

    Row {
        height: 28; spacing: 7
        Text { text: "Adjust"; color: theme.fg2; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
        Repeater {
            model: [
                {key: "parametric", label: "Parametric curve", color: "#a3aab5"},
                {key: "pointCurve", label: "RGB curve", color: "#d8dfe9"},
                {key: "pointCurveRed", label: "Red curve", color: "#e5635b"},
                {key: "pointCurveGreen", label: "Green curve", color: "#58c48e"},
                {key: "pointCurveBlue", label: "Blue curve", color: "#68a9ff"}
            ]
            delegate: Item {
                id: option
                required property var modelData
                readonly property bool checked: modelData.key === "parametric" ? curve.parametric : !curve.parametric && curve.channel === modelData.key
                width: 23; height: 28
                Rectangle {
                    visible: option.modelData.key !== "parametric"
                    anchors.centerIn: parent; width: 17; height: 17; radius: 8.5
                    color: "transparent"; border.width: 2; border.color: option.modelData.color
                    Rectangle { visible: option.checked; anchors.centerIn: parent; width: 5; height: 5; radius: 2.5; color: option.modelData.color }
                }
                Icon {
                    visible: option.modelData.key === "parametric"
                    anchors.centerIn: parent; size: 18
                    path: "M3 19C8 19 6 5 12 5M7 19C12 19 10 5 16 5M11 19C16 19 14 5 20 5"
                    color: option.checked ? theme.accentFg : theme.fg2
                }
                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        curve.parametric = option.modelData.key === "parametric"
                        if (!curve.parametric) curve.channel = option.modelData.key
                    }
                }
                ToolTip.text: modelData.label; ToolTip.visible: hover.hovered
                Accessible.role: Accessible.RadioButton; Accessible.name: modelData.label; Accessible.checked: checked
                Accessible.onPressAction: {
                    curve.parametric = modelData.key === "parametric"
                    if (!curve.parametric) curve.channel = modelData.key
                }
            }
        }
    }
    Rectangle {
        width: parent.width; height: width
        color: theme.base; border.color: theme.lineSoft; radius: 4
        clip: true; layer.enabled: true
        Histogram {
            anchors.fill: parent; anchors.margins: 1
            service: curve.model.histograms; path: curve.visible ? curve.model.histogramPath : ""
            luma: Qt.alpha(theme.fg3, 0.26)
        }
        Canvas {
            id: plot
            anchors.fill: parent
            // The backend uses the same monotone cubic interpolation between control points.
            function pointSamples(points) {
                var slopes = [], tangents = [], samples = [], i
                for (i = 0; i < points.length - 1; ++i)
                    slopes.push((points[i + 1][1] - points[i][1]) / Math.max(1, points[i + 1][0] - points[i][0]))
                tangents[0] = slopes[0]
                tangents[points.length - 1] = slopes[slopes.length - 1]
                for (i = 1; i < points.length - 1; ++i)
                    tangents[i] = slopes[i - 1] * slopes[i] <= 0 ? 0 : (slopes[i - 1] + slopes[i]) / 2
                for (i = 0; i < slopes.length; ++i) {
                    if (slopes[i] === 0) { tangents[i] = 0; tangents[i + 1] = 0; continue }
                    var a = tangents[i] / slopes[i], b = tangents[i + 1] / slopes[i]
                    var magnitude = a * a + b * b
                    if (magnitude > 9) {
                        var scale = 3 / Math.sqrt(magnitude)
                        tangents[i] = scale * a * slopes[i]
                        tangents[i + 1] = scale * b * slopes[i]
                    }
                }
                var segment = 0
                for (var x = 0; x <= 255; ++x) {
                    if (x <= points[0][0]) { samples.push(points[0][1]); continue }
                    if (x >= points[points.length - 1][0]) { samples.push(points[points.length - 1][1]); continue }
                    while (segment < points.length - 2 && x > points[segment + 1][0]) ++segment
                    var span = points[segment + 1][0] - points[segment][0]
                    var t = (x - points[segment][0]) / span, t2 = t * t, t3 = t2 * t
                    var y = (2 * t3 - 3 * t2 + 1) * points[segment][1]
                        + (t3 - 2 * t2 + t) * span * tangents[segment]
                        + (-2 * t3 + 3 * t2) * points[segment + 1][1]
                        + (t3 - t2) * span * tangents[segment + 1]
                    samples.push(Math.max(0, Math.min(255, y)))
                }
                return samples
            }
            function parametricSamples() {
                var values = curve.model.values
                var splits = values.curveSplits || [25, 50, 75]
                var s = [Math.max(5, Math.min(95, splits[0])) / 100,
                         Math.max(5, Math.min(95, splits[1])) / 100,
                         Math.max(5, Math.min(95, splits[2])) / 100]
                var centers = [s[0] / 2, (s[0] + s[1]) / 2, (s[1] + s[2]) / 2, (s[2] + 1) / 2]
                var widths = [s[0], s[1] - s[0], s[2] - s[1], 1 - s[2]]
                var amounts = [values.curveShadows, values.curveDarks, values.curveLights, values.curveHighlights]
                var samples = [], previous = 0
                for (var i = 0; i <= 255; ++i) {
                    var x = i / 255, y = x
                    for (var k = 0; k < 4; ++k) {
                        var d = (x - centers[k]) / (Math.max(0.1, widths[k]) * 1.2)
                        y += amounts[k] / 100 * 0.25 * Math.exp(-d * d * 2) * x * (1 - x) * 4
                    }
                    previous = Math.max(previous, Math.max(0, Math.min(1, y)))
                    samples.push(previous * 255)
                }
                return samples
            }
            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                ctx.strokeStyle = theme.line; ctx.lineWidth = 1
                for (var i = 1; i < 4; ++i) {
                    ctx.beginPath(); ctx.moveTo(width * i / 4, 0); ctx.lineTo(width * i / 4, height); ctx.stroke()
                    ctx.beginPath(); ctx.moveTo(0, height * i / 4); ctx.lineTo(width, height * i / 4); ctx.stroke()
                }
                var samples = curve.parametric ? parametricSamples() : pointSamples(curve.points)
                ctx.strokeStyle = curve.stroke; ctx.lineWidth = 1.6; ctx.beginPath()
                for (i = 0; i < samples.length; ++i) {
                    var x = i * (width - 2) / 255 + 1, y = height - 1 - samples[i] / 255 * (height - 2)
                    if (i === 0) ctx.moveTo(x, y)
                    else ctx.lineTo(x, y)
                }
                ctx.stroke()
                if (!curve.parametric) for (i = 0; i < curve.points.length; ++i) {
                    ctx.beginPath()
                    ctx.arc(1 + curve.points[i][0] / 255 * (width - 2), height - 1 - curve.points[i][1] / 255 * (height - 2), 4, 0, Math.PI * 2)
                    ctx.fillStyle = i === curve.selected ? theme.accent : theme.s1
                    ctx.fill(); ctx.stroke()
                }
            }
        }
        MouseArea {
            id: pointer
            objectName: "rawCurveGraph"
            anchors.fill: parent; cursorShape: Qt.CrossCursor
            enabled: !curve.parametric && curve.model.loaded && !curve.model.saving
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            function findPoint(mouse, points) {
                for (var i = 0; i < points.length; ++i) {
                    var dx = mouse.x - points[i][0] / 255 * width
                    var dy = mouse.y - (1 - points[i][1] / 255) * height
                    if (dx * dx + dy * dy < 144) return i
                }
                return -1
            }
            onPressed: (mouse) => {
                curve.model.beginGesture()
                var points = curve.storedPoints.length ? JSON.parse(JSON.stringify(curve.storedPoints)) : [[0, 0], [255, 255]]
                curve.selected = findPoint(mouse, points)
                if (mouse.button === Qt.RightButton) {
                    if (curve.selected > 0 && curve.selected < points.length - 1) points.splice(curve.selected, 1)
                    curve.selected = -1
                } else if (curve.selected < 0 && points.length < 16) {
                    var x = Math.round(Math.max(1, Math.min(254, mouse.x / width * 255)))
                    var y = Math.round(Math.max(0, Math.min(255, (1 - mouse.y / height) * 255)))
                    var index = 0
                    while (index < points.length && points[index][0] < x) ++index
                    if (index > 0 && index < points.length && x - points[index - 1][0] >= 1 && points[index][0] - x >= 1) {
                        points.splice(index, 0, [x, y]); curve.selected = index
                    }
                }
                curve.editingPoints = points
                plot.requestPaint()
            }
            onPositionChanged: (mouse) => {
                if (!pressed || curve.selected < 0) return
                var points = curve.editingPoints.slice(), index = curve.selected
                var x = points[index][0]
                if (index > 0 && index < points.length - 1)
                    x = Math.max(points[index - 1][0] + 1, Math.min(points[index + 1][0] - 1, Math.round(mouse.x / width * 255)))
                points[index] = [x, Math.round(Math.max(0, Math.min(255, (1 - mouse.y / height) * 255)))]
                curve.editingPoints = points; plot.requestPaint()
                curve.model.setCurve(curve.channel, points)
            }
            onReleased: { curve.model.setCurve(curve.channel, curve.editingPoints); curve.model.endGesture(); plot.requestPaint() }
            onCanceled: curve.model.endGesture()
            Accessible.role: Accessible.Graphic; Accessible.name: "Tone curve. Click to add a point, drag to adjust, right-click to remove."
        }
    }
    Text {
        visible: !curve.parametric
        width: parent.width
        text: "Click to add a point · Right-click to remove"
        color: theme.fg3; font.pixelSize: 10
    }
    Column {
        visible: curve.parametric; width: parent.width
        Repeater {
            model: [{label: "Highlights", key: "curveHighlights"}, {label: "Lights", key: "curveLights"}, {label: "Darks", key: "curveDarks"}, {label: "Shadows", key: "curveShadows"}]
            delegate: RawColorSlider {
        editModel: curve.model
                required property var modelData
                width: parent.width; label: modelData.label
                enabled: curve.model.loaded && !curve.model.saving
                value: curve.model.values[modelData.key]
                onCommitted: (next) => curve.model.adjust(modelData.key, next)
            }
        }
    }
}
