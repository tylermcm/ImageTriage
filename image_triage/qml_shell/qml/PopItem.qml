import QtQuick

// One row in a popover: label, optional shortcut, and either a tick (single choice) or a switch (toggle).
Item {
    id: row
    property string label
    property string kbd: ""
    property string icon: ""
    property bool checked: false
    property string mode: "none"       // "none" | "tick" | "switch"
    signal clicked()
    width: parent ? parent.width : 200
    height: 34
    Rectangle { anchors.fill: parent; radius: 10; color: Qt.alpha(theme.lift, hover.hovered ? 0.08 : 0) }
    Row {
        anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: 10; spacing: 10
        Icon { visible: row.icon !== ""; name: row.icon; color: theme.fg2; size: 16; anchors.verticalCenter: parent.verticalCenter }
        Text { text: row.label; color: hover.hovered ? theme.fg : theme.fg2; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
    }
    Text {
        visible: row.kbd !== ""; text: row.kbd; color: theme.fg3; font.pixelSize: 10; font.weight: Font.Medium
        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
        padding: 2; leftPadding: 5; rightPadding: 5
        Rectangle { anchors.fill: parent; z: -1; radius: 5; color: "transparent"; border.color: theme.line }
    }
    Icon {
        visible: row.mode === "tick"; name: "check"; size: 14; color: theme.accentFg; opacity: row.checked ? 1 : 0
        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
    }
    Rectangle {
        visible: row.mode === "switch"
        width: 30; height: 17; radius: 9; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
        color: row.checked ? Qt.alpha(theme.accent, 0.6) : theme.s3
        Behavior on color { ColorAnimation { duration: 200 } }
        Rectangle {
            width: 13; height: 13; radius: 7; y: 2; x: row.checked ? 15 : 2; color: row.checked ? "white" : theme.fg2
            Behavior on x { SpringAnimation { spring: 5; damping: 0.4; epsilon: 0.25 } }
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: row.clicked() }
}
