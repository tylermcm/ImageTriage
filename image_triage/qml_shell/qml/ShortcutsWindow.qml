import QtQuick
import QtQuick.Controls

// The keyboard shortcuts guide: every key by category in two columns, searchable; keys the user changed in Settings show in blue. The model is
// reference_model.py (`reference`); this file draws its `view.keys` (mockup: scripts/qml_parity/help.dc.html).
Backdrop {
    id: root
    objectName: "shortcutsWindow"
    readonly property var v: reference.view
    readonly property var k: v.keys || ({ cats: [], none: false, count: 0, query: "" })
    shown: v.window === "keys"
    onDismissed: reference.close()
    z: 106

    readonly property real winW: Math.min(780, width - 64)
    readonly property real winH: Math.min(640, height - 72)
    // the categories fill two columns the way CSS columns do: top to bottom, then the next column, no category split
    readonly property var split: {
        var cats = k.cats, total = 0, h = []
        for (var i = 0; i < cats.length; ++i) { var ch = 12 + 14 + 4 + cats[i].rows.length * 34 + 6 + 14; h.push(ch); total += ch }
        var left = [], right = [], acc = 0
        for (var j = 0; j < cats.length; ++j) {
            if (acc + h[j] / 2 <= total / 2 + 1 || left.length === 0) { left.push(cats[j]); acc += h[j] } else right.push(cats[j])
        }
        return [left, right]
    }

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
                title: "Keyboard shortcuts"; sub: "Everything you can do without the mouse. Keys you have changed in Settings are shown in blue."
                iconPath: "M3 7h18v10H3zM7 11h.01M11 11h.01M15 11h.01M7 14h10"
                onCloseRequested: reference.close()
                extra: RSearch { width: 230; height: 34; placeholder: "Find a shortcut"; text: root.k.query; onEdited: (t) => reference.setKeyQuery(t) }
            }
            Flickable {
                id: body
                y: hd.height; width: parent.width; height: parent.height - hd.height - 60
                contentWidth: width; contentHeight: cols.height + 8 + 18; clip: true; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 6; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: theme.line } background: null }
                Row {
                    id: cols
                    x: 26; y: 8; width: body.width - 52; spacing: 16
                    visible: !root.k.none
                    Repeater {
                        model: root.split
                        delegate: Column {
                            required property var modelData
                            width: (cols.width - 16) / 2; spacing: 14
                            Repeater {
                                model: parent.modelData
                                delegate: Rectangle {
                                    id: cat
                                    required property var modelData
                                    width: parent.width; height: crow.height + 12 + 6; radius: 14; color: theme.s1; border.color: theme.lineSoft
                                    Column {
                                        id: crow
                                        x: 14; y: 12; width: parent.width - 28
                                        Text { height: 14; text: cat.modelData.name.toUpperCase(); color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84; bottomPadding: 0 }
                                        Item { width: 1; height: 4 }
                                        Repeater {
                                            model: cat.modelData.rows
                                            delegate: Item {
                                                id: kr
                                                required property var modelData
                                                required property int index
                                                width: crow.width; height: 34
                                                Rectangle { visible: kr.index > 0; width: parent.width; height: 1; color: theme.lineSoft }
                                                Text { anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 0.5; width: parent.width - kc.width - 12; elide: Text.ElideRight; text: kr.modelData.t; color: theme.fg2; font.pixelSize: 13 }
                                                Row { id: kc; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 4
                                                    Repeater { model: kr.modelData.keys; delegate: KeyCap { required property string modelData; text: modelData; changed: kr.modelData.changed } } }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                Column {
                    visible: root.k.none; x: 26; y: 8; width: body.width - 52; topPadding: 60 - 8
                    Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "No shortcut matches"; color: theme.fg; font.pixelSize: 15; font.weight: Font.Medium }
                    Text { width: parent.width; topPadding: 4; horizontalAlignment: Text.AlignHCenter; text: "Try another word, like “rename” or “zoom”."; color: theme.fg3; font.pointSize: UiState.pt(13.5) }
                }
            }
            Rectangle {
                y: parent.height - 60; width: parent.width; height: 60; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                Text { x: 26; anchors.verticalCenter: parent.verticalCenter; text: root.k.count + (root.k.count === 1 ? " shortcut" : " shortcuts"); color: theme.fg3; font.pointSize: UiState.pt(12.5) }
                Row { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { text: "Change shortcuts in Settings"; h: 34; hpad: 16; radius: 12; onClicked: { reference.go("settings") } }
                    SButton { text: "Done"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: reference.close() } }
            }
        }
    }
}
