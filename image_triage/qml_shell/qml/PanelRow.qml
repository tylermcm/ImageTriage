import QtQuick

// One row of a side-panel list: icon, label, trailing count; the current row gets an accent bar.
Item {
    id: row
    property string icon
    property string text
    property string count
    property bool current: false
    property int indent: 0
    property bool interactive: true
    property bool muted: false           // the whole row in the quiet text colour (a status line rather than a list item)
    width: parent ? parent.width : 280
    height: 32
    Rectangle { anchors.fill: parent; color: row.current ? Qt.alpha(theme.accent, 0.13) : Qt.alpha(theme.lift, hover.hovered && row.interactive ? 0.05 : 0) }
    Rectangle { visible: row.current; x: 0; y: 6; width: 2; height: parent.height - 12; radius: 1; color: theme.accent }
    Row {
        anchors.verticalCenter: parent.verticalCenter; x: 16 + row.indent; spacing: 10
        Icon { visible: row.icon !== ""; name: row.icon; size: 16; color: row.current ? theme.fg : theme.fg2; anchors.verticalCenter: parent.verticalCenter }
        Text { text: row.text; color: row.muted ? theme.fg3 : (row.current || hover.hovered ? theme.fg : theme.fg2); font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter
               elide: Text.ElideRight
               width: Math.min(implicitWidth, Math.max(20, row.width - (16 + row.indent) - (row.icon !== "" ? 26 : 0) - (row.count !== "" ? cnt.implicitWidth + 16 + 12 : 16))) }
    }
    Text { id: cnt; visible: row.count !== ""; text: row.count; color: theme.fg3; font.pixelSize: 11; anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter }
    HoverHandler { id: hover; cursorShape: row.interactive ? Qt.PointingHandCursor : Qt.ArrowCursor }
}
