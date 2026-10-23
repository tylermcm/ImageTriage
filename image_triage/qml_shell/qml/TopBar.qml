import QtQuick
import QtQuick.Shapes

// The slim application bar: brand icon, menus, the command search, theme and inspector toggles, window controls.
Rectangle {
    id: top
    height: 42
    color: theme.s1
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }

    readonly property real brandW: UiState.topAligned ? 72 : 36

    Item {
        id: brand
        width: top.brandW; height: parent.height
        Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
        Shape {
            anchors.centerIn: parent; width: 18; height: 18; preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: ConicalGradient {
                    centerX: 9; centerY: 9; angle: 210
                    GradientStop { position: 0.0; color: theme.accent }
                    GradientStop { position: 0.5; color: "#c58ae6" }
                    GradientStop { position: 1.0; color: theme.accent }
                }
                PathSvg { path: Icons.rect(0, 0, 18, 18, 6) }
            }
        }
        Accessible.name: "Image Triage"
        Accessible.role: Accessible.Graphic
    }
    Row {
        id: menus
        x: UiState.topAligned ? brand.width + 8 : brand.width + 4
        Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
        anchors.verticalCenter: parent.verticalCenter
        visible: top.width > 900
        spacing: 2
        Repeater {
            model: ["File", "Review", "View", "AI", "Help"]
            delegate: Item {
                required property string modelData
                width: lbl.implicitWidth + 18; height: 28
                Rectangle { anchors.fill: parent; radius: 7; color: Qt.alpha(theme.lift, h.hovered ? 0.08 : 0) }
                Text { id: lbl; anchors.centerIn: parent; text: modelData; color: h.hovered ? theme.fg : theme.fg2; font.pixelSize: 13 }
                HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
                Accessible.role: Accessible.MenuItem; Accessible.name: modelData
            }
        }
    }
    Rectangle {
        id: cmd
        anchors.centerIn: parent
        width: Math.min(460, parent.width - brand.width - rightRow.width - 40); height: 28; radius: 9
        color: ch.hovered ? theme.s2 : theme.base; border.color: ch.hovered ? theme.line : theme.lineSoft
        Behavior on color { ColorAnimation { duration: 150 } }
        Row {
            anchors.verticalCenter: parent.verticalCenter; x: 10; spacing: 8
            Icon { name: "search"; size: 16; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
            Text { text: "Search photos, or run a command"; color: theme.fg3; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
            text: "Ctrl K"; color: theme.fg3; font.pixelSize: 10; leftPadding: 5; rightPadding: 5
            Rectangle { anchors.fill: parent; z: -1; radius: 5; color: "transparent"; border.color: theme.line }
        }
        HoverHandler { id: ch; cursorShape: Qt.PointingHandCursor }
        Accessible.role: Accessible.Button; Accessible.name: "Search photos or run a command"
    }
    Row {
        id: rightRow
        anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 2
        Repeater {
            model: [{ i: "sun", tip: "Switch light or dark theme" }, { i: "inspector", tip: "Show or hide the inspector" }]
            delegate: Item {
                required property var modelData
                width: 32; height: 32
                readonly property bool on: modelData.i === "inspector" && UiState.insp
                Rectangle { anchors.fill: parent; radius: 9; color: on ? Qt.alpha(theme.accent, 0.16) : Qt.alpha(theme.lift, bh.hovered ? 0.08 : 0) }
                Icon { anchors.centerIn: parent; name: modelData.i; size: 18; color: on ? theme.accentFg : (bh.hovered ? theme.fg : theme.fg2) }
                HoverHandler { id: bh; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: { if (modelData.i === "sun") theme.light = !theme.light; else UiState.insp = !UiState.insp } }
                Accessible.role: Accessible.Button; Accessible.name: modelData.tip
            }
        }
        Row {
            visible: top.width > 900
            leftPadding: 6
            Repeater {
                model: ["winMin", "winMax", "winClose"]
                delegate: Item { required property string modelData; width: 38; height: 42; Icon { anchors.centerIn: parent; name: modelData; size: 12; color: theme.fg3 } }
            }
        }
    }
}
