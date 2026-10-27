import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

// Browse Library: search across the folders that were indexed, scope chips, result cards and the list of indexed folders. The model is
// reference_model.py (`reference`); this file draws its `view.lib` (mockup: scripts/qml_parity/help.dc.html).
Backdrop {
    id: root
    objectName: "libraryWindow"
    readonly property var v: reference.view
    readonly property var l: v.lib || ({ empty: false, stats: [], scopes: [], cards: [], roots: [], query: "", scope: "all", noResults: false, foot: "", show: "", canShow: false })
    shown: v.window === "lib"
    onDismissed: reference.close()
    z: 106

    readonly property real winW: Math.min(780, width - 64)
    readonly property real winH: Math.min(660, height - 72)
    component SecLabel: Text { color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }

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
                title: "Browse Library"; sub: "Search across the folders you chose to index, without leaving your normal folder-first workflow. Results open in the grid."
                iconPath: "M4 5h4v14H4zM10 5h4v14h-4zM16 6l4 1-3 12-4-1z"
                onCloseRequested: reference.close()
            }
            Flickable {
                id: body
                y: hd.height; width: parent.width; height: parent.height - hd.height - 60
                contentWidth: width; contentHeight: (root.l.empty ? emptyCol.height : full.height) + 8 + 18; clip: true; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 6; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: theme.line } background: null }
                Column {
                    id: full
                    visible: !root.l.empty
                    x: 26; y: 10; width: body.width - 52
                    // the numbers
                    Row {
                        width: parent.width; spacing: 8
                        Repeater {
                            model: root.l.stats
                            delegate: Rectangle {
                                required property var modelData
                                width: (full.width - 16) / 3; height: 11 + 22 + 3 + 14 + 11 + 1; radius: 13; color: theme.s1; border.color: theme.lineSoft
                                Text { x: 13; y: 11; text: parent.modelData.n; color: theme.fg; font.pixelSize: 20; font.weight: Font.DemiBold; font.letterSpacing: -0.4; height: 22; verticalAlignment: Text.AlignVCenter }
                                Text { x: 13; y: 11 + 22 + 3; text: parent.modelData.l; color: theme.fg3; font.pointSize: UiState.pt(12) }
                            }
                        }
                    }
                    Item { width: 1; height: 14 }
                    RSearch { width: parent.width; placeholder: "Search file names and paths. Leave blank to browse everything indexed"; text: root.l.query; onEdited: (t) => reference.setLibQuery(t) }
                    Item { width: 1; height: 12 }
                    // where to look
                    Flow {
                        width: parent.width; spacing: 6
                        Repeater {
                            model: root.l.scopes
                            delegate: Rectangle {
                                id: chip
                                required property var modelData
                                readonly property bool on: root.l.scope === modelData.id
                                width: cl.implicitWidth + 22 + (cn.implicitWidth > 0 ? cn.implicitWidth + 7 : 0); height: 28; radius: 14
                                color: on ? theme.s3 : theme.s1; border.width: on ? 1.5 : 1; border.color: on ? theme.accent : theme.lineSoft
                                Text { id: cl; x: 11; anchors.verticalCenter: parent.verticalCenter; text: chip.modelData.label; color: chip.on ? theme.fg : theme.fg2; font.pointSize: UiState.pt(12.5) }
                                Text { id: cn; x: 11 + cl.implicitWidth + 7; anchors.verticalCenter: parent.verticalCenter; text: chip.modelData.n; color: theme.fg3; font.pointSize: UiState.pt(11.5) }
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: reference.setLibScope(chip.modelData.id) }
                                Accessible.role: Accessible.Button; Accessible.name: modelData.label
                            }
                        }
                    }
                    Item { width: 1; height: 12 }
                    // what was found
                    Grid {
                        width: parent.width; columns: 4; spacing: 8
                        Repeater {
                            model: root.l.cards
                            delegate: Rectangle {
                                id: card
                                required property var modelData
                                width: (full.width - 24) / 4; height: width * 2 / 3 + 7 + 15 + 13 + 8; radius: 12; color: theme.s1; border.color: theme.lineSoft
                                RoundClip { anchors.fill: parent; anchors.margins: 1; radius: 11
                                    ThumbImage { width: parent.width; height: parent.width * 2 / 3 - 1; path: card.modelData.path; bucket: 240 }
                                    Text { x: 10; y: parent.width * 2 / 3 + 6; width: parent.width - 20; elide: Text.ElideRight; text: card.modelData.name; color: theme.fg; font.pointSize: UiState.pt(12) }
                                    Text { x: 10; y: parent.width * 2 / 3 + 6 + 15; width: parent.width - 20; elide: Text.ElideRight; text: card.modelData.where; color: theme.fg3; font.pointSize: UiState.pt(11) } }
                            }
                        }
                    }
                    Column { visible: root.l.noResults; width: parent.width; topPadding: 60
                        Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "No matches"; color: theme.fg; font.pixelSize: 15; font.weight: Font.Medium }
                        Text { width: parent.width; topPadding: 4; horizontalAlignment: Text.AlignHCenter; text: "Nothing indexed has that in its name or path."; color: theme.fg3; font.pointSize: UiState.pt(13.5) }
                        Item { width: 1; height: 60 } }
                    // the folders
                    Item { width: 1; height: 18 }
                    Item { width: parent.width; height: 28
                        SecLabel { anchors.verticalCenter: parent.verticalCenter; text: "INDEXED FOLDERS" }
                        SButton { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; text: "Add folder…"; h: 28; hpad: 11; radius: 9; onClicked: folderDialog.open() } }
                    Item { width: 1; height: 8 - 0 }
                    Repeater {
                        model: root.l.roots
                        delegate: Item {
                            id: rr
                            required property var modelData
                            width: full.width; height: 56 + 6
                            Rectangle { width: parent.width; height: 56; radius: 12; color: theme.s1; border.color: theme.lineSoft
                                Icon { x: 12; anchors.verticalCenter: parent.verticalCenter; name: "openFolder"; size: 18; strokeWidth: 1.6; color: theme.fg3 }
                                Column { x: 42; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 42 - 150
                                    Text { width: parent.width; elide: Text.ElideRight; text: rr.modelData.name; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                    Text { width: parent.width; elide: Text.ElideRight; text: rr.modelData.meta; color: theme.fg3; font.pointSize: UiState.pt(12) } }
                                Row { anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; spacing: 0
                                    Repeater { model: [{ t: "Re-index", a: "reindex" }, { t: "Remove", a: "remove" }]
                                        delegate: Item { id: ab; required property var modelData; width: at.implicitWidth + 16; height: 26
                                            Rectangle { anchors.fill: parent; radius: 7; color: Qt.alpha(theme.lift, ah.hovered ? 0.08 : 0) }
                                            Text { id: at; anchors.centerIn: parent; text: ab.modelData.t; color: ah.hovered ? theme.fg : theme.fg3; font.pointSize: UiState.pt(12) }
                                            HoverHandler { id: ah; cursorShape: Qt.PointingHandCursor }
                                            TapHandler { onTapped: ab.modelData.a === "reindex" ? reference.reindex(rr.modelData.path) : reference.removeRoot(rr.modelData.path) }
                                            Accessible.role: Accessible.Button; Accessible.name: ab.modelData.t } } } }
                        }
                    }
                    Text { x: 2; width: parent.width - 4; topPadding: 6; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 19.4; lineHeightMode: Text.FixedHeight
                           text: "The index only remembers file names, paths and bundle snapshots so you can find a set again. AI results stay inside each folder." }
                }
                Column {
                    id: emptyCol
                    visible: root.l.empty; x: 26; y: 8; width: body.width - 52; topPadding: 48
                    Rectangle { x: (parent.width - 56) / 2; width: 56; height: 56; radius: 28; color: theme.s2; border.color: theme.lineSoft
                        Icon { anchors.centerIn: parent; path: "M4 5h4v14H4zM10 5h4v14h-4zM16 6l4 1-3 12-4-1z"; size: 26; strokeWidth: 1.6; color: theme.fg3 } }
                    Text { width: parent.width; topPadding: 12; horizontalAlignment: Text.AlignHCenter; text: "Nothing is indexed yet"; color: theme.fg; font.pixelSize: 15; font.weight: Font.Medium }
                    Text { x: (parent.width - 380) / 2; width: 380; topPadding: 4; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(13); lineHeight: 20.15; lineHeightMode: Text.FixedHeight
                           text: "Pick the folders you want to be able to search together, such as your yearly shoot folders. You can add or remove them whenever you like." }
                    Item { width: 1; height: 16 }
                    SButton { x: (parent.width - width) / 2; text: "Index a folder…"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: folderDialog.open() }
                }
            }
            Rectangle {
                y: parent.height - 60; width: parent.width; height: 60; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                Text { x: 26; anchors.verticalCenter: parent.verticalCenter; text: root.l.foot; color: theme.fg3; font.pointSize: UiState.pt(12.5) }
                Row { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { text: "Close"; h: 34; hpad: 16; radius: 12; onClicked: reference.close() }
                    SButton { text: root.l.show; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: root.l.canShow; onClicked: reference.showInGrid() } }
            }
        }
    }
    FolderDialog { id: folderDialog; title: "Choose a folder to index"; onAccepted: reference.addFolder(selectedFolder.toString().replace(/^file:\/\/\/?/, Qt.platform.os === "windows" ? "" : "/")) }
}
