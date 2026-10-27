import QtQuick
import QtQuick.Controls

// Help: a rail of topics with a search box, the article for the chosen topic (or About with the update check), and a footer. The model is
// reference_model.py (`reference`); this file draws its `view.help` (mockup: scripts/qml_parity/help.dc.html).
Backdrop {
    id: root
    objectName: "helpWindow"
    readonly property var v: reference.view
    readonly property var h: v.help || ({ topics: [], blocks: [], links: [], kind: "none", query: "", title: "", lead: "", version: "", upColor: "fg3", upTitle: "", upBody: "", upBtn: "", upPrimary: false })
    shown: v.window === "help"
    plain: UiState.settingsOpen            // opened from Settings: it takes the settings window's place, over the same dimmed backdrop
    onDismissed: reference.close()
    onOpacityChanged: UiState.guideCovers = shown && plain && opacity > 0.98
    onShownChanged: if (!shown) UiState.guideCovers = false
    z: 106

    readonly property real winW: plain ? Math.min(1060, width - 96) : Math.min(920, width - 64)      // the settings window's own size when it stands in for it
    readonly property real winH: plain ? Math.min(700, height - 68 - 64) : Math.min(640, height - 72)
    function dotColor(n) { return n === "win" ? theme.win : n === "rev" ? theme.rev : n === "rej" ? theme.rej : n === "accent" ? theme.accentFg : theme.fg3 }

    DialogCard {
        id: win
        shown: root.shown
        flat: root.plain; radius: root.plain ? 14 : 16
        width: root.winW; height: root.winH
        x: (parent.width - width) / 2; y: root.plain ? 42 + (parent.height - 68 - height) / 2 : (parent.height - height) / 2
        RoundClip {
            anchors.fill: parent; anchors.margins: 1; radius: win.radius - 1
            Rectangle { anchors.fill: parent; color: Qt.alpha(theme.base, 0.97) }
            // ---- the rail ----
            Rectangle {
                id: rail
                width: 240; height: parent.height; color: theme.s1
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }
                Row { x: 18; y: 18; spacing: 10
                    Rectangle { width: 30; height: 30; radius: 10; color: Qt.tint(theme.s2, Qt.alpha(theme.accent, 0.16)); border.color: Qt.alpha(theme.accent, 0.28)
                        Icon { anchors.centerIn: parent; path: "M9.5 9a2.5 2.5 0 115 0c0 1.7-2.5 2-2.5 4M12 17v.01M12 21a9 9 0 100-18 9 9 0 000 18z"; size: 16; strokeWidth: 1.8; color: theme.accentFg } }
                    Text { anchors.verticalCenter: parent.verticalCenter; text: "Help"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15 }
                }
                RSearch { id: search; x: 12; y: 62; width: parent.width - 24; placeholder: "Search help"; text: root.h.query; onEdited: (t) => reference.setQuery(t) }
                Flickable {
                    id: list
                    x: 12; y: 108; width: parent.width - 24; height: parent.height - 108 - 12 - 34 - 8 - 2
                    contentWidth: width; contentHeight: col.height; clip: true; boundsBehavior: Flickable.StopAtBounds
                    Column {
                        id: col
                        width: list.width; spacing: 1
                        Repeater {
                            model: root.h.topics
                            delegate: Item {
                                id: row
                                required property var modelData
                                width: col.width; height: modelData.sep ? 15 : 34
                                Rectangle { visible: row.modelData.sep; x: 6; y: 8; width: parent.width - 12; height: 1; color: theme.lineSoft }
                                Rectangle { visible: !row.modelData.sep; anchors.fill: parent; radius: 9
                                    color: row.modelData.cur ? Qt.alpha(theme.accent, 0.13) : Qt.alpha(theme.lift, th.hovered ? 0.06 : 0) }
                                Rectangle { visible: !row.modelData.sep && row.modelData.cur; x: 0; y: 7; width: 2; height: parent.height - 14; radius: 1; color: theme.accent }
                                Icon { visible: !row.modelData.sep; x: 10; anchors.verticalCenter: parent.verticalCenter; path: row.modelData.d || ""; size: 16; strokeWidth: 1.7; color: !!row.modelData.cur || th.hovered ? theme.fg : theme.fg2 }
                                Text { visible: !row.modelData.sep; x: 36; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 36 - ((row.modelData.n || "") !== "" ? 32 : 10); elide: Text.ElideRight
                                       text: row.modelData.title || ""; color: row.modelData.cur || th.hovered ? theme.fg : theme.fg2; font.pixelSize: 13 }
                                Text { visible: (row.modelData.n || "") !== ""; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; text: row.modelData.n || ""; color: theme.fg3; font.pointSize: UiState.pt(11) }
                                HoverHandler { id: th; enabled: !row.modelData.sep; cursorShape: Qt.PointingHandCursor }
                                TapHandler { enabled: !row.modelData.sep; onTapped: reference.setTopic(row.modelData.id) }
                                Accessible.role: Accessible.Button; Accessible.name: row.modelData.title || ""
                            }
                        }
                        Text { visible: root.h.topics.length === 0; x: 10; width: parent.width - 20; topPadding: 14; text: "No topic matches."; color: theme.fg3; font.pointSize: UiState.pt(12.5) }
                    }
                }
                Item {
                    x: 12; y: parent.height - 12 - 34; width: parent.width - 24; height: 34
                    Rectangle { anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, qh.hovered ? 0.06 : 0) }
                    Icon { x: 10; anchors.verticalCenter: parent.verticalCenter; path: "M3 7h18v10H3zM7 11h.01M11 11h.01M15 11h.01M7 14h10"; size: 16; strokeWidth: 1.7; color: qh.hovered ? theme.fg : theme.fg2 }
                    Text { x: 36; anchors.verticalCenter: parent.verticalCenter; text: "Keyboard shortcuts"; color: qh.hovered ? theme.fg : theme.fg2; font.pixelSize: 13 }
                    HoverHandler { id: qh; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: reference.open("keys") }
                    Accessible.role: Accessible.Button; Accessible.name: "Keyboard shortcuts"
                }
            }
            // ---- the article ----
            Item {
                id: main
                x: rail.width; width: parent.width - rail.width; height: parent.height
                Flickable {
                    id: art
                    width: parent.width; height: parent.height - 56
                    contentWidth: width; contentHeight: body.height + 46; clip: true; boundsBehavior: Flickable.StopAtBounds
                    onContentHeightChanged: if (contentY > contentHeight - height) contentY = Math.max(0, contentHeight - height)
                    property string key: root.h.title || ""
                    onKeyChanged: contentY = 0
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 6; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: theme.line } background: null }
                    Column {
                        id: body
                        x: 36; y: 26; width: art.width - 72
                        // an article
                        Column {
                            visible: root.h.kind === "article"; width: parent.width
                            Text { width: parent.width; text: root.h.title; color: theme.fg; font.pixelSize: 22; font.weight: Font.DemiBold; font.letterSpacing: -0.44; lineHeight: 27.5; lineHeightMode: Text.FixedHeight }
                            Text { width: Math.min(600, parent.width); topPadding: 8; text: root.h.lead; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 14; lineHeight: 22.4; lineHeightMode: Text.FixedHeight }
                            Repeater {
                                model: root.h.kind === "article" ? root.h.blocks : []
                                delegate: Loader {
                                    required property var modelData
                                    required property int index
                                    width: body.width
                                    property var blk: modelData
                                    sourceComponent: modelData.kind === "h" ? hC : modelData.kind === "p" ? pC : modelData.kind === "keys" ? kC : modelData.kind === "tip" ? tC : bC
                                }
                            }
                        }
                        // About
                        Column {
                            visible: root.h.kind === "about"; width: parent.width
                            Item { width: parent.width; height: 10 + 84 + 6 + 16 + 28 + 2 + 18
                                Image { x: (parent.width - 84) / 2; y: 10; width: 84; height: 84; source: Qt.resolvedUrl("../../ui/assets/app_icon-v4.png"); sourceSize: Qt.size(168, 168); fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true }
                                Text { y: 10 + 84 + 16; width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "Image Triage"; color: theme.fg; font.pixelSize: 22; font.weight: Font.DemiBold; font.letterSpacing: -0.44 }
                                Text { y: 10 + 84 + 16 + 28 + 2; width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "Version " + root.h.version; color: theme.fg3; font.pixelSize: 13 } }
                            Text { x: (parent.width - Math.min(600, parent.width)) / 2; width: Math.min(600, parent.width); topPadding: 13; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 14; lineHeight: 22.4; lineHeightMode: Text.FixedHeight
                                   text: "A desktop photo triage tool built for speed and keyboard-driven flow. Sort, rate and cull large shoots quickly, then hand off or export the keepers." }
                            Item { width: 1; height: 15 }
                            Rectangle {
                                x: (parent.width - width) / 2; width: Math.min(420, parent.width); height: Math.max(upc.implicitHeight, 28) + 22; radius: 13; color: theme.s1; border.color: theme.lineSoft
                                Rectangle { x: 14; anchors.verticalCenter: parent.verticalCenter; width: 9; height: 9; radius: 5; color: root.dotColor(root.h.upColor) }
                                Column { id: upc; x: 35; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 35 - 14 - upb.width - 12
                                    Text { width: parent.width; text: root.h.upTitle; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; lineHeight: 20.15; lineHeightMode: Text.FixedHeight }
                                    Text { width: parent.width; text: root.h.upBody; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 13; lineHeight: 18; lineHeightMode: Text.FixedHeight } }
                                SButton { id: upb; anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: root.h.upBtn; variant: root.h.upPrimary ? "primary" : "normal"; h: 28; hpad: 11; radius: 9
                                          onClicked: reference.checkUpdates() }
                                // the gap above, like margin: 18px auto 0
                            }
                            Item { width: 1; height: 18 }
                            Column {
                                x: (parent.width - width) / 2; width: Math.min(420, parent.width); spacing: 2
                                Repeater {
                                    model: root.h.links
                                    delegate: Item {
                                        id: lk
                                        required property var modelData
                                        width: parent.width; height: 36
                                        Rectangle { anchors.fill: parent; radius: 10; color: Qt.alpha(theme.lift, lh.hovered ? 0.07 : 0) }
                                        Icon { x: 12; anchors.verticalCenter: parent.verticalCenter; path: lk.modelData.d; size: 16; strokeWidth: 1.7; color: lh.hovered ? theme.fg : theme.fg2 }
                                        Text { x: 38; anchors.verticalCenter: parent.verticalCenter; text: lk.modelData.t; color: lh.hovered ? theme.fg : theme.fg2; font.pixelSize: 13 }
                                        HoverHandler { id: lh; cursorShape: Qt.PointingHandCursor }
                                        TapHandler { onTapped: reference.openLink(lk.modelData.id) }
                                        Accessible.role: Accessible.Button; Accessible.name: lk.modelData.t
                                    }
                                }
                            }
                        }
                        // nothing found
                        Column {
                            visible: root.h.kind === "none"; width: parent.width; topPadding: 60 - 26 + 34
                            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "Nothing found"; color: theme.fg; font.pixelSize: 15; font.weight: Font.Medium }
                            Text { width: parent.width; topPadding: 4; horizontalAlignment: Text.AlignHCenter; text: "Try a different word, or pick a topic on the left."; color: theme.fg3; font.pointSize: UiState.pt(13.5) }
                        }
                    }
                }
                Rectangle {
                    y: parent.height - 56; width: parent.width; height: 56; color: theme.s1
                    Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 36; anchors.verticalCenter: parent.verticalCenter; text: "Press F1 any time to open Help."; color: theme.fg3; font.pointSize: UiState.pt(12.5) }
                    Row { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                        SButton { text: "Keyboard shortcuts"; h: 32; hpad: 14; radius: 11; onClicked: reference.open("keys") }
                        SButton { text: UiState.settingsOpen ? "Back to settings" : "Done"; variant: "primary"; h: 32; hpad: 14; radius: 11; onClicked: reference.close() } }
                }
            }
        }
    }

    // ---- the article's blocks ----
    Component { id: hC
        Item { height: 22 + hl.height; width: parent ? parent.width : 0
            Text { id: hl; y: 22; width: parent.width; bottomPadding: 0; text: blk.t; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; height: 17 } } }
    Component { id: pC
        Item { height: 6 + pl.implicitHeight; width: parent ? parent.width : 0
            Text { id: pl; y: 6; width: Math.min(600, parent.width); text: blk.t; wrapMode: Text.WordWrap; color: theme.fg2; font.pointSize: UiState.pt(13.5); lineHeight: 22.275; lineHeightMode: Text.FixedHeight } } }
    Component { id: kC
        Item { height: 12 + box.height; width: parent ? parent.width : 0
            Rectangle { id: box; y: 12; width: Math.min(600, parent.width); height: krows.height; radius: 12; color: theme.s1; border.color: theme.lineSoft
                Column { id: krows; width: parent.width
                    Repeater { model: blk.rows
                        delegate: Item { id: kr; required property var modelData; required property int index; width: krows.width; height: 38
                            Rectangle { visible: kr.index > 0; width: parent.width; height: 1; color: theme.lineSoft }
                            Text { x: 14; anchors.verticalCenter: parent.verticalCenter; text: kr.modelData.t; color: theme.fg2; font.pixelSize: 13 }
                            Row { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 4
                                Repeater { model: kr.modelData.keys; delegate: KeyCap { required property string modelData; text: modelData } } } } } } } } }
    Component { id: tC
        Item { height: 16 + tip.height; width: parent ? parent.width : 0
            Rectangle { id: tip; y: 16; width: Math.min(600, parent.width); height: tt.implicitHeight + 24; radius: 12; color: Qt.tint(theme.s1, Qt.alpha(theme.accent, 0.09)); border.color: Qt.alpha(theme.accent, 0.24)
                Icon { x: 14; y: 14; path: "M12 8v5M12 16.5v.01M12 21a9 9 0 100-18 9 9 0 000 18z"; size: 17; strokeWidth: 1.8; color: theme.accentFg }
                Text { id: tt; x: 42; y: 12; width: parent.width - 42 - 14; text: blk.t; wrapMode: Text.WordWrap; color: theme.fg2; font.pointSize: UiState.pt(13); lineHeight: 20.15; lineHeightMode: Text.FixedHeight } } } }
    Component { id: bC
        Item { height: 16 + 34; width: parent ? parent.width : 0
            SButton { y: 16; text: blk.t; h: 34; hpad: 16; radius: 12; onClicked: reference.go(blk.to) } } }
}
