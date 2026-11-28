import QtQuick
import QtQuick.Controls

// Quick Export (quick_export_model.py): a preset, where the copies go, Export. Opened by the grid's Export…, the viewer's Export, the
// rail's Export and Ctrl+E. The presets are Batch Export's (Instagram's shapes, smaller copies, full size, and the person's own);
// "More options…" opens Batch Export with the chosen one filled in. Up and Down choose a preset, Return exports, Esc closes.
Backdrop {
    id: root
    objectName: "quickExportDialog"
    shown: UiState.quickExportOpen
    readonly property var presets: quickExport.presets
    readonly property string chosen: quickExport.preset
    readonly property string summary: { void quickExport.count; void quickExport.dest; void quickExport.only; return quickExport.summary() }
    // the list with a heading before each group: {head} or {name, desc}
    readonly property var lines: {
        var out = [], group = ""
        for (var i = 0; i < presets.length; ++i) {
            if (presets[i].group !== group) { group = presets[i].group; out.push({ head: group }) }
            out.push(presets[i])
        }
        return out
    }
    readonly property bool canExport: quickExport.count > 0 && !quickExport.busy && chosen !== ""

    function close() { UiState.quickExportOpen = false; UiState.quickOnly = "" }
    function exportNow() {
        if (!canExport) return
        if (quickExport.run()) close()
    }
    function step(delta) {
        var names = presets.map(p => p.name), i = names.indexOf(chosen)
        var next = Math.max(0, Math.min(names.length - 1, (i < 0 ? 0 : i) + delta))
        if (names.length) quickExport.choose(names[next])
    }
    // Batch Export with every setting, filled in from the chosen preset, for the same photos
    function moreOptions() {
        var only = UiState.quickOnly
        batch.usePreset(chosen)
        close()
        UiState.batchOnly = only
        UiState.batchMode = "export"
    }
    onShownChanged: {
        if (!shown) return
        quickExport.begin(UiState.quickOnly)
        forceActiveFocus()
        list.contentY = 0
        Qt.callLater(root.reveal)
    }
    // the chosen preset in view (it may be far down the list: one of the person's own)
    function reveal() {
        var item = rows.itemAt(lines.findIndex(l => l.name === chosen))
        if (item && item.y + item.height > list.contentY + list.height) list.contentY = Math.min(list.contentHeight - list.height, item.y - 8)
    }
    onDismissed: close()
    Keys.onReturnPressed: exportNow()
    Keys.onEnterPressed: exportNow()
    Keys.onUpPressed: { step(-1); reveal() }
    Keys.onDownPressed: { step(1); reveal() }
    Connections {
        target: quickExport
        function onSentToPocketDrop(count) { UiState.tab = "drop" }
    }

    DialogCard {
        shown: root.shown
        width: 480; height: col.implicitHeight + 46
        x: (parent.width - width) / 2; y: (parent.height - height) / 2
        Column {
            id: col
            x: 23; y: 23; width: parent.width - 46
            Text { width: parent.width; text: "Quick export"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15; height: 24 }
            Text { objectName: "quickExportSummary"; width: parent.width; elide: Text.ElideMiddle; text: root.summary; color: theme.fg3; font.pixelSize: 13; height: 20 }
            Item { width: 1; height: 12 }
            Rectangle {
                width: parent.width; height: Math.min(340, rowsCol.implicitHeight + 12); radius: 12; color: theme.base; border.color: theme.lineSoft
                Flickable {
                    id: list
                    objectName: "quickExportPresets"
                    anchors.fill: parent; anchors.margins: 6
                    contentWidth: width; contentHeight: rowsCol.implicitHeight
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { id: lbar; policy: ScrollBar.AsNeeded
                        contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: lbar.active || lbar.hovered ? 1 : 0 }
                        background: Item {} }
                    Column {
                        id: rowsCol
                        width: list.width
                        Repeater {
                            id: rows
                            model: root.lines
                            delegate: Item {
                                id: line
                                required property var modelData
                                readonly property bool head: modelData.head !== undefined
                                readonly property bool on: !head && modelData.name === root.chosen
                                objectName: head ? "" : "quickPreset-" + modelData.name
                                width: rowsCol.width; height: head ? 28 : 40
                                Text { visible: line.head; x: 10; anchors.bottom: parent.bottom; anchors.bottomMargin: 5; text: line.head ? line.modelData.head.toUpperCase() : ""
                                       color: theme.fg3; font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.6 }
                                Rectangle { visible: !line.head; anchors.fill: parent; radius: 8
                                            color: line.on ? Qt.tint(theme.base, Qt.alpha(theme.accent, 0.14)) : (rh.hovered ? Qt.alpha(theme.fg, 0.05) : "transparent")
                                            border.width: line.on ? 1 : 0; border.color: Qt.alpha(theme.accent, 0.6) }
                                Rectangle { visible: !line.head; x: 12; anchors.verticalCenter: parent.verticalCenter; width: 14; height: 14; radius: 7; color: "transparent"
                                            border.width: line.on ? 4.5 : 1.5; border.color: line.on ? theme.accent : theme.fg3 }
                                // the name takes what the description leaves (the description is short: a size and a format)
                                Text { visible: !line.head; x: 36; width: parent.width - 36 - descText.width - 24; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight
                                       text: line.head ? "" : line.modelData.name; color: theme.fg; font.pixelSize: 13; font.weight: line.on ? Font.Medium : Font.Normal }
                                Text { id: descText; visible: !line.head; anchors.right: parent.right; anchors.rightMargin: 12; width: Math.min(implicitWidth, parent.width * 0.45)
                                       anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight; text: line.head ? "" : line.modelData.desc; color: theme.fg3; font.pixelSize: 12 }
                                HoverHandler { id: rh; enabled: !line.head; cursorShape: Qt.PointingHandCursor }
                                TapHandler { enabled: !line.head; onTapped: quickExport.choose(line.modelData.name); onDoubleTapped: { quickExport.choose(line.modelData.name); root.exportNow() } }
                                Accessible.role: line.head ? Accessible.Heading : Accessible.RadioButton
                                Accessible.name: line.head ? line.modelData.head : line.modelData.name + ", " + line.modelData.desc
                                Accessible.checked: line.on
                            }
                        }
                    }
                }
            }
            Item { width: 1; height: 14 }
            Item {
                width: parent.width; height: 34
                Text { anchors.verticalCenter: parent.verticalCenter; text: "Save to"; color: theme.fg2; font.pixelSize: 13 }
                Seg { objectName: "quickExportDest"; anchors.right: parent.right; width: 300; height: 34; fill: true; showExtras: false
                      labels: ["Export folder", "PocketDrop"]
                      current: quickExport.dest === "pocketdrop" ? 1 : 0
                      onActivated: (i) => quickExport.setDest(i === 1 ? "pocketdrop" : "folder") }
            }
            Text { width: parent.width; wrapMode: Text.WordWrap; topPadding: 6; color: theme.fg3; font.pixelSize: 12; lineHeight: 16.5; lineHeightMode: Text.FixedHeight
                   text: quickExport.dest === "pocketdrop" ? "The copies are added to PocketDrop, ready to send to your phone. Nothing is added to your folders."
                                                            : "The copies go in a folder named Export beside the photos. The originals are not changed." }
            Item { width: 1; height: 18 }
            Item {
                width: parent.width; height: 34
                SButton { objectName: "quickExportMore"; anchors.left: parent.left; text: "More options…"; variant: "ghost"; h: 34; hpad: 12; radius: 12
                          enabled: root.chosen !== ""; onClicked: root.moreOptions() }
                Row { anchors.right: parent.right; spacing: 8
                    SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: root.close() }
                    SButton { objectName: "quickExportGo"; variant: "primary"; h: 34; hpad: 18; radius: 12; enabled: root.canExport; onClicked: root.exportNow()
                              text: quickExport.dest === "pocketdrop" ? "Send to PocketDrop" : "Export" } }
            }
        }
    }
}
