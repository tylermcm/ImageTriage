import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

// Safe Trash: the photos deleted with "Safe Trash (Undoable)", on this computer and in the app's recycle folders on connected
// removable drives. Each row is one photo with everything that belongs to it. The model is safe_trash_model.py (`safeTrash`).
// Tab moves through the rows and the buttons, Space ticks a row, Escape closes. Deleting for good asks first.
Backdrop {
    id: root
    objectName: "safeTrashWindow"
    shown: safeTrash.open
    onDismissed: safeTrash.close()
    onShownChanged: if (shown) forceActiveFocus()
    z: 106

    readonly property real winW: Math.min(860, width - 64)
    readonly property real winH: Math.min(680, height - 72)
    readonly property bool idle: !safeTrash.busy && !safeTrash.loading
    readonly property int picked: safeTrash.selectedCount
    function photos(n) { return n + (n === 1 ? " photo" : " photos") }
    function localPath(url) { return url.toString().replace(/^file:\/\/\/?/, Qt.platform.os === "windows" ? "" : "/") }

    property bool purgeAll: false
    property int pendingLimit: 0
    ConfirmCard {
        id: purgeCard; objectName: "safeTrashPurgeCard"; warn: true; cancelText: "Cancel"
        onAccepted: safeTrash.purge(root.purgeAll)
        onClosed: root.forceActiveFocus()
    }
    ConfirmCard {
        id: limitCard; objectName: "safeTrashLimitCard"; warn: true; cancelText: "Cancel"; confirmText: "Set limit and delete"
        onAccepted: safeTrash.setLimit(root.pendingLimit)
        onClosed: root.forceActiveFocus()
    }
    function askPurge(all) {
        var n = all ? safeTrash.count : root.picked
        if (n === 0) return
        root.purgeAll = all
        purgeCard.title = all ? "Empty the safe trash?" : "Delete " + photos(n) + " for good?"
        purgeCard.body = (all ? "All " + photos(n) + " (" + safeTrash.totalText + ") in the safe trash" : photos(n) + " (" + safeTrash.selectedText + ")")
                       + " will be deleted from the disk, with their sidecars and edit data. This cannot be undone."
        purgeCard.confirmText = all ? "Empty trash" : "Delete for good"
        purgeCard.open()
    }
    function chooseLimit(gb) {
        var n = safeTrash.limitWouldRemove(gb)
        if (n === 0) { safeTrash.setLimit(gb); return }
        root.pendingLimit = gb
        limitCard.title = "Set the limit to " + gb + " GB?"
        limitCard.body = "The safe trash is larger than that. The " + photos(n) + " deleted longest ago will be deleted for good now. This cannot be undone."
        limitCard.open()
    }
    FolderDialog { id: toDialog; title: "Restore to folder"; onAccepted: safeTrash.restoreTo(false, root.localPath(selectedFolder)) }

    DialogCard {
        id: win
        shown: root.shown
        width: root.winW; height: root.winH
        x: (parent.width - width) / 2; y: (parent.height - height) / 2
        RoundClip {
            anchors.fill: parent; anchors.margins: 1; radius: 15
            Rectangle { anchors.fill: parent; color: Qt.alpha(theme.base, 0.97) }
            RHeader {
                id: hd
                width: parent.width
                title: "Safe Trash"
                sub: "Photos you deleted stay here until you restore them or delete them for good. Restoring puts a photo back where it was, with its sidecars, edit data and the decision it had."
                iconPath: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1 -13M10 10.5v6M14 10.5v6"
                onCloseRequested: safeTrash.close()
            }

            // ---- the line above the list: how much is here, and choose all ----
            Item {
                id: bar
                y: hd.height; x: 26; width: parent.width - 52; height: 36
                Text { anchors.verticalCenter: parent.verticalCenter; color: theme.fg2; font.pixelSize: 13
                       text: safeTrash.loading && safeTrash.count === 0 ? "Reading the safe trash…"
                           : safeTrash.count === 0 ? "" : root.photos(safeTrash.count) + ", " + safeTrash.totalText
                             + (root.picked > 0 ? "  ·  " + root.picked + " chosen (" + safeTrash.selectedText + ")" : "")
                       Accessible.role: Accessible.StaticText; Accessible.name: text }
                SButton { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; visible: safeTrash.count > 0; variant: "ghost"; h: 28
                          text: root.picked === safeTrash.count ? "Choose none" : "Choose all"; onClicked: safeTrash.selectAll(root.picked !== safeTrash.count) }
            }

            // ---- the photos ----
            ListView {
                id: list
                objectName: "safeTrashList"
                x: 26; y: bar.y + bar.height + 4; width: parent.width - 52; height: foot.y - y - limitBox.height - 22
                model: safeTrash.rows; clip: true; spacing: 6; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 6; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: theme.line } background: null }
                delegate: Item {
                    id: rowItem
                    required property int index
                    required property string name
                    required property string folder
                    required property bool folderMissing
                    required property string deleted
                    required property string size
                    required property int files
                    required property string thumb
                    required property string place
                    required property bool selected
                    required property string decision
                    width: list.width - 10; height: 60
                    activeFocusOnTab: true
                    Keys.onPressed: (e) => { if ((e.key === Qt.Key_Space || e.key === Qt.Key_Return || e.key === Qt.Key_Enter) && !e.isAutoRepeat) { safeTrash.toggle(rowItem.index); e.accepted = true } }
                    Accessible.role: Accessible.CheckBox; Accessible.checkable: true; Accessible.checked: rowItem.selected
                    Accessible.name: rowItem.name + ", deleted " + rowItem.deleted + ", from " + rowItem.folder + (rowItem.folderMissing ? " (folder no longer there)" : "") + ", " + rowItem.size
                    Rectangle { anchors.fill: parent; radius: 12; color: rowItem.selected ? Qt.tint(theme.s1, Qt.alpha(theme.accent, 0.14)) : (rh.hovered ? theme.s2 : theme.s1)
                                border.width: 1; border.color: rowItem.selected ? Qt.alpha(theme.accent, 0.5) : theme.lineSoft }
                    Rectangle { visible: rowItem.activeFocus; anchors.fill: parent; anchors.margins: -2; radius: 14; color: "transparent"; border.width: 2; border.color: theme.accent }
                    // the tick
                    Rectangle { id: tick; x: 14; anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18; radius: 5
                                color: rowItem.selected ? theme.accent : "transparent"; border.width: 1; border.color: rowItem.selected ? theme.accent : theme.line
                                Icon { visible: rowItem.selected; anchors.centerIn: parent; size: 13; strokeWidth: 2.2; color: theme.onAccent; path: "M4.5 12.5l4.5 4.5 10.5-10.5" } }
                    // a picture when one of the files is cheap to show as it is, else the trash mark
                    Rectangle { id: pic; x: 44; anchors.verticalCenter: parent.verticalCenter; width: 44; height: 44; radius: 8; color: theme.s3; clip: true
                                Image { anchors.fill: parent; visible: rowItem.thumb !== ""; source: rowItem.thumb; asynchronous: true; cache: false
                                        sourceSize.width: 96; sourceSize.height: 96; fillMode: Image.PreserveAspectCrop }
                                Icon { visible: rowItem.thumb === ""; anchors.centerIn: parent; name: "trash"; size: 18; strokeWidth: 1.6; color: theme.fg3 } }
                    Column {
                        x: 100; anchors.verticalCenter: parent.verticalCenter; width: parent.width - x - meta.width - 24; spacing: 2
                        Text { width: parent.width; elide: Text.ElideMiddle; text: rowItem.name + (rowItem.files > 1 ? "  +" + (rowItem.files - 1) : ""); color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                        Text { width: parent.width; elide: Text.ElideMiddle; color: rowItem.folderMissing ? theme.rev : theme.fg3; font.pointSize: UiState.pt(12)
                               text: rowItem.folderMissing ? "Folder no longer there: " + rowItem.folder : rowItem.folder === "Not recorded" ? "Where it came from was not recorded" : "From " + rowItem.folder }
                    }
                    Column {
                        id: meta
                        anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                        Text { anchors.right: parent.right; text: rowItem.deleted; color: theme.fg2; font.pointSize: UiState.pt(12) }
                        Text { anchors.right: parent.right; color: theme.fg3; font.pointSize: UiState.pt(11.5)
                               text: rowItem.size + (rowItem.decision !== "" ? "  ·  " + rowItem.decision : "") + (rowItem.place !== "This computer" ? "  ·  " + rowItem.place : "") }
                    }
                    HoverHandler { id: rh; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: { rowItem.forceActiveFocus(); safeTrash.toggle(rowItem.index) } }
                }
            }
            // nothing here
            Column {
                visible: safeTrash.count === 0 && !safeTrash.loading
                anchors.horizontalCenter: parent.horizontalCenter; y: list.y + Math.max(0, (list.height - height) / 2); width: Math.min(420, parent.width - 80); spacing: 6
                Rectangle { anchors.horizontalCenter: parent.horizontalCenter; width: 56; height: 56; radius: 28; color: theme.s2; border.color: theme.lineSoft
                    Icon { anchors.centerIn: parent; name: "trash"; size: 26; strokeWidth: 1.6; color: theme.fg3 } }
                Text { width: parent.width; topPadding: 6; horizontalAlignment: Text.AlignHCenter; text: "The safe trash is empty"; color: theme.fg; font.pixelSize: 15; font.weight: Font.Medium }
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(13)
                       text: "Photos deleted with the Safe Trash delete behavior are listed here. Photos sent to the system trash are restored from there." }
            }

            // ---- the size limit, and what the last restore or delete left ----
            Rectangle {
                id: limitBox
                x: 26; y: foot.y - height - 12; width: parent.width - 52; radius: 12; color: theme.s1; border.color: theme.lineSoft
                height: limitCol.implicitHeight + 24
                Column {
                    id: limitCol
                    x: 14; y: 12; width: parent.width - 28; spacing: 8
                    Item { width: parent.width; height: 32
                        Text { anchors.verticalCenter: parent.verticalCenter; text: "Size limit"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                        SSelect { objectName: "safeTrashLimit"; anchors.right: parent.right; name: "Safe Trash size limit"; options: safeTrash.limitOptions; current: safeTrash.limit
                                  enabled: root.idle; opacity: enabled ? 1 : 0.4; onPicked: (value) => root.chooseLimit(value) }
                    }
                    Text { width: parent.width; wrapMode: Text.WordWrap; text: safeTrash.limitNote; color: theme.fg3; font.pointSize: UiState.pt(12); lineHeight: 17.5; lineHeightMode: Text.FixedHeight }
                    Text { visible: safeTrash.report.length > 0; width: parent.width; wrapMode: Text.WordWrap; color: theme.rev; font.pointSize: UiState.pt(12)
                           text: "Left in the trash: " + safeTrash.report.slice(0, 3).join("; ") + (safeTrash.report.length > 3 ? "; and " + (safeTrash.report.length - 3) + " more" : "")
                           Accessible.role: Accessible.StaticText; Accessible.name: text }
                }
            }

            Rectangle {
                id: foot
                y: parent.height - 60; width: parent.width; height: 60; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                Row { x: 26; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { objectName: "safeTrashRestore"; text: root.picked > 0 ? "Restore " + root.picked : "Restore all"; variant: "primary"; h: 34; hpad: 16; radius: 12
                              enabled: root.idle && safeTrash.count > 0; onClicked: safeTrash.restore(root.picked === 0) }
                    SButton { objectName: "safeTrashRestoreTo"; text: "Restore to folder…"; h: 34; hpad: 16; radius: 12
                              enabled: root.idle && root.picked > 0; onClicked: toDialog.open() }
                    SButton { objectName: "safeTrashDelete"; text: "Delete for good…"; h: 34; hpad: 16; radius: 12
                              enabled: root.idle && root.picked > 0; onClicked: root.askPurge(false) }
                    SButton { objectName: "safeTrashEmpty"; text: "Empty trash…"; h: 34; hpad: 16; radius: 12
                              enabled: root.idle && safeTrash.count > 0; onClicked: root.askPurge(true) }
                }
                SButton { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: "Done"; h: 34; hpad: 16; radius: 12; onClicked: safeTrash.close() }
            }
        }
    }
}
