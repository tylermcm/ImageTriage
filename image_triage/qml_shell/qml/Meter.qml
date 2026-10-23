import QtQuick

// A thin horizontal meter with a gradient fill (drive capacity, quality checks).
Item {
    id: m
    property real value: 0               // 0..1
    property color from: theme.accent
    property color to: theme.accent
    property real threshold: -1
    implicitHeight: 6
    Rectangle { anchors.fill: parent; radius: height / 2; color: theme.s3 }
    Rectangle {
        height: parent.height; radius: height / 2
        width: parent.width * Math.max(0, Math.min(1, m.value))
        gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: m.from } GradientStop { position: 1; color: m.to } }
        Behavior on width { NumberAnimation { duration: 800; easing.type: Easing.OutCubic } }
    }
    Rectangle { visible: m.threshold >= 0; x: parent.width * m.threshold; y: -2; width: 1; height: parent.height + 4; color: Qt.alpha(theme.fg3, 0.5) }
}
