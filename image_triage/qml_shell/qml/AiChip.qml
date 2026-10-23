import QtQuick
import QtQuick.Shapes

// The AI button: an animated conic-gradient ring while it analyzes, a steady green one when results are ready.
Item {
    id: chip
    property real angle: 0
    implicitHeight: 36
    implicitWidth: row.implicitWidth + 25
    NumberAnimation on angle { running: UiState.ai === 1; from: 0; to: 360; duration: 2200; loops: Animation.Infinite }

    Rectangle { anchors.fill: parent; radius: 14; color: theme.s3 }
    Shape {
        id: ring
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        opacity: UiState.ai === 1 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 350 } }
        ShapePath {
            fillRule: ShapePath.OddEvenFill; strokeColor: "transparent"
            fillGradient: ConicalGradient {
                centerX: chip.width / 2; centerY: chip.height / 2; angle: chip.angle
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.45; color: "transparent" }
                GradientStop { position: 0.65; color: theme.accent }
                GradientStop { position: 0.85; color: "#d58ae8" }
                GradientStop { position: 1.0; color: "transparent" }
            }
            PathSvg { path: Icons.rect(0, 0, chip.width, chip.height, 14) + Icons.rect(1.5, 1.5, chip.width - 3, chip.height - 3, 12.5) }
        }
    }
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        opacity: UiState.ai === 2 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 350 } }
        ShapePath {
            fillRule: ShapePath.OddEvenFill; strokeColor: "transparent"
            fillGradient: ConicalGradient {
                centerX: chip.width / 2; centerY: chip.height / 2; angle: 0
                GradientStop { position: 0.0; color: theme.win }
                GradientStop { position: 0.5; color: "#3ec7c0" }
                GradientStop { position: 1.0; color: theme.win }
            }
            PathSvg { path: Icons.rect(0, 0, chip.width, chip.height, 14) + Icons.rect(1.5, 1.5, chip.width - 3, chip.height - 3, 12.5) }
        }
    }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 8
        Icon { name: "spark"; size: 17; color: theme.accentFg; anchors.verticalCenter: parent.verticalCenter }
        Text {
            text: UiState.ai === 0 ? "Analyze" : UiState.ai === 1 ? "Analyzing…" : "AI ready"
            color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter
        }
    }
    Timer { id: done; interval: 2600; onTriggered: UiState.ai = 2 }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    TapHandler {
        onTapped: {
            if (UiState.ai === 1) return;
            if (UiState.ai === 2) { UiState.ai = 0; return; }
            UiState.ai = 1; done.restart();
        }
    }
    Accessible.role: Accessible.Button
    Accessible.name: "AI analysis"
}
