import QtQuick

// The floating button bar: frosted glass over the grid, menus above it, the sliding filter, thumbnail size, and the AI chip.
Item {
    id: bar
    property Item sourceItem
    property real avail: 1000           // width of the area it floats in; labels and groups drop out as it narrows
    property int row: 0                 // the grid's current row (what Review acts on)
    readonly property bool labels: avail >= 1080
    readonly property bool sizeSwitch: avail >= 640
    readonly property var filterKeys: ["all", "win", "keep", "rej"]

    width: Math.min(content.implicitWidth + 10, avail - 24)
    height: 46

    Glass { anchors.fill: parent; sourceItem: bar.sourceItem; radius: 20 }

    Row {
        id: content
        x: 5; y: 5; spacing: 4
        FbButton {
            id: review; icon: "check"; label: "Review"; showLabel: bar.labels; caret: true; open: reviewMenu.opened
            onClicked: reviewMenu.opened ? reviewMenu.close() : reviewMenu.open()
            Popover {
                id: reviewMenu
                PopItem { label: "Mark winner"; icon: "star"; kbd: "W"; onClicked: { photos.setDecision(bar.row, "win"); reviewMenu.close() } }
                PopItem { label: "Keep"; icon: "check"; kbd: "K"; onClicked: { photos.setDecision(bar.row, "keep"); reviewMenu.close() } }
                PopItem { label: "Reject"; icon: "x"; kbd: "X"; onClicked: { photos.setDecision(bar.row, "rej"); reviewMenu.close() } }
                PopDivider {}
                PopItem { label: "Clear decision"; icon: "minus"; kbd: "U"; onClicked: { photos.setDecision(bar.row, ""); reviewMenu.close() } }
            }
        }
        FbButton {
            id: view; icon: "view"; label: "View"; showLabel: bar.labels; caret: true; open: viewMenu.opened
            onClicked: viewMenu.opened ? viewMenu.close() : viewMenu.open()
            Popover {
                id: viewMenu
                PopItem { label: "Filenames"; mode: "switch"; checked: UiState.showNames; onClicked: UiState.showNames = !UiState.showNames }
                PopItem { label: "Decision badges"; mode: "switch"; checked: UiState.showBadges; onClicked: UiState.showBadges = !UiState.showBadges }
            }
        }
        FbButton {
            id: sortBtn; icon: "sort"; label: "Sort"; showLabel: bar.labels; caret: true; open: sortMenu.opened
            onClicked: sortMenu.opened ? sortMenu.close() : sortMenu.open()
            Popover {
                id: sortMenu
                Repeater {
                    model: [["name", "Name"], ["ai", "AI score"], ["rating", "Rating"], ["decision", "Decision"]]
                    delegate: PopItem {
                        required property var modelData
                        label: modelData[1]; mode: "tick"; checked: UiState.sort === modelData[0]
                        onClicked: { UiState.sort = modelData[0]; photos.setSort(modelData[0]); sortMenu.close() }
                    }
                }
            }
        }
        Rectangle { width: 1; height: 22; y: 7; color: theme.line }
        Seg {
            labels: ["All", "Winners", "Keep", "Rejected"]
            extras: [photos.total, photos.winCount, photos.keepCount, photos.rejCount]
            showExtras: bar.avail >= 860
            padH: bar.avail >= 860 ? 12 : 9
            current: Math.max(0, bar.filterKeys.indexOf(UiState.filter))
            onActivated: (i) => { UiState.filter = bar.filterKeys[i]; photos.setFilter(bar.filterKeys[i] === "all" ? "all" : bar.filterKeys[i]) }
        }
        Rectangle { visible: bar.sizeSwitch; width: 1; height: 22; y: 7; color: theme.line }
        Seg {
            visible: bar.sizeSwitch
            icons: ["densS", "densM", "densL"]; padH: 9
            current: UiState.dens === "s" ? 0 : UiState.dens === "m" ? 1 : 2
            onActivated: (i) => { UiState.dens = ["s", "m", "l"][i] }
        }
        Rectangle { width: 1; height: 22; y: 7; color: theme.line }
        AiChip {}
    }
}
