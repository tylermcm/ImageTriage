import QtQuick

// One line of the menu: a label, an optional shortcut hint or tick. Hosts use it for their own entries (see PocketDropView.menuExtras).
Item {
    id: e
    property var popup: null           // the menu to close after a tap (the view's menuPopup)
    property string label
    property string kbd: ""
    property bool tick: false
    property bool ticked: false
    property bool enabledItem: true
    property bool stay: false          // keep the menu open (settings)
    signal activated()
    width: parent ? parent.width : 200; height: visible ? 32 : 0
    opacity: enabledItem ? 1 : 0.4
    Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, hov.hovered && e.enabledItem ? 0.08 : 0) }
    Text { x: 10; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 20 - (e.tick ? 22 : 0) - (e.kbd !== "" ? 50 : 0); elide: Text.ElideRight
        text: e.label; color: theme.fg; font.pixelSize: 13 }
    Text { visible: e.kbd !== ""; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; text: e.kbd; color: theme.fg3; font.pixelSize: 11 }
    PdIcon { visible: e.tick && e.ticked; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
        path: "M4.5 12.5l4.5 4.5 10.5-10.5"; size: 15; strokeWidth: 2.4; color: theme.accentFg }
    HoverHandler { id: hov; cursorShape: e.enabledItem ? Qt.PointingHandCursor : Qt.ArrowCursor }
    TapHandler { enabled: e.enabledItem; onTapped: { if (!e.stay && e.popup) e.popup.close(); e.activated() } }
    Accessible.role: Accessible.MenuItem; Accessible.name: e.label; Accessible.checkable: e.tick; Accessible.checked: e.ticked
}
