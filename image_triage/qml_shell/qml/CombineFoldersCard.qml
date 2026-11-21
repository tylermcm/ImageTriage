import QtQuick
import QtQuick.Controls

// Combine folders: a trip's camera folders (107NZ7_2, 108NZ7_2, …) moved into one, their numbering continued (folder_combine.py).
// Which folders, where they go (the folder they are in, one of them, or a new one), and a preview of what happens to the names.
Popup {
    id: card
    objectName: "combineFoldersCard"
    property string parentPath: ""
    property string parentName: ""
    property var folders: []                   // [{path, name, count, picked}]
    property string dest: ""                   // a path, or "new"
    property string newName: ""
    property bool renumber: true
    property bool removeEmpty: true
    readonly property var picked: { var out = []; for (var i = 0; i < folders.length; ++i) if (folders[i].picked) out.push(folders[i].path); return out }
    readonly property string destPath: dest === "new" ? (newName.trim() !== "" ? parentPath + "/" + newName.trim() : "") : dest
    readonly property var preview: fileActions.combinePreview(picked, destPath, renumber)
    readonly property var destOptions: {
        var out = [{ label: parentName + " (the folder they are in)", value: parentPath }]
        for (var i = 0; i < folders.length; ++i) if (folders[i].picked) out.push({ label: folders[i].name, value: folders[i].path })
        out.push({ label: "A new folder…", value: "new" })
        return out
    }

    function openFor(folder) {
        var found = fileActions.combineCandidates(folder)
        if (!found.folders || found.folders.length === 0) return
        parentPath = found.parent; parentName = found.parentName; folders = found.folders
        dest = parentPath; newName = ""; renumber = true; removeEmpty = true
        open()
    }
    function toggle(index) {
        var copy = folders.slice(); copy[index] = Object.assign({}, copy[index], { picked: !copy[index].picked }); folders = copy
        if (dest !== parentPath && dest !== "new" && picked.indexOf(dest) < 0) dest = parentPath     // the destination was unticked
    }

    modal: true; dim: true; focus: true
    parent: Overlay.overlay                    // sized and centred on the window, not the side panel it is opened from
    anchors.centerIn: Overlay.overlay
    width: Math.min(520, (parent ? parent.width : 520) - 48)
    padding: 22
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Qt.rgba(6 / 255, 7 / 255, 9 / 255, 0.52) }
    enter: Transition { ParallelAnimation {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 200 }
        NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } } }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 120 } }
    background: Item {
        Shadow { anchors.fill: parent; radius: 16; offsetA: 24; insetA: 16; blurA: 48; alphaA: 0.7 }
        Rectangle { anchors.fill: parent; radius: 16; color: Qt.alpha(theme.s2, 0.97); border.color: Qt.alpha(theme.lift, 0.11) }
    }
    contentItem: Column {
        spacing: 0
        Text { width: parent.width; text: "Combine folders"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold }
        Text { width: parent.width; topPadding: 6; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 13; lineHeight: 19.5; lineHeightMode: Text.FixedHeight
               text: "Moves the files of these folders into one. Their edits and decisions go with them, and Undo puts everything back." }
        Item { width: 1; height: 14 }
        Text { text: "FOLDERS"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
        Item { width: 1; height: 6 }
        Flickable {
            width: parent.width; height: Math.min(folderCol.height, 5 * 38); contentHeight: folderCol.height; clip: true; boundsBehavior: Flickable.StopAtBounds
            Column {
                id: folderCol; width: parent.width
                Repeater {
                    model: card.folders
                    delegate: Item {
                        required property var modelData
                        required property int index
                        objectName: "combineFolder_" + modelData.name
                        width: folderCol.width; height: 38
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                        Icon { y: 11; size: 16; color: theme.fg2; path: "M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" }
                        Text { x: 26; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 26 - 110; elide: Text.ElideRight
                               text: modelData.name; color: theme.fg; font.pixelSize: 13 }
                        Text { anchors.right: sw.left; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                               text: modelData.count + (modelData.count === 1 ? " file" : " files"); color: theme.fg3; font.pixelSize: 12 }
                        SSwitch { id: sw; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; name: modelData.name
                                  checked: modelData.picked; onToggled: card.toggle(index) }
                    }
                }
            }
        }
        Item { width: 1; height: 14 }
        Row {
            spacing: 12; width: parent.width
            Column {
                spacing: 6; width: card.dest === "new" ? (parent.width - 12) / 2 : parent.width
                Text { text: "Into"; color: theme.fg2; font.pixelSize: 12 }
                SSelect { objectName: "combineInto"; width: parent.width; name: "Into"; options: card.destOptions; current: card.dest; onPicked: (v) => card.dest = v }
            }
            Column {
                spacing: 6; width: (parent.width - 12) / 2; visible: card.dest === "new"
                Text { text: "New folder's name"; color: theme.fg2; font.pixelSize: 12 }
                FInput { objectName: "combineNewName"; width: parent.width; placeholder: card.parentName; text: card.newName; onEdited: (v) => card.newName = v }
            }
        }
        Item { width: 1; height: 10 }
        Repeater {
            model: [{ k: "renumber", t: "Continue the numbering", d: "Each folder's _DSC0001… takes the next number after the photos before it, instead of a name with (1)." },
                    { k: "removeEmpty", t: "Remove the emptied folders", d: "A folder that still holds a subfolder stays." }]
            delegate: Item {
                required property var modelData
                width: parent ? parent.width : 0; height: Math.max(46, dsc.y + dsc.height + 8)
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                Text { id: ttl; y: 8; width: parent.width - 52; text: modelData.t; color: theme.fg; font.pixelSize: 13 }
                Text { id: dsc; y: ttl.y + ttl.height + 2; width: parent.width - 52; wrapMode: Text.WordWrap; text: modelData.d; color: theme.fg3; font.pixelSize: 12 }
                SSwitch { objectName: "combine_" + modelData.k; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; name: modelData.t
                          checked: card[modelData.k]; onToggled: (v) => card[modelData.k] = v }
            }
        }
        Item { width: 1; height: 12 }
        Text {
            objectName: "combineSummary"
            width: parent.width; wrapMode: Text.WordWrap; font.pixelSize: 12; lineHeight: 17.4; lineHeightMode: Text.FixedHeight
            color: card.preview.error ? theme.rej : theme.fg2
            text: card.preview.error || card.preview.summary || ""
        }
        Repeater {
            model: card.preview.examples || []
            delegate: Text { required property var modelData; width: card.availableWidth; elide: Text.ElideMiddle; text: modelData; color: theme.fg3; font.pixelSize: 12; font.family: "Consolas" }
        }
        Repeater {
            model: card.preview.left || []
            delegate: Text { required property var modelData; width: card.availableWidth; wrapMode: Text.WordWrap; text: modelData + ", so it stays."; color: theme.fg3; font.pixelSize: 12 }
        }
        Item { width: 1; height: 18 }
        Row {
            anchors.right: parent.right; spacing: 8
            SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: card.close() }
            SButton { objectName: "combineApply"; text: "Combine"; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: card.preview.ok === true
                      onClicked: { fileActions.combineFolders(card.picked, card.destPath, card.renumber, card.removeEmpty); card.close() } }
        }
    }
}
