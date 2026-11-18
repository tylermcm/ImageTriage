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
    property bool arrowColumn: false     // a column before the icon for an expand arrow (the folder tree), blank when `chevron` is ""
    property string chevron: ""
    width: parent ? parent.width : 280
    height: 32
    Rectangle { anchors.fill: parent; color: row.current ? Qt.alpha(theme.accent, 0.13) : Qt.alpha(theme.lift, hover.hovered && row.interactive ? 0.05 : 0) }
    Rectangle { visible: row.current; x: 0; y: 6; width: 2; height: parent.height - 12; radius: 1; color: theme.accent }
    Row {
        anchors.verticalCenter: parent.verticalCenter; x: (row.arrowColumn ? 8 : 16) + row.indent; spacing: row.arrowColumn ? 6 : 10
        Item { visible: row.arrowColumn; width: 12; height: 16; anchors.verticalCenter: parent.verticalCenter
               Icon { visible: row.chevron !== ""; name: row.chevron; size: 12; anchors.centerIn: parent; color: hover.hovered || row.current ? theme.fg2 : theme.fg3 } }
        Icon { visible: row.icon !== ""; name: row.icon; size: 16; color: row.current ? theme.fg : theme.fg2; anchors.verticalCenter: parent.verticalCenter }
        Text { text: row.text; color: row.muted ? theme.fg3 : (row.current || hover.hovered ? theme.fg : theme.fg2); font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter
               elide: Text.ElideRight
               width: Math.min(implicitWidth, Math.max(20, row.width - ((row.arrowColumn ? 8 + 18 : 16) + row.indent) - (row.icon !== "" ? 26 : 0) - (row.count !== "" ? cnt.implicitWidth + 16 + 12 : 16))) }
    }
    Text { id: cnt; visible: row.count !== ""; text: row.count; color: theme.fg3; font.pixelSize: 11; anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter }
    HoverHandler { id: hover; cursorShape: row.interactive ? Qt.PointingHandCursor : Qt.ArrowCursor }
}
