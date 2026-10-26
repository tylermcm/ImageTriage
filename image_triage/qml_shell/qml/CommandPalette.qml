import QtQuick
import QtQuick.Controls
import QtQuick.Effects

// The command palette: a card floating near the top of the window, over the dimmed and blurred application. Layout, sizes and colours
// follow the HTML mockup; commands, ranking, shortcuts and recents come from `palette` (palette_model.py).
FocusScope {
    id: root
    objectName: "commandPalette"
    property Item sourceItem
    readonly property bool shown: UiState.paletteOpen
    anchors.fill: parent
    visible: opacity > 0.01
    opacity: shown ? 1 : 0
    z: 110
    Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

    readonly property string query: UiState.paletteQuery
    readonly property var items: { void cmdPalette.revision; return cmdPalette.results(query, UiState.paletteContext) }
    readonly property var rowIdx: { var r = []; for (var i = 0; i < items.length; ++i) if (items[i].kind === "row") r.push(i); return r }
    readonly property int cur: Math.max(0, Math.min(UiState.paletteSel, rowIdx.length - 1))
    readonly property real cardW: Math.min(680, width - 48)
    readonly property real cardTop: 96
    readonly property real maxH: Math.min(540, height - 160)
    readonly property real listPad: 6
    readonly property real headFirst: 24
    readonly property real headNext: 30
    readonly property real rowH: 44
    readonly property real emptyH: 169.7
    readonly property real contentH: {
        if (items.length === 0) return emptyH
        var h = 0
        for (var i = 0; i < items.length; ++i) h += items[i].kind === "hdr" ? (i === 0 ? headFirst : headNext) : rowH
        return h
    }
    readonly property real listH: Math.max(0, Math.min(contentH + 2 * listPad, maxH - 56 - 38 - 2))
    readonly property real cardH: 1 + 56 + listH + 38 + 1          // inside the card's 1px border

    function close() { UiState.paletteOpen = false }
    function run(i) {
        if (i < 0 || i >= items.length || items[i].kind !== "row") return
        var id = items[i].id
        close()
        cmdPalette.run(id)
    }
    function move(d) {
        if (rowIdx.length === 0) return
        UiState.paletteSel = (cur + d + rowIdx.length) % rowIdx.length
        list.positionViewAtIndex(rowIdx[UiState.paletteSel], ListView.Contain)
    }
    onShownChanged: if (shown) { input.forceActiveFocus(); list.positionViewAtBeginning() }
    Keys.onEscapePressed: root.close()

    // the dimmed, blurred application
    Item {
        id: scene
        anchors.fill: parent
        ShaderEffectSource { id: snap; anchors.fill: parent; sourceItem: root.sourceItem; live: true; visible: false }
        MultiEffect { anchors.fill: parent; source: snap; blurEnabled: true; blur: 1.0; blurMax: 20 }
        Rectangle { anchors.fill: parent; color: theme.scrim }
    }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onClicked: root.close(); onWheel: (w) => { w.accepted = true } }

    Item {
        id: card
        x: (root.width - width) / 2; y: root.cardTop - (root.shown ? 0 : 8)
        width: root.cardW; height: root.cardH
        scale: root.shown ? 1 : 0.985
        Behavior on y { NumberAnimation { duration: 260; easing.type: Easing.OutQuint } }
        Behavior on scale { NumberAnimation { duration: 260; easing.type: Easing.OutQuint } }
        Behavior on height { enabled: false }

        Shadow { anchors.fill: parent; radius: 18; offsetA: 48; insetA: 24; blurA: 64; multA: 3; alphaA: 0.85; offsetB: 12; insetB: 12; blurB: 24; alphaB: 0.5 }

        Rectangle { x: -1; y: -1; width: parent.width + 2; height: parent.height + 2; radius: 19; color: "transparent"; border.width: 1; border.color: theme.lineSoft }
        Item {
            anchors.fill: parent; clip: true
            // frosted glass of the dimmed window behind the card, then the surface tint
            Item { id: cardMask; anchors.fill: parent; visible: false; layer.enabled: true
                Rectangle { width: parent.width; height: parent.height; radius: 18 } }
            ShaderEffectSource {
                id: glassSrc; anchors.fill: parent; sourceItem: scene; live: true; visible: false
                sourceRect: Qt.rect(card.x, root.cardTop, card.width, card.height)
            }
            MultiEffect {
                anchors.fill: parent; source: glassSrc; blurEnabled: true; blur: 1.0; blurMax: 64; saturation: 0.4
                maskEnabled: true; maskSource: cardMask; maskThresholdMin: 0.5; maskSpreadAtMin: 1.0
            }
            Rectangle { width: parent.width; height: parent.height; radius: 18; color: Qt.alpha(theme.s2, theme.light ? 0.95 : 0.90)
                        border.width: 1; border.color: Qt.alpha(theme.lift, 0.11) }
            Item {   // inside the 1px border
                id: inner; x: 1; y: 1; width: parent.width - 2; height: parent.height - 2

            // ---- search row ---------------------------------------------------------------------------------------------
            Item {
                id: searchRow; width: parent.width; height: 56
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                Icon { x: 18; anchors.verticalCenter: parent.verticalCenter; size: 18; color: theme.fg3; name: "search" }
                TextInput {
                    id: input
                    x: 18 + 18 + 12; anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - x - 14 - escKey.width - (ctxChip.visible ? ctxChip.width + 12 : 0) - 12
                    color: theme.fg; font.pixelSize: 15; font.letterSpacing: -0.075; clip: true; selectByMouse: true
                    text: UiState.paletteQuery
                    onTextEdited: { UiState.paletteQuery = text; UiState.paletteSel = 0; list.positionViewAtBeginning() }
                    Keys.onPressed: (e) => {
                        if (e.key === Qt.Key_Down) { root.move(1); e.accepted = true }
                        else if (e.key === Qt.Key_Up) { root.move(-1); e.accepted = true }
                        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) { root.run(root.rowIdx[root.cur]); e.accepted = true }
                        else if (e.key === Qt.Key_Escape) { root.close(); e.accepted = true }
                    }
                    Text { visible: !parent.text; text: "Type a command, action, preset, or alias"; color: theme.fg3; font: parent.font }
                    Accessible.name: cmdPalette.title(UiState.paletteContext, "")
                }
                Rectangle {
                    id: ctxChip
                    visible: UiState.paletteContext !== "main"
                    height: 22; width: chipText.implicitWidth + 18; radius: 11
                    x: escKey.x - 12 - width; anchors.verticalCenter: parent.verticalCenter
                    color: Qt.alpha(theme.accent, 0.16)
                    Text { id: chipText; anchors.centerIn: parent; text: UiState.paletteContext === "preview" ? "Preview" : "Zen"; color: theme.accentFg
                           font.pointSize: UiState.pt(11); font.weight: Font.Medium }
                }
                Kbd { id: escKey; text: "Esc"; x: parent.width - 14 - width; anchors.verticalCenter: parent.verticalCenter }
            }

            // ---- results ------------------------------------------------------------------------------------------------
            Item {
                id: listBox; y: 56; width: parent.width; height: root.listH
                ListView {
                    id: list
                    x: root.listPad; y: 0; width: parent.width - 2 * root.listPad; height: parent.height; topMargin: root.listPad; bottomMargin: root.listPad
                    clip: true; boundsBehavior: Flickable.StopAtBounds; reuseItems: false
                    model: root.items
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 6; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: theme.line } background: null }
                    delegate: Item {
                        id: row
                        required property int index
                        required property var modelData
                        readonly property bool isHdr: modelData.kind === "hdr"
                        readonly property bool sel: !isHdr && root.rowIdx[root.cur] === index
                        width: ListView.view.width
                        height: isHdr ? (index === 0 ? root.headFirst : root.headNext) : root.rowH

                        // section header
                        Text {
                            visible: row.isHdr
                            x: 12; y: row.index === 0 ? 6 : 12; height: 13; verticalAlignment: Text.AlignVCenter
                            text: row.isHdr ? String(modelData.label).toUpperCase() : ""
                            color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84
                        }

                        // command row
                        Item {
                            visible: !row.isHdr
                            anchors.fill: parent
                            Rectangle {
                                anchors.fill: parent; radius: 12
                                color: row.sel ? Qt.alpha(theme.accent, 0.15) : Qt.alpha(theme.lift, rh.hovered ? 0.05 : 0)
                                border.width: row.sel ? 1 : 0; border.color: Qt.alpha(theme.accent, 0.30)
                            }
                            Rectangle {
                                id: tile; x: 10; anchors.verticalCenter: parent.verticalCenter; width: 30; height: 30; radius: 9
                                color: row.sel ? Qt.alpha(theme.accent, 0.22) : theme.s3
                                Icon { anchors.centerIn: parent; size: 16; color: row.sel ? theme.accentFg : theme.fg2; path: row.isHdr ? "" : root.iconPath(modelData.icon) }
                            }
                            Column {
                                x: 10 + 30 + 12; anchors.verticalCenter: parent.verticalCenter; spacing: 1
                                width: parent.width - x - 12 - (stateTag.visible ? stateTag.width + 12 : 0) - (keys.visible ? keys.width + 12 : 0)
                                Row {
                                    height: 18; clip: true
                                    Text { text: row.isHdr ? "" : modelData.pre; color: theme.fg; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter; height: 18 }
                                    Text { visible: !row.isHdr && modelData.hit !== ""; text: row.isHdr ? "" : modelData.hit; color: theme.accentFg; font.pixelSize: 13; font.weight: Font.DemiBold; verticalAlignment: Text.AlignVCenter; height: 18 }
                                    Text { text: row.isHdr ? "" : modelData.post; color: theme.fg; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter; height: 18 }
                                }
                                Text {
                                    visible: !row.isHdr && modelData.sub !== ""; height: 15; width: parent.width; elide: Text.ElideRight
                                    text: row.isHdr ? "" : modelData.sub; color: theme.fg3; font.pointSize: UiState.pt(11.5); verticalAlignment: Text.AlignVCenter
                                }
                            }
                            Row {
                                id: keys
                                visible: !row.isHdr && modelData.keys.length > 0
                                anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter; spacing: 3
                                Repeater { model: row.isHdr ? [] : modelData.keys; delegate: Kbd { required property string modelData; text: modelData } }
                            }
                            Rectangle {
                                id: stateTag
                                visible: !row.isHdr && modelData.state !== ""
                                height: 20; width: stText.implicitWidth + 16; radius: 10
                                x: (keys.visible ? keys.x : parent.width - 12) - 12 - width; anchors.verticalCenter: parent.verticalCenter
                                color: !row.isHdr && modelData.state === "On" ? Qt.alpha(theme.win, 0.14) : theme.s3
                                Text { id: stText; anchors.centerIn: parent; text: row.isHdr ? "" : modelData.state; font.pointSize: UiState.pt(11)
                                       color: !row.isHdr && modelData.state === "On" ? theme.win : theme.fg3 }
                            }
                            HoverHandler { id: rh; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: root.run(row.index) }
                            Accessible.role: Accessible.Button; Accessible.name: row.isHdr ? "" : modelData.pre + modelData.hit + modelData.post
                        }
                    }
                }

                // nothing matched
                Column {
                    visible: root.items.length === 0
                    anchors.horizontalCenter: parent.horizontalCenter; y: 6 + 46; spacing: 6
                    Icon { anchors.horizontalCenter: parent.horizontalCenter; size: 26; strokeWidth: 1.5; color: theme.fg3; opacity: 0.7; path: "M11 4.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM16 16l4 4M9 11h4" }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "No matching commands"; color: theme.fg; font.pointSize: UiState.pt(13.5); font.weight: Font.Medium }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Try “rename”, “theme” or “filter”"; color: theme.fg3; font.pixelSize: 12 }
                }
            }

            // ---- footer -------------------------------------------------------------------------------------------------
            Item {
                y: parent.height - 37; width: parent.width; height: 37
                Rectangle { y: -1; width: parent.width; height: 1; color: theme.lineSoft }
                Row {
                    x: 16; anchors.verticalCenter: parent.verticalCenter; spacing: 16
                    Row { spacing: 6; anchors.verticalCenter: parent.verticalCenter
                        Row { spacing: 4; anchors.verticalCenter: parent.verticalCenter; Kbd { text: "↑" } Kbd { text: "↓" } }
                        Text { text: "Navigate"; color: theme.fg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter } }
                    Row { spacing: 6; anchors.verticalCenter: parent.verticalCenter; Kbd { text: "Enter" }
                        Text { text: "Run"; color: theme.fg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter } }
                    Row { spacing: 6; anchors.verticalCenter: parent.verticalCenter; Kbd { text: "Esc" }
                        Text { text: "Close"; color: theme.fg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter } }
                }
                Text { anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
                       text: cmdPalette.countText(root.query, UiState.paletteContext); color: theme.fg3; font.pointSize: UiState.pt(11.5) }
            }
            }
        }
    }

    function iconPath(name) {
        return ({
            file: "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z",
            edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
            tools: "M14.5 6.5a4 4 0 005 5L10 21a2.1 2.1 0 01-3-3zM14 7l3-3 3 3-3 3",
            "export": "M12 15V4M8 8l4-4 4 4M5 14v4a2 2 0 002 2h10a2 2 0 002-2v-4",
            library: "M5 4v16M10 4v16M15 6l4 14",
            review: "M12 3a9 9 0 100 18 9 9 0 000-18zM8 12.5l3 3 5-6",
            view: "M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z",
            search: "M11 4.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM16 16l4 4",
            ai: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z",
            workspace: "M5 4h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2zM9 4v16",
            help: "M12 3a9 9 0 100 18 9 9 0 000-18zM9.5 9.5a2.5 2.5 0 114 2c-.8.6-1.5 1-1.5 2M12 17h.01",
            theme: "M12 8a4 4 0 100 8 4 4 0 000-8zM12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"
        })[name] || ""
    }
}
