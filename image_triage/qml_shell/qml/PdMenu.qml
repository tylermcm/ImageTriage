import QtQuick
import QtQuick.Controls

// The ⋯ menu: actions first, then a few settings. Everything is a named command on `pocketDrop`; `host` adds its own entries.
Popup {
    id: menu
    property var d: ({})
    property Item view
    property alias extras: extraSlot.data
    readonly property var m: d.menu || ({})
    readonly property bool hasLink: !!d.hasLink
    padding: 6
    width: 268
    height: Math.min(col.implicitHeight + 12, (parent ? parent.height : 600) - 70)
    modal: false; focus: true; closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle { radius: 14; color: theme.s1; border.color: theme.line }

    component Entry: PdMenuEntry { popup: menu; width: col.width }
    component Heading: Text {
        width: col.width; height: visible ? 26 : 0; leftPadding: 10; topPadding: 8
        color: theme.fg3; font.pixelSize: 11; font.weight: Font.Medium; font.letterSpacing: 0.6
    }
    component Divider: Item { width: col.width; height: 9; Rectangle { x: 6; y: 4; width: parent.width - 12; height: 1; color: theme.lineSoft } }

    contentItem: Flickable {
        clip: true; contentHeight: col.implicitHeight; boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        Column {
            id: col
            width: menu.width - 12
            Heading { text: "LINK" }
            Entry { label: menu.d.copyLabel || "Copy link"; kbd: "Ctrl C"; enabledItem: menu.hasLink; onActivated: pocketDrop.command("copy") }
            Entry { label: "Open link in browser"; enabledItem: !!menu.d.canOpen; onActivated: pocketDrop.command("open_link") }
            Entry { label: "New link (the old one stops working)"; onActivated: pocketDrop.command("new_link") }

            Heading { text: "RECEIVED" }
            Entry { label: "Open received files folder"; onActivated: pocketDrop.command("menu", "open_inbox") }
            Entry { label: "Change received files folder…"; onActivated: view.chooseInbox() }
            Item { id: historyBox; width: col.width; height: hist.height
                Column { id: hist; width: parent.width
                    Repeater {
                        model: (menu.m.history || []).slice(0, 3)
                        delegate: Item {
                            required property var modelData
                            readonly property bool sentOut: modelData.kind === "s"
                            width: col.width; height: 30; opacity: sentOut ? 0.6 : 1
                            Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, hv.hovered && !sentOut ? 0.08 : 0) }
                            Text { x: 10; anchors.verticalCenter: parent.verticalCenter; width: parent.width - when.width - 28; elide: Text.ElideRight
                                text: (sentOut ? "→ " : modelData.kind === "n" ? "✎ " : "↓ ") + modelData.title + "  ·  " + modelData.device
                                color: theme.fg2; font.pixelSize: 12 }
                            Text { id: when; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; text: modelData.when; color: theme.fg3; font.pixelSize: 11 }
                            HoverHandler { id: hv; cursorShape: sentOut ? Qt.ArrowCursor : Qt.PointingHandCursor }
                            TapHandler { enabled: !sentOut; onTapped: { menu.close(); pocketDrop.command("history_open", String(modelData.i)) } }
                        } } } }
            Entry { label: "Clear transfer history"; visible: (menu.m.history || []).length > 0; onActivated: pocketDrop.command("menu", "history_clear") }

            Heading { text: "NEARBY PCS" }
            Entry { label: "Send files to another PC…"; onActivated: pocketDrop.command("pc_send") }
            Entry { label: "Receive files from another PC…"; onActivated: pocketDrop.command("pc_receive") }

            Heading { text: "SETTINGS" }
            Entry { label: "Stop sharing after a download"; tick: true; ticked: !!menu.m.burn; stay: true; onActivated: pocketDrop.command("menu", "burn") }
            Entry { label: "Copy phone notes to the clipboard"; tick: true; ticked: !!menu.m.noteClip; stay: true; onActivated: pocketDrop.command("menu", "note_clip") }
            Entry { label: "Warn before using Anywhere"; tick: true; ticked: !!menu.m.warn; stay: true; onActivated: pocketDrop.command("menu", "warn_any") }
            Entry { label: "Always on top"; visible: menu.m.topmost !== undefined; tick: true; ticked: !!menu.m.topmost; stay: true; onActivated: pocketDrop.command("menu", "topmost") }
            Item { id: extraSlot; width: col.width; height: childrenRect.height }
            Entry { label: "Forget remembered phones (" + (menu.m.phones || 0) + ")"; visible: (menu.m.phones || 0) > 0; onActivated: pocketDrop.command("menu", "forget_phones") }

            Heading { text: "NETWORK"; visible: (menu.m.addrs || []).length > 1 || !!menu.m.tunnelOpen }
            Repeater {
                model: (menu.m.addrs || []).length > 1 ? menu.m.addrs : []
                delegate: Entry {
                    required property var modelData
                    required property int index
                    label: modelData.ip + "   " + modelData.adapter; tick: true; ticked: !!modelData.selected; stay: true
                    onActivated: pocketDrop.command("addr", String(index))
                }
            }
            Entry { label: "Close Anywhere tunnel"; visible: !!menu.m.tunnelOpen; onActivated: pocketDrop.command("menu", "stop_tunnel") }
            Divider {}
            Entry { label: "About PocketDrop"; onActivated: pocketDrop.command("menu", "about") }
        }
    }
}
