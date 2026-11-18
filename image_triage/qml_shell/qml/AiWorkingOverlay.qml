import QtQuick

// An AI tool at work on the photo (AI Denoise, Sky Replacement, AI Erase, Light Depth, Atmosphere, Bokeh, AI masks), in the manner
// of Luminar's: the photo dims a little, a soft band of light sweeps across it again and again, a few sparkles come and go, and a
// pill says which tool is working and what it is doing now. Laid exactly over the painted photo; clicks pass through except on Cancel.
Item {
    id: ov
    property bool active: false
    property string title: ""
    property string step: ""
    property bool canCancel: false
    signal cancelRequested()

    opacity: active ? 1 : 0
    visible: opacity > 0.01
    Behavior on opacity { NumberAnimation { duration: active ? 260 : 420; easing.type: Easing.OutCubic } }
    readonly property bool running: visible
    readonly property color glowA: "#7aa2ff"
    readonly property color glowB: "#d58ae8"

    Item {
        id: field
        anchors.fill: parent
        clip: true

        // a gentle dimming that breathes, so the photo reads as "being worked on" even between sweeps
        Rectangle {
            anchors.fill: parent; color: "#0b0d14"
            SequentialAnimation on opacity {
                running: ov.running; loops: Animation.Infinite
                NumberAnimation { from: 0.18; to: 0.32; duration: 1400; easing.type: Easing.InOutSine }
                NumberAnimation { from: 0.32; to: 0.18; duration: 1400; easing.type: Easing.InOutSine }
            }
        }

        // the sweeping band: a wide diagonal gradient, blue into violet, moving from left to right
        Item {
            id: band
            readonly property real span: Math.max(field.width, field.height) * 0.75
            width: span; height: Math.hypot(field.width, field.height) * 1.6
            y: (field.height - height) / 2
            rotation: 18
            property real travel: 0
            x: -span * 0.9 + travel * (field.width + span * 0.8)
            SequentialAnimation on travel {
                running: ov.running; loops: Animation.Infinite
                NumberAnimation { from: 0; to: 1; duration: 2100; easing.type: Easing.InOutSine }
                PauseAnimation { duration: 250 }
            }
            Rectangle {
                anchors.fill: parent
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.30; color: Qt.alpha(ov.glowA, 0.14) }
                    GradientStop { position: 0.47; color: Qt.alpha(ov.glowB, 0.38) }
                    GradientStop { position: 0.52; color: Qt.alpha("#ffffff", 0.30) }
                    GradientStop { position: 0.58; color: Qt.alpha(ov.glowB, 0.30) }
                    GradientStop { position: 0.72; color: Qt.alpha(ov.glowA, 0.12) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
            }
        }

        // sparkles that twinkle at scattered places (placed by a fixed pattern, so they do not jump about on every redraw)
        Repeater {
            model: 9
            delegate: Item {
                required property int index
                readonly property real fx: [0.18, 0.72, 0.41, 0.86, 0.29, 0.63, 0.12, 0.52, 0.80][index]
                readonly property real fy: [0.26, 0.18, 0.62, 0.55, 0.80, 0.38, 0.47, 0.12, 0.84][index]
                readonly property real size: 9 + (index % 3) * 4
                x: fx * field.width - size / 2; y: fy * field.height - size / 2
                width: size; height: size
                opacity: 0
                Icon { anchors.centerIn: parent; size: parent.size; color: index % 2 ? ov.glowB : "#ffffff"
                       path: "M12 2.5l2.2 7.3 7.3 2.2-7.3 2.2L12 21.5l-2.2-7.3L2.5 12l7.3-2.2z"; filled: true }
                SequentialAnimation on opacity {
                    running: ov.running; loops: Animation.Infinite
                    PauseAnimation { duration: (index * 397) % 2100 }
                    NumberAnimation { to: 0.9; duration: 380; easing.type: Easing.OutQuad }
                    NumberAnimation { to: 0; duration: 700; easing.type: Easing.InQuad }
                    PauseAnimation { duration: 900 + (index * 211) % 1300 }
                }
                SequentialAnimation on scale {
                    running: ov.running; loops: Animation.Infinite
                    PauseAnimation { duration: (index * 397) % 2100 }
                    NumberAnimation { from: 0.4; to: 1.1; duration: 380; easing.type: Easing.OutBack }
                    NumberAnimation { to: 0.6; duration: 700 }
                    PauseAnimation { duration: 900 + (index * 211) % 1300 }
                }
            }
        }
    }

    // what is working, and what it is doing now
    Rectangle {
        id: pill
        objectName: "aiWorkingPill"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: Math.min(28, parent.height * 0.06)
        width: Math.min(parent.width - 24, row.implicitWidth + 28); height: 40; radius: 20
        color: Qt.rgba(0.07, 0.08, 0.11, 0.86)
        border.width: 1; border.color: Qt.alpha(ov.glowB, 0.45)
        Row {
            id: row
            anchors.centerIn: parent; spacing: 10
            Item {
                width: 18; height: 18; anchors.verticalCenter: parent.verticalCenter
                Icon { anchors.centerIn: parent; size: 18; color: ov.glowB; path: "M12 2.5l2.2 7.3 7.3 2.2-7.3 2.2L12 21.5l-2.2-7.3L2.5 12l7.3-2.2z"; filled: true
                       RotationAnimator on rotation { running: ov.running; from: 0; to: 360; duration: 2600; loops: Animation.Infinite }
                       SequentialAnimation on scale { running: ov.running; loops: Animation.Infinite
                           NumberAnimation { from: 0.85; to: 1.1; duration: 650; easing.type: Easing.InOutSine }
                           NumberAnimation { from: 1.1; to: 0.85; duration: 650; easing.type: Easing.InOutSine } } }
            }
            Text { anchors.verticalCenter: parent.verticalCenter; text: ov.title; color: "#ffffff"; font.pixelSize: 13; font.weight: Font.DemiBold }
            Text { anchors.verticalCenter: parent.verticalCenter; visible: ov.step !== ""; text: ov.step; color: Qt.rgba(1, 1, 1, 0.72); font.pixelSize: 12
                   elide: Text.ElideRight; width: Math.min(implicitWidth, Math.max(60, ov.width - 260)) }
            Text {
                objectName: "aiWorkingCancel"
                visible: ov.canCancel; anchors.verticalCenter: parent.verticalCenter
                text: "Cancel"; color: ov.glowA; font.pixelSize: 12; font.weight: Font.Medium
                TapHandler { onTapped: ov.cancelRequested() }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                Accessible.role: Accessible.Button; Accessible.name: "Cancel " + ov.title
                Accessible.onPressAction: ov.cancelRequested()
            }
        }
    }
    Accessible.role: Accessible.ProgressBar
    Accessible.name: title + (step !== "" ? ": " + step : "")
}
