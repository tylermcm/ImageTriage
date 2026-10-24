import QtQuick

// The ⋯ menu of the PocketDrop page: the engine's own menu (Ui::showMenu) as a Popover, each entry a named command.
Popover {
    id: menu
    property Item panel
    name: "pdmenu"
    placement: 2
    minWidth: 247
    readonly property var d: pocketDrop.st
    readonly property var m: d.menu || ({})
    readonly property bool hasLink: !!d.hasLink

    component Divider: Item { width: parent ? parent.width : 100; height: 13; Rectangle { x: 4; y: 6; width: parent.width - 8; height: 1; color: theme.lineSoft } }

    component Entry: Item {
        id: e
        property string label
        property string kbd: ""
        property string icon: ""
        property bool checked: false
        property string mode: "none"
        property bool enabledItem: true
        signal activated()
        width: parent ? parent.width : 200; height: 32
        opacity: enabledItem ? 1 : 0.4
        PopItem { width: parent.width; height: 32; label: e.label; kbd: e.kbd; icon: e.icon; checked: e.checked; mode: e.mode; enabled: e.enabledItem
            onClicked: { if (e.enabledItem) { e.activated() } } }
    }

    Entry { label: "Add files…"; icon: "plus"; kbd: "Ctrl O"; onActivated: { menu.close(); panel.chooseFiles() } }
    Entry { label: "Add folder…"; onActivated: { menu.close(); panel.chooseFolder() } }
    Entry { label: "Paste"; kbd: "Ctrl V"; onActivated: { menu.close(); pocketDrop.command("paste") } }
    Divider {}
    Entry { label: menu.d.copyLabel || "Copy link"; kbd: "Ctrl C"; enabledItem: menu.hasLink; onActivated: { menu.close(); pocketDrop.command("copy") } }
    Entry { label: "Open link in browser"; enabledItem: !!menu.d.canOpen; onActivated: { menu.close(); pocketDrop.command("open_link") } }
    Entry { label: "New link (the old one stops working)"; onActivated: { menu.close(); pocketDrop.command("new_link") } }
    Divider {}
    Entry { label: "Open received files folder"; onActivated: { menu.close(); pocketDrop.command("menu", "open_inbox") } }
    Entry { label: "Change received files folder…"; onActivated: { menu.close(); panel.chooseInbox() } }
    Divider {}
    PopHeader { caption: "When sharing" }
    Entry { label: "Stop sharing after a download"; mode: "tick"; checked: !!menu.m.burn; onActivated: pocketDrop.command("menu", "burn") }
    Entry { label: "Keep Anywhere tunnel ready"; mode: "tick"; checked: !!menu.m.auto; onActivated: pocketDrop.command("menu", "auto_any") }
    Entry { label: "Warn before using Anywhere"; mode: "tick"; checked: !!menu.m.warn; onActivated: pocketDrop.command("menu", "warn_any") }
    Divider {}
    PopHeader { caption: "Network address" }
    Repeater {
        model: menu.m.addrs || []
        delegate: Item {
            required property var modelData
            required property int index
            width: parent.width; height: 32
            Rectangle { anchors.fill: parent; radius: 10; color: Qt.alpha(theme.lift, ah.hovered ? 0.08 : 0) }
            Text { x: 10; anchors.verticalCenter: parent.verticalCenter; text: modelData.ip; color: ah.hovered ? theme.fg : theme.fg2; font.pixelSize: 13 }
            Text { anchors.right: tk.left; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; text: modelData.adapter; color: theme.fg3; font.pointSize: UiState.pt(11.5) }
            Icon { id: tk; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; name: "check"; size: 14; strokeWidth: 2.2
                color: theme.accentFg; opacity: modelData.selected ? 1 : 0 }
            HoverHandler { id: ah; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: pocketDrop.command("addr", String(index)) }
        }
    }
    Divider {}
    Entry { label: "Close Anywhere tunnel"; enabledItem: !!menu.m.tunnelOpen; onActivated: { menu.close(); pocketDrop.command("menu", "stop_tunnel") } }
    Entry { label: "About PocketDrop"; onActivated: { menu.close(); pocketDrop.command("menu", "about") } }
}
