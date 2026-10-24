import QtQuick

// The top of a reference window: a rounded tile with an icon, the title, a sentence under it, and a close button at the right.
Item {
    id: hd
    property string title
    property string sub
    property string iconPath
    property alias extra: slot.data         // something between the text and the close button (the shortcuts search)
    signal closeRequested()
    height: 22 + Math.max(42, col.implicitHeight) + 10
    Rectangle { x: 26; y: 22; width: 42; height: 42; radius: 13; color: Qt.tint(theme.s2, Qt.alpha(theme.accent, 0.16)); border.color: Qt.alpha(theme.accent, 0.28)
        Icon { anchors.centerIn: parent; path: hd.iconPath; size: 22; strokeWidth: 1.7; color: theme.accentFg } }
    Column {
        id: col
        x: 82; y: 22; width: Math.min(540, hd.width - 82 - 26 - slot.width - 50)
        Text { text: hd.title; color: theme.fg; font.pixelSize: 18; font.weight: Font.DemiBold; font.letterSpacing: -0.27; lineHeight: 23.4; lineHeightMode: Text.FixedHeight }
        Text { width: parent.width; topPadding: 4; text: hd.sub; color: theme.fg3; wrapMode: Text.WordWrap; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
    }
    Item { id: slot; anchors.right: close.left; anchors.rightMargin: 6; y: 22; width: childrenRect.width; height: 34 }
    Item {
        id: close
        x: hd.width - 26 - 30 + 6 - 0; y: 22; width: 30; height: 30
        Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, ch.hovered ? 0.08 : 0) }
        Icon { anchors.centerIn: parent; name: "x"; size: 16; strokeWidth: 1.8; color: ch.hovered ? theme.fg : theme.fg3 }
        HoverHandler { id: ch; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: hd.closeRequested() }
        Accessible.role: Accessible.Button; Accessible.name: "Close"
    }
}
