import QtQuick
import QtQuick.Shapes

// The panel beside the rail: Library, Faces, Collections, PocketDrop. Opening animates the column's width (in Main); the contents
// slide and fade with it, and each page's blocks rise in one after another when it becomes the current page.
Item {
    id: panel
    property real fullW: 300
    readonly property real p: Math.max(0, Math.min(1, width / fullW))
    clip: true

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
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: facesCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 250 } }
            Column {
                id: facesCol; width: parent.width; topPadding: 12
                Stag { active: faces.on; n: 0; Column { width: parent.width
                    SectionLabel { caption: "People" }
                    Grid {
                        x: 16; y: 0; topPadding: 6; bottomPadding: 6; columns: 3; columnSpacing: 8; rowSpacing: 14
                        Avatar { initial: "A"; label: "Anna"; count: 214; hue: 20 }
                        Avatar { initial: "M"; label: "Mark"; count: 187; hue: 150 }
                        Avatar { initial: "L"; label: "Lena"; count: 142; hue: 260 }
                        Avatar { initial: "J"; label: "Jonas"; count: 96; hue: 330 }
                        Avatar { initial: "?"; label: "Unnamed 1"; count: 61; hue: 200 }
                        Avatar { initial: "?"; label: "Unnamed 2"; count: 44; hue: 80 }
                    } } }
                Stag { active: faces.on; n: 1; Column { width: parent.width
                    SectionLabel { caption: "Indexing" }
                    Item { width: 1; height: 1 }
                    Meter { x: 16; width: parent.width - 32; value: 0.86 }
                    PanelRow { text: "1,104 of 1,284 photos scanned"; count: "86%"; interactive: false; muted: true } } }
            }
        }

        // ---- Collections ---------------------------------------------------------------------------------------------
        Flickable {
            id: coll
            readonly property bool on: UiState.tab === "coll"
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: collCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 250 } }
            Column {
                id: collCol; width: parent.width; topPadding: 12
                Stag { active: coll.on; n: 0; Column { width: parent.width
                    SectionLabel { caption: "Winners" }
                    PanelRow { icon: "star"; text: "All winners"; count: "96"; current: true } } }
                Stag { active: coll.on; n: 1; Column { width: parent.width
                    SectionLabel { caption: "Projects" }
                    Repeater {
                        model: [["Album selects", "84", [1, 6, 10]], ["Print order", "24", [4, 2, 7]], ["Portfolio", "18", [11, 0, 8]]]
                        delegate: Item {
                            required property var modelData
                            width: parent.width; height: 48.4
                            Rectangle { anchors.fill: parent; color: Qt.alpha(theme.lift, ph.hovered ? 0.05 : 0) }
                            Item { x: 16; y: 7.2; width: 34 + 2 * 22; height: 34
                                Repeater { model: 3
                                    delegate: Item { required property int index; x: index * 22; width: 34; height: 34
                                        Rectangle { anchors.fill: parent; radius: 7; color: theme.s2 }                    // the 2px border
                                        RoundClip { x: 2; y: 2; width: 30; height: 30; radius: 5
                                            Rectangle { anchors.fill: parent; color: theme.s3 }
                                            ThumbImage { anchors.fill: parent; bucket: 160
                                                path: { void photos.revision; var it = photos.get(modelData[2][index] % Math.max(1, photos.shown)); return it && it.path ? it.path : "" } } } } } }
                            Column { x: 94; y: 6
                                Text { height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData[0]; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                Text { height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData[1] + " photos"; color: theme.fg3; font.pointSize: UiState.pt(10.8333) } }
                            HoverHandler { id: ph; cursorShape: Qt.PointingHandCursor }
                        }
                    } } }
            }
        }

        // ---- PocketDrop ----------------------------------------------------------------------------------------------
        Flickable {
            id: drop
            readonly property bool on: UiState.tab === "drop"
            y: head.height; width: parent.width; height: parent.height - head.height
            contentHeight: dropCol.implicitHeight + 24; clip: true; boundsBehavior: Flickable.StopAtBounds
            opacity: on ? 1 : 0; visible: opacity > 0.01
            Behavior on opacity { NumberAnimation { duration: 250 } }
            Column {
                id: dropCol; width: parent.width; topPadding: 12
                Stag { active: drop.on; n: 0; Column { width: parent.width
                    SectionLabel { caption: "Device" }
                    Item { width: parent.width; height: 50.4
                        Rectangle { x: 12; y: 14.2; width: 16; height: 16; radius: 8; color: Qt.alpha(theme.win, 0.22) }       // 0 0 0 4px, 22%
                        Rectangle { x: 16; y: 18.2; width: 8; height: 8; radius: 4; color: theme.win }
                        Column { x: 34; y: 4
                            Text { height: 18.2; verticalAlignment: Text.AlignVCenter; text: "Pixel 8 Pro"; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                            Text { height: 18.2; verticalAlignment: Text.AlignVCenter; text: "Connected over Wi-Fi"; color: theme.fg3; font.pixelSize: 13 } } } } }
                Stag { active: drop.on; n: 1
                    Item { width: parent.width; height: 6 + 74.4
                        Rectangle { x: 16; y: 6; width: parent.width - 32; height: 74.4; radius: 12; color: dz.hovered ? Qt.alpha(theme.accent, 0.08) : "transparent"
                            Behavior on color { ColorAnimation { duration: 200 } }
                            Shape { id: dashed; anchors.fill: parent; layer.enabled: true; layer.samples: 4   // dashes need the geometry renderer
                                ShapePath { strokeColor: dz.hovered ? theme.accent : theme.line; strokeWidth: 1; fillColor: "transparent"; strokeStyle: ShapePath.DashLine; dashPattern: [3, 3]
                                    PathSvg { path: Icons.rect(0.5, 0.5, dashed.width - 1, dashed.height - 1, 11.5) } } }
                            Text { anchors.centerIn: parent; width: parent.width - 26; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; lineHeight: 18.2; lineHeightMode: Text.FixedHeight
                                   text: "Drop photos here to send to your phone"; color: theme.fg3; font.pixelSize: 13 }
                            HoverHandler { id: dz } } } }
                Stag { active: drop.on; n: 2; Column { width: parent.width
                    SectionLabel { caption: "Transfers" }
                    Repeater {
                        model: [["Album selects.zip", "64% \u00b7 18 MB/s", 0.64, true], ["DSC_4207.jpg", "Sent", 1.0, false], ["DSC_4240.jpg", "Sent", 1.0, false], ["Client selects", "Waiting", 0.0, false]]
                        delegate: Item {
                            required property var modelData
                            width: parent.width; height: 42.2
                            Text { x: 16; y: 7; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData[0]; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                            Text { anchors.right: parent.right; anchors.rightMargin: 16; y: 7; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData[1]; color: theme.fg3; font.pointSize: UiState.pt(11) }
                            Meter { x: 16; y: 32.2; width: parent.width - 32; height: 3; value: modelData[2]; spanTrack: false; stops: modelData[3] ? [theme.accent, "#90d4ff", theme.accent] : [theme.accent] }
                        }
                    } } }
            }
        }
    }
}
