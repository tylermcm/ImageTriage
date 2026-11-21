import QtQuick

Item {
    id: b
    property string label
    property string iconPath: ""
    property bool primary: false
    property bool lifted: false
    property bool flat: false
    signal clicked()
    implicitHeight: 36; height: 36
    // the width the icon and label need; narrower than that (a narrow panel) the label goes and the icon stays, centred and whole
    readonly property real fullWidth: (iconPath !== "" ? 16 : 0) + (iconPath !== "" && label !== "" ? 7 : 0) + (label !== "" ? labelText.implicitWidth : 0) + 28
    readonly property bool compact: label !== "" && iconPath !== "" && width < fullWidth
    implicitWidth: fullWidth
    clip: true
    readonly property alias hovered: hov.hovered
    scale: tap.pressed ? 0.97 : 1
    Behavior on scale { NumberAnimation { duration: 90 } }
    Rectangle {
        anchors.fill: parent; radius: 12
        color: b.primary ? (hov.hovered ? Qt.tint(theme.accent, Qt.alpha(theme.lift, 0.10)) : theme.accent)
                         : b.flat ? Qt.alpha(theme.lift, hov.hovered || b.lifted ? 0.08 : 0)
                                  : (hov.hovered || b.lifted ? Qt.tint(theme.s3, Qt.alpha(theme.lift, 0.06)) : theme.s3)
        border.color: b.primary || b.flat ? "transparent" : theme.lineSoft
    }
    Row {
        id: row; anchors.centerIn: parent; spacing: 7
        PdIcon { visible: b.iconPath !== ""; path: b.iconPath; size: 16; strokeWidth: 2; color: b.primary ? theme.onAccent : theme.fg; anchors.verticalCenter: parent.verticalCenter }
        Text { id: labelText; visible: b.label !== "" && !b.compact; text: b.label; color: b.primary ? theme.onAccent : theme.fg; font.pixelSize: 13; font.weight: b.primary ? Font.Medium : Font.Normal; anchors.verticalCenter: parent.verticalCenter }
    }
    HoverHandler { id: hov; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; onTapped: b.clicked() }
    Accessible.role: Accessible.Button; Accessible.name: b.label
}
