import QtQuick
import QtQuick.Shapes
import QtQuick.Controls

// The panel beside the rail: Library, Faces, Collections, PocketDrop. Opening animates the column's width (in Main); the contents
// slide and fade with it, and each page's blocks rise in one after another when it becomes the current page.
Item {
    id: panel
    property real fullW: 300
    readonly property real p: Math.max(0, Math.min(1, width / fullW))
    clip: true

    // Hands a Library entry (a collection key such as "col:album") to PocketDrop and shows its page.
    function shareToPhone(key) {
        pocketDrop.refreshLibrary(UiState.selRow)
        pocketDrop.addLibrary(key)
        UiState.tab = "drop"
    }
    function thumbPaths(idx) {
        var out = []
        for (var i = 0; i < 3; ++i) { var it = photos.get(idx[i] % Math.max(1, photos.shown)); out.push(it && it.path ? it.path : "") }
        return out
    }

    Rectangle { anchors.fill: parent; color: theme.s2 }
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }

    Item {
        id: inner
        width: panel.fullW; height: parent.height
        x: (panel.p - 1) * 18
        opacity: panel.p

        Item {
            id: head
            width: parent.width; height: 44
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
            Text {
                x: 16; anchors.verticalCenter: parent.verticalCenter
                text: (UiState.tabTitles[UiState.tab] || "Library").toUpperCase(); color: theme.fg2
                font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 1
            }
            Item {
                anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter; width: 32; height: 32
                Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, ch.hovered ? 0.08 : 0) }
                Icon { anchors.centerIn: parent; name: "chevL"; size: 18; color: theme.fg }
                HoverHandler { id: ch; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: UiState.tab = "" }
                Accessible.role: Accessible.Button; Accessible.name: "Hide side panel"
            }
        }

        // ---- Library -------------------------------------------------------------------------------------------------
        Flickable {
            id: lib
            readonly property bool on: UiState.tab === "lib"
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: libCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 250 } }
            Column {
                id: libCol; width: parent.width; topPadding: 12
                Stag { active: lib.on; n: 0
                    Rectangle {
                        x: 12; width: parent.width - 24; height: 32; radius: 9; color: theme.base; border.color: fieldIn.activeFocus ? theme.accent : theme.lineSoft
                        Row { anchors.verticalCenter: parent.verticalCenter; x: 10; spacing: 8
                            Icon { name: "search"; size: 18; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
                            TextInput { id: fieldIn; width: 200; color: theme.fg; font.pixelSize: 13; clip: true; anchors.verticalCenter: parent.verticalCenter
                                Text { visible: !parent.text && !parent.activeFocus; text: "Find a folder"; color: theme.fg3; font: parent.font }
                                Accessible.name: "Find a folder" }
                        }
                    }
                }
                Stag { active: lib.on; n: 1; Column { width: parent.width
                    SectionLabel { caption: "Favorites" }
                    PanelRow { icon: "star"; text: "Wedding 2026"; count: "1,284" }
                    PanelRow { icon: "star"; text: "Client selects"; count: "212" } } }
                Stag { active: lib.on; n: 2; Column { width: parent.width
                    SectionLabel { caption: "Drives" }
                    Repeater {
                        model: [{ n: "Photos (D:)", s: "612 GB free of 2 TB", v: 0.70 }, { n: "Cards (E:)", s: "41 GB free of 256 GB", v: 0.84 }]
                        delegate: Item {
                            required property var modelData
                            width: libCol.width; height: 43.2          // 6 + 18.2 line + 7 + 4 meter + 8
                            Text { x: 16; y: 6; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData.n; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                            Text { anchors.right: parent.right; anchors.rightMargin: 16; y: 6; text: modelData.s; color: theme.fg3; font.pointSize: UiState.pt(11) }
                            Meter { x: 16; y: 31.2; width: parent.width - 32; value: modelData.v; stops: ["#4cbd88", "#e6ac3d", "#ef6856"] }
                        }
                    } } }
                Stag { active: lib.on; n: 3; Column { width: parent.width
                    SectionLabel { caption: "Folders" }
                    PanelRow { icon: "chevR"; text: "2025"; count: "8,410" }
                    PanelRow { icon: "chevDown"; text: "2026"; count: "3,902" }
                    PanelRow { icon: "chevR"; text: "January"; count: "640"; indent: 18 }
                    PanelRow { icon: "chevDown"; text: "Georgia trip"; count: "1,284"; indent: 18 }
                    PanelRow { icon: "library"; text: photos.leafName || "Kutaisi"; count: String(photos.total); indent: 36; current: true }
                    PanelRow { icon: "library"; text: "Tbilisi"; count: "402"; indent: 36 } } }
            }
        }

        // ---- Faces ---------------------------------------------------------------------------------------------------
        Flickable {
            id: faces
            readonly property bool on: UiState.tab === "faces"
            readonly property int rev: people.revision
            readonly property var rows: { void rev; return people.sidebar() }
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: facesCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 250 } }
            Column {
                id: facesCol; width: parent.width; topPadding: 12
                Stag { active: faces.on; n: 0
                    Rectangle {
                        x: 12; width: parent.width - 24; height: 32; radius: 9; color: theme.base; border.color: faceIn.activeFocus ? theme.accent : theme.lineSoft
                        Row { anchors.verticalCenter: parent.verticalCenter; x: 10; spacing: 8
                            Icon { name: "search"; size: 16; strokeWidth: 1.7; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
                            TextInput { id: faceIn; width: 230; color: theme.fg; font.pixelSize: 13; clip: true; anchors.verticalCenter: parent.verticalCenter; selectByMouse: true
                                text: UiState.faceQuery; onTextEdited: { UiState.faceQuery = text; people.setQuery(text) }
                                Text { visible: !parent.text && !parent.activeFocus; text: "Find a person"; color: theme.fg3; font: parent.font }
                                Accessible.name: "Find a person" }
                        }
                    }
                }
                Stag { active: faces.on; n: 1; Column { width: parent.width
                    SectionLabel { caption: "People" }
                    Repeater {
                        model: faces.rows
                        delegate: Item {
                            id: fr
                            required property var modelData
                            readonly property bool cur: { void photos.scopeKind; return photos.scopeKind === "person" && photos.scopeId === modelData.id }
                            width: faces.width; height: 50
                            Rectangle { anchors.fill: parent; color: fr.cur ? Qt.alpha(theme.accent, 0.13) : Qt.alpha(theme.lift, fh.hovered ? 0.05 : 0) }
                            Rectangle { visible: fr.cur; x: 0; y: 8; width: 2; height: parent.height - 16; radius: 1; color: theme.accent }
                            FaceDisc { x: 16; anchors.verticalCenter: parent.verticalCenter; width: 36; height: 36; hue: fr.modelData.hue; initial: fr.modelData.init; fontSize: 13 }
                            Text { x: 64; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 64 - 52; elide: Text.ElideRight; text: fr.modelData.name || "Unnamed"; color: fr.modelData.name ? theme.fg : theme.fg3; font.pixelSize: 13 }
                            Text { anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: fr.modelData.count; color: theme.fg3; font.pixelSize: 11 }
                            HoverHandler { id: fh; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: { if (fr.cur) collections.closeScope(); else people.openPerson(fr.modelData.id) } }
                            Accessible.role: Accessible.Button; Accessible.name: (fr.modelData.name || "Unnamed face") + ", " + fr.modelData.count + " photos"
                        }
                    }
                    Text { visible: faces.rows.length === 0; x: 16; width: parent.width - 32; topPadding: 6; bottomPadding: 6; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                           color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18; lineHeightMode: Text.FixedHeight
                           text: { void faces.rev; return people.emptyText() } }
                    Item {
                        visible: faces.rows.length > 0; width: parent.width; height: 40
                        Rectangle { anchors.fill: parent; color: Qt.alpha(theme.lift, ah.hovered ? 0.05 : 0) }
                        Icon { x: 16; anchors.verticalCenter: parent.verticalCenter; name: "people"; size: 18; strokeWidth: 1.7; color: theme.fg3 }
                        Text { x: 46; anchors.verticalCenter: parent.verticalCenter; text: "All people…"; color: theme.fg2; font.pixelSize: 13 }
                        Icon { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; name: "chevR"; size: 16; color: theme.fg3 }
                        HoverHandler { id: ah; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: UiState.peopleOpen = true }
                        Accessible.role: Accessible.Button; Accessible.name: "All people"
                    } } }
                Stag { active: faces.on; n: 2; visible: people.hasIndex; Column { width: parent.width
                    SectionLabel { caption: "Face index" }
                    Item { width: 1; height: 1 }
                    Meter { x: 16; width: parent.width - 32; value: people.indexInfo().value }
                    PanelRow { text: people.indexInfo().text; count: people.indexInfo().pct; interactive: false; muted: true } } }
            }
        }

        // ---- Collections ---------------------------------------------------------------------------------------------
        Flickable {
            id: coll
            readonly property bool on: UiState.tab === "coll"
            readonly property int rev: collections.revision
            readonly property var rows: { void rev; return collections.rows }
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: collCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 250 } }
            Column {
                id: collCol; width: parent.width; topPadding: 12
                Stag { active: coll.on; n: 0; Column { width: parent.width
                    SectionLabel { caption: "Winners" }
                    Item {
                        id: wrow
                        readonly property bool cur: { void photos.scopeKind; return photos.scopeKind === "winners" }
                        width: parent.width; height: 34
                        Rectangle { anchors.fill: parent; color: wrow.cur ? Qt.alpha(theme.accent, 0.13) : Qt.alpha(theme.lift, wh.hovered ? 0.05 : 0) }
                        Rectangle { visible: wrow.cur; x: 0; y: 6; width: 2; height: parent.height - 12; radius: 1; color: theme.accent }
                        Icon { x: 16; anchors.verticalCenter: parent.verticalCenter; name: "heart"; size: 16; strokeWidth: 1.8; color: wrow.cur ? theme.fg : theme.fg2 }
                        Text { x: 42; anchors.verticalCenter: parent.verticalCenter; text: "Winners"; color: wrow.cur || wh.hovered ? theme.fg : theme.fg2; font.pixelSize: 13 }
                        Text { anchors.right: parent.right; anchors.rightMargin: 18; anchors.verticalCenter: parent.verticalCenter; color: theme.fg3; font.pixelSize: 11; text: { void coll.rev; return collections.winnersCount } }
                        HoverHandler { id: wh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: { if (wrow.cur) collections.closeScope(); else collections.openWinners() } }
                        Accessible.role: Accessible.Button; Accessible.name: "Winners from every shoot"
                    } } }
                Stag { active: coll.on; n: 1; Column { width: parent.width
                    Item {
                        width: parent.width; height: 14 + 24 + 6
                        Text { x: 16; y: 14; height: 24; verticalAlignment: Text.AlignVCenter; text: "COLLECTIONS"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                        Item {
                            anchors.right: parent.right; anchors.rightMargin: 10; y: 14; width: 24; height: 24
                            Rectangle { anchors.fill: parent; radius: 8; color: Qt.alpha(theme.lift, nh.hovered ? 0.08 : 0) }
                            Icon { anchors.centerIn: parent; name: "plus"; size: 16; strokeWidth: 1.8; color: nh.hovered ? theme.fg : theme.fg3 }
                            HoverHandler { id: nh; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: { UiState.collDlgId = ""; UiState.collDlg = "new" } }
                            Accessible.role: Accessible.Button; Accessible.name: "New collection"
                        }
                    }
                    Text { visible: coll.rows.length === 0; x: 16; width: parent.width - 32; wrapMode: Text.WordWrap; topPadding: 6; text: "No collections yet. Make one to gather photos from any folder without moving them."; color: theme.fg3; font.pixelSize: 12; lineHeight: 17.4; lineHeightMode: Text.FixedHeight }
                    Item { visible: coll.rows.length === 0; x: 16; width: 1; height: 44
                        SButton { y: 10; text: "New collection"; variant: "primary"; h: 28; hpad: 11; radius: 9; onClicked: { UiState.collDlgId = ""; UiState.collDlg = "new" } } }
                    Repeater {
                        model: coll.rows
                        delegate: Item {
                            id: crow
                            required property var modelData
                            required property int index
                            readonly property bool cur: { void photos.scopeKind; return photos.scopeKind === "collection" && photos.scopeId === modelData.id }
                            readonly property bool drop: UiState.cldrop === modelData.id
                            readonly property bool hot: ch.hovered || dragH.active
                            width: coll.width; height: 34
                            Rectangle { x: crow.drop ? 8 : 0; width: parent.width - (crow.drop ? 16 : 0); height: parent.height; radius: crow.drop ? 8 : 0
                                        color: crow.drop ? Qt.alpha(theme.accent, 0.12) : crow.cur ? Qt.alpha(theme.accent, 0.13) : Qt.alpha(theme.lift, ch.hovered ? 0.05 : 0) }
                            Rectangle { visible: crow.drop; x: 8; width: parent.width - 16; height: parent.height; radius: 8; color: "transparent"; border.width: 1.5; border.color: Qt.alpha(theme.accent, 0.70) }
                            Rectangle { visible: crow.cur; x: 0; y: 6; width: 2; height: parent.height - 12; radius: 1; color: theme.accent }
                            Row {
                                x: 16; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                                CollMark { anchors.verticalCenter: parent.verticalCenter; color: crow.modelData.color; iconPath: crow.modelData.iconPath }
                                Text { anchors.verticalCenter: parent.verticalCenter; width: Math.min(implicitWidth, crow.width - 130); elide: Text.ElideRight; text: crow.modelData.name; color: theme.fg
                                       font.pixelSize: 13; font.weight: crow.modelData.active ? Font.DemiBold : Font.Normal }
                            }
                            Text { visible: crow.drop; anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: "Drop here"; color: theme.accentFg; font.pointSize: UiState.pt(11.5) }
                            Row {
                                visible: !crow.drop && !crow.hot; anchors.right: parent.right; anchors.rightMargin: 18; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                                Rectangle { visible: crow.modelData.active; height: 17; width: wl.implicitWidth + 12; radius: 6; color: Qt.alpha(theme.accent, 0.14); anchors.verticalCenter: parent.verticalCenter
                                    Text { id: wl; anchors.centerIn: parent; text: "W"; color: theme.accentFg; font.pixelSize: 10; font.weight: Font.DemiBold } }
                                Text { text: crow.modelData.count; color: theme.fg3; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                            }
                            Row {
                                visible: crow.hot && !crow.drop; anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                                Item { width: 26; height: 26
                                    Rectangle { anchors.fill: parent; radius: 8; color: Qt.alpha(theme.lift, sh.hovered ? 0.09 : 0) }
                                    Icon { anchors.centerIn: parent; name: "pocketdrop"; size: 16; strokeWidth: 1.8; color: sh.hovered ? theme.fg : theme.fg3 }
                                    HoverHandler { id: sh; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: panel.shareToPhone("col:" + crow.modelData.id) }
                                    Accessible.role: Accessible.Button; Accessible.name: "Share " + crow.modelData.name + " to phone" }
                                Item { width: 26; height: 26
                                    Rectangle { anchors.fill: parent; radius: 8; color: Qt.alpha(theme.lift, mh.hovered ? 0.09 : 0) }
                                    Icon { anchors.centerIn: parent; name: "more"; size: 16; strokeWidth: 2.4; color: mh.hovered ? theme.fg : theme.fg3 }
                                    HoverHandler { id: mh; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: UiState.collMenu = UiState.collMenu === crow.modelData.id ? "" : crow.modelData.id }
                                    Accessible.role: Accessible.Button; Accessible.name: "More for " + crow.modelData.name }
                            }
                            HoverHandler { id: ch; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: { if (crow.cur) collections.closeScope(); else collections.open(crow.modelData.id) } }
                            TapHandler { acceptedButtons: Qt.RightButton; onTapped: UiState.collMenu = crow.modelData.id }
                            DropArea {   // photos dragged from the grid (the grid is not a drag source yet)
                                anchors.fill: parent
                                onEntered: UiState.cldrop = crow.modelData.id
                                onExited: { if (UiState.cldrop === crow.modelData.id) UiState.cldrop = "" }
                                onDropped: { collections.addSelection(crow.modelData.id, UiState.selRow); UiState.cldrop = "" }
                            }
                            DragHandler {   // drag onto the PocketDrop button (it opens after a moment) and drop on its page
                                id: dragH; target: null; cursorShape: Qt.ClosedHandCursor
                                onActiveChanged: {
                                    if (active) {
                                        UiState.pdDragInfo = { key: "col:" + crow.modelData.id, title: crow.modelData.name, sub: crow.modelData.here + " photos", thumbs: crow.modelData.thumbs }
                                        UiState.pdGhostX = centroid.scenePosition.x; UiState.pdGhostY = centroid.scenePosition.y
                                        UiState.pdDragging = true
                                    } else {
                                        var p = panel.mapFromItem(null, centroid.scenePosition.x, centroid.scenePosition.y)
                                        var onPage = UiState.tab === "drop" && p.x >= 0 && p.x <= panel.width && p.y >= 0 && p.y <= panel.height
                                        UiState.pdDragging = false
                                        if (onPage) panel.shareToPhone("col:" + crow.modelData.id)
                                    }
                                }
                                onCentroidChanged: { if (active) { UiState.pdGhostX = centroid.scenePosition.x; UiState.pdGhostY = centroid.scenePosition.y } }
                            }
                            Accessible.role: Accessible.Button; Accessible.name: crow.modelData.name + ", " + crow.modelData.count + " photos"
                        }
                    } } }
            }
        }

        // ---- PocketDrop ----------------------------------------------------------------------------------------------
        PocketDropPanel { id: drop; y: head.height; width: parent.width; height: parent.height - head.height }

        // ---- a collection's menu -------------------------------------------------------------------------------------
        ConfirmCard {
            id: delCard
            property string cid: ""
            title: "Delete this collection?"
            body: "The photos stay where they are; only the collection is removed."
            confirmText: "Delete"; cancelText: "Cancel"
            onAccepted: { collections.remove(cid) }
        }
        Popup {
            id: cmenu
            readonly property string cid: UiState.collMenu
            readonly property var c: { void collections.revision; return cid !== "" ? collections.get(cid) : ({}) }
            readonly property bool inside: { void photos.scopeKind; return photos.scopeKind === "collection" && photos.scopeId === cid }
            readonly property int idx: { var r = collections.rows; for (var i = 0; i < r.length; ++i) if (r[i].id === cid) return i; return 0 }
            parent: inner
            x: panel.fullW - 12 - width; y: head.height + 150 + idx * 34
            padding: 7; modal: true; dim: false; focus: true
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            onCidChanged: { if (cid !== "") open(); else if (opened) close() }
            onClosed: UiState.collMenu = ""
            enter: Transition { ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 170 }
                NumberAnimation { property: "scale"; from: 0.95; to: 1; duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.6 } } }
            exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 110 } }
            background: Glass { sourceItem: panel.Window.contentItem; radius: 14; tint: Qt.alpha(theme.s2, 0.92); saturation: 0.4; shadowAlphaA: 0.7; shadowAlphaB: 0; shadowOffsetA: 24; shadowInset: 16 }
            component MItem: Item {
                id: mi
                property string label; property bool off: false; property bool danger: false; property bool tick: false
                signal clicked()
                width: parent ? parent.width : 221; height: 32; opacity: off ? 0.4 : 1
                Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, mh2.hovered && !mi.off ? 0.08 : 0) }
                Text { x: 10; anchors.verticalCenter: parent.verticalCenter; text: mi.label; font.pixelSize: 13; color: mi.danger ? theme.rej : (mh2.hovered && !mi.off ? theme.fg : theme.fg2) }
                Icon { visible: mi.tick; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; name: "check"; size: 14; strokeWidth: 1.9; color: theme.accentFg }
                HoverHandler { id: mh2; enabled: !mi.off; cursorShape: Qt.PointingHandCursor }
                TapHandler { enabled: !mi.off; onTapped: { cmenu.close(); mi.clicked() } }
                Accessible.role: Accessible.MenuItem; Accessible.name: mi.label
            }
            contentItem: Column {
                width: 221
                MItem { label: "New collection\u2026"; onClicked: { UiState.collDlgId = ""; UiState.collDlg = "new" } }
                Item { width: parent.width; height: 13; Rectangle { x: 4; y: 6; width: parent.width - 8; height: 1; color: theme.lineSoft } }
                MItem { label: "Open collection"; off: cmenu.inside; onClicked: collections.open(cmenu.cid) }
                MItem { label: "Edit\u2026"; onClicked: { UiState.collDlgId = cmenu.cid; UiState.collDlg = "edit" } }
                MItem { label: "Add selection here"; off: cmenu.inside; onClicked: collections.addSelection(cmenu.cid, UiState.selRow) }
                MItem { label: "Add all " + photos.shown + " visible here"; off: cmenu.inside; onClicked: collections.addVisible(cmenu.cid) }
                MItem { label: "New winners go here"; tick: cmenu.c.id !== undefined && cmenu.cid === collections.activeId; onClicked: collections.toggleActive(cmenu.cid) }
                MItem { label: "Export\u2026" }
                Item { width: parent.width; height: 13; Rectangle { x: 4; y: 6; width: parent.width - 8; height: 1; color: theme.lineSoft } }
                MItem { label: "Remove selection from collection"; off: !cmenu.inside; onClicked: collections.removeSelection(cmenu.cid, UiState.selRow) }
                MItem { label: "Delete collection\u2026"; danger: true; onClicked: { delCard.cid = cmenu.cid; delCard.open() } }
            }
        }
    }
}
