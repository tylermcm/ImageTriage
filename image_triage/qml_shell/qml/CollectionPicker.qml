import QtQuick
import QtQuick.Shapes

// "Add 3 photos to a Collection": type to find one (or to name a new one), pick a row, Choose. Recent collections come first.
Backdrop {
    id: root
    objectName: "collectionPicker"
    shown: UiState.collPicker && UiState.collDlg === ""
    property string sel: ""
    property string query: UiState.collPickerQuery
    readonly property string clean: query.split(/\s+/).filter(function (w) { return w !== "" }).join(" ")
    readonly property var all: { void collections.revision; return collections.pickerEntries() }
    readonly property var entries: { var q = clean.toLowerCase(); return all.filter(function (e) { return q === "" || e.name.toLowerCase().indexOf(q) >= 0 }) }
    readonly property bool exact: { var q = clean.toLowerCase(); return all.some(function (e) { return e.name.toLowerCase() === q }) }
    readonly property bool narrowed: photos.shown < photos.total
    readonly property string title: photos.scopeKind !== "" ? "Add to Collection" : "Add " + (narrowed ? photos.shown + " photos" : "photo") + " to a Collection"
    onShownChanged: { if (shown) { sel = all.length ? all[0].id : "new"; forceActiveFocus() } }
    onEntriesChanged: { if (sel !== "new" && !entries.some(function (e) { return e.id === sel })) sel = entries.length ? entries[0].id : "new" }
    function close() { UiState.collPicker = false; UiState.collPickerQuery = "" }
    function choose() {
        if (sel === "new" || sel === "") { UiState.collName = clean; UiState.collDlgId = ""; UiState.collDlg = "new" }
        else { collections.addVisible(sel); close() }
    }
    onDismissed: close()
    Keys.onReturnPressed: choose()

    DialogCard {
        shown: root.shown
        width: 400; height: col.implicitHeight + 46
        x: (parent.width - width) / 2; y: 130
        Column {
            id: col
            x: 23; y: 23; width: parent.width - 46
            Text { width: parent.width; height: 20.25; verticalAlignment: Text.AlignVCenter; text: root.title; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15 }
            Item { width: 1; height: 12 }
            Rectangle {
                width: parent.width; height: 34; radius: 10; color: theme.base; border.color: qin.activeFocus ? theme.accent : theme.lineSoft
                Row { x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    Icon { name: "search"; size: 16; strokeWidth: 1.7; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
                    TextInput { id: qin; width: col.width - 54; anchors.verticalCenter: parent.verticalCenter; color: theme.fg; font.pixelSize: 13; clip: true; selectByMouse: true
                        text: UiState.collPickerQuery; onTextEdited: UiState.collPickerQuery = text
                        Text { visible: !parent.text; text: "Type to find or name a new collection"; color: theme.fg3; font: parent.font }
                        Accessible.name: "Find or name a collection" } }
            }
            Item { width: 1; height: 12 }
            Rectangle {
                width: parent.width; height: 236; radius: 10; color: theme.base; border.color: theme.lineSoft; clip: true
                Flickable {
                    anchors.fill: parent; anchors.margins: 4; contentHeight: lst.implicitHeight; boundsBehavior: Flickable.StopAtBounds; clip: true
                    Column {
                        id: lst; width: parent.width
                        Repeater {
                            model: root.entries
                            delegate: Item {
                                id: row
                                required property var modelData
                                readonly property bool on: root.sel === modelData.id
                                width: lst.width; height: 34
                                Rectangle { anchors.fill: parent; radius: 8; color: row.on ? Qt.alpha(theme.accent, 0.16) : Qt.alpha(theme.lift, rh.hovered ? 0.06 : 0) }
                                CollMark { x: 10; anchors.verticalCenter: parent.verticalCenter; color: row.modelData.color; iconPath: row.modelData.iconPath; iconSize: 14 }
                                Text { x: 34; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 34 - rc.implicitWidth - 20; elide: Text.ElideRight; text: row.modelData.name; color: row.on ? theme.fg : theme.fg2; font.pixelSize: 13 }
                                Text { id: rc; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; color: theme.fg3; font.pointSize: UiState.pt(11.5)
                                       text: "(" + row.modelData.count + ")" + (row.modelData.recent ? "  · recent" : "") }
                                HoverHandler { id: rh; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: root.sel = row.modelData.id }
                                TapHandler { onDoubleTapped: root.choose() }
                                Accessible.role: Accessible.Button; Accessible.name: row.modelData.name; Accessible.checked: row.on
                            }
                        }
                        Item { visible: root.entries.length > 0; width: lst.width; height: 9; Rectangle { x: 6; y: 4; width: parent.width - 12; height: 1; color: theme.lineSoft } }
                        Item {
                            id: nrow
                            readonly property bool on: root.sel === "new"
                            width: lst.width; height: 34
                            Rectangle { anchors.fill: parent; radius: 8; color: nrow.on ? Qt.alpha(theme.accent, 0.16) : Qt.alpha(theme.lift, nh.hovered ? 0.06 : 0) }
                            Icon { x: 10; anchors.verticalCenter: parent.verticalCenter; name: "plus"; size: 14; strokeWidth: 1.9; color: theme.fg2 }
                            Text { x: 34; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 44; elide: Text.ElideRight; color: nrow.on ? theme.fg : theme.fg2; font.pixelSize: 13
                                   text: root.clean !== "" && !root.exact ? "New collection “" + root.clean + "”" : "New collection…" }
                            HoverHandler { id: nh; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: root.sel = "new" }
                            TapHandler { onDoubleTapped: root.choose() }
                            Accessible.role: Accessible.Button; Accessible.name: "New collection"
                        }
                    }
                }
            }
            Item { width: 1; height: 20 }
            Row { anchors.right: parent.right; spacing: 8
                SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: root.close() }
                SButton { text: "Choose"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: root.choose() }
            }
        }
    }
}
