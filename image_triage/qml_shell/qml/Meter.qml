import QtQuick

// A thin horizontal meter. A multi-colour ramp spans the whole track (the mockup sets background-size to 1/value), so a short fill
// shows only the first part of the ramp.
Item {
    id: m
    property real value: 0               // 0..1
    property var stops: [theme.accent]   // one colour = solid; three = evenly spaced ramp across the track
    property real threshold: -1
    implicitHeight: 4

    readonly property real v: Math.max(0, Math.min(1, value))
    function lerp(a, b, t) { a = Qt.color(a); b = Qt.color(b); return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t, 1) }
    function at(t) {                      // ramp colour at a track fraction
        var n = stops.length - 1, k = Math.min(n - 1, Math.floor(t * n))
        return lerp(stops[k], stops[k + 1], t * n - k)
    }
    // the ramp as seen through the fill: [position in fill, colour]
    readonly property var ramp: {
        if (stops.length < 2 || v <= 0) return [[0, stops[0]], [1, stops[0]]]
        var out = [[0, stops[0]]], n = stops.length - 1
        for (var i = 1; i < n; i++) if (i / n < v) out.push([i / n / v, stops[i]])
        out.push([1, at(v)])
        return out
    }

    Rectangle { anchors.fill: parent; radius: height / 2; color: theme.s3 }
    Rectangle {
        height: parent.height; radius: height / 2
        width: parent.width * m.v
        color: m.stops[0]
        gradient: m.stops.length > 1 ? grad : null
        Gradient {
            id: grad
            orientation: Gradient.Horizontal
            GradientStop { position: m.ramp[0][0]; color: m.ramp[0][1] }
            GradientStop { position: m.ramp.length > 2 ? m.ramp[1][0] : 0.5; color: m.ramp.length > 2 ? m.ramp[1][1] : m.lerp(m.ramp[0][1], m.ramp[m.ramp.length - 1][1], 0.5) }
            GradientStop { position: 1.0; color: m.ramp[m.ramp.length - 1][1] }
        }
        Behavior on width { NumberAnimation { duration: 800; easing.type: Easing.OutCubic } }
    }
    Rectangle { visible: m.threshold >= 0; x: parent.width * m.threshold; y: -2; width: 1; height: parent.height + 4; color: Qt.alpha(theme.fg3, 0.5) }
}
