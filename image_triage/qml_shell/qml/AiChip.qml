import QtQuick
import QtQuick.Shapes
import QtQuick.Effects

// The AI button: an animated conic-gradient ring while it analyzes, a steady green one when results are ready.
Item {
    id: chip
    property real angle: 0
    property real idleAngle: 0           // the idle gradient turns slowly
    implicitHeight: 36
    implicitWidth: row.implicitWidth + 25
    NumberAnimation on idleAngle { running: cull.aiState === 0 && chip.visible; from: 0; to: 360; duration: 7000; loops: Animation.Infinite }
    NumberAnimation on angle { running: cull.aiState === 1; from: 0; to: 360; duration: 2200; loops: Animation.Infinite }

    // ::after: the same conic ramp, inset 3px, blurred 12px, half opacity, behind the chip while it analyzes
    Shape {
        x: 3; y: 3; width: chip.width - 6; height: chip.height - 6
        opacity: cull.aiState === 1 ? 0.5 : 0
        Behavior on opacity { NumberAnimation { duration: 400 } }
        preferredRendererType: Shape.CurveRenderer
        layer.enabled: opacity > 0
        layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 26; autoPaddingEnabled: true }
        ShapePath {
            strokeColor: "transparent"
            fillGradient: ConicalGradient {
                centerX: chip.width / 2 - 3; centerY: chip.height / 2 - 3; angle: 90 - chip.angle
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.15; color: "#d58ae8" }
                GradientStop { position: 0.35; color: theme.accent }
                GradientStop { position: 0.55; color: "transparent" }
                GradientStop { position: 1.0; color: "transparent" }
            }
            PathSvg { path: Icons.rect(0, 0, chip.width - 6, chip.height - 6, 11) }
        }
    }
    Rectangle { anchors.fill: parent; radius: 14; color: theme.s3 }
    Shape {   // idle ("Analyze"): a slowly turning conic-gradient outline at half opacity; it gives way to the ring while analyzing and to the green one when done
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        opacity: cull.aiState === 0 ? 0.5 : 0
        Behavior on opacity { NumberAnimation { duration: 350 } }
        ShapePath {
            fillRule: ShapePath.OddEvenFill; strokeColor: "transparent"
            fillGradient: ConicalGradient {
                centerX: chip.width / 2; centerY: chip.height / 2; angle: 90 - chip.idleAngle
                GradientStop { position: 0.0; color: theme.accent }
                GradientStop { position: 0.33; color: "#d58ae8" }
                GradientStop { position: 0.66; color: "#3ec7c0" }
                GradientStop { position: 1.0; color: theme.accent }
            }
            PathSvg { path: Icons.rect(0, 0, chip.width, chip.height, 14) + Icons.rect(1.5, 1.5, chip.width - 3, chip.height - 3, 12.5) }
        }
    }
    Shape {
        id: ring
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        opacity: cull.aiState === 1 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 350 } }
        ShapePath {
            fillRule: ShapePath.OddEvenFill; strokeColor: "transparent"
            fillGradient: ConicalGradient {
                // CSS runs clockwise from the angle, Qt counter-clockwise from 3 o'clock: mirrored stops, start angle 90 - a
                centerX: chip.width / 2; centerY: chip.height / 2; angle: 90 - chip.angle
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.15; color: "#d58ae8" }
                GradientStop { position: 0.35; color: theme.accent }
                GradientStop { position: 0.55; color: "transparent" }
                GradientStop { position: 1.0; color: "transparent" }
            }
            PathSvg { path: Icons.rect(0, 0, chip.width, chip.height, 14) + Icons.rect(1.5, 1.5, chip.width - 3, chip.height - 3, 12.5) }
        }
    }
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        opacity: cull.aiState === 2 ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 350 } }
        ShapePath {
            fillRule: ShapePath.OddEvenFill; strokeColor: "transparent"
            fillGradient: ConicalGradient {
                centerX: chip.width / 2; centerY: chip.height / 2; angle: 90
                GradientStop { position: 0.0; color: theme.win }
                GradientStop { position: 0.5; color: "#3ec7c0" }
                GradientStop { position: 1.0; color: theme.win }
            }
            PathSvg { path: Icons.rect(0, 0, chip.width, chip.height, 14) + Icons.rect(1.5, 1.5, chip.width - 3, chip.height - 3, 12.5) }
        }
    }
    Row {
        id: row
        x: 11; anchors.verticalCenter: parent.verticalCenter
        spacing: 8
        Icon { name: "spark"; size: 17; color: theme.accentFg; anchors.verticalCenter: parent.verticalCenter }
        Text {
            text: cull.aiState === 0 ? "Analyze" : cull.aiState === 1 ? "Analyzing…" : "AI ready"
            color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter
        }
    }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    TapHandler {
        onTapped: cull.openCull()
    }
    Accessible.role: Accessible.Button
    Accessible.name: "AI analysis"
}
