import QtQuick

// The floating button bar: frosted glass over the grid, menus above it, the sliding filter, thumbnail size, and the AI chip.
Item {
    id: bar
    property Item sourceItem
    property real avail: 1000           // width of the area it floats in; labels and groups drop out as it narrows
    property int row: 0                 // the grid's current row (what Review acts on)
    readonly property bool labels: avail >= 1080
    // The thumbnail-size group goes as soon as the bar would not fit (the mockup only dropped it at 640px, and until then the AI button
    // hung out past the glass). `need` is the bar's width with every group present, summed from the parts so it does not depend on
    // what is currently shown.
    readonly property real need: review.implicitWidth + view.implicitWidth + sortBtn.implicitWidth + filters.implicitWidth + sizes.implicitWidth + ai.implicitWidth + 3 + 8 * 4 + 12
    readonly property bool sizeSwitch: avail >= 640 && avail - 24 >= need
    readonly property var filterKeys: ["all", "win", "keep", "rej"]

    // width max-content, max-width 100% - 24px
    width: Math.min(content.implicitWidth + 12, avail - 24)
    height: 48

    Glass { anchors.fill: parent; sourceItem: bar.sourceItem; radius: 20; outline: false }

    Flickable {
        // whatever still does not fit (very narrow windows) is clipped and scrolls sideways instead of hanging out of the glass
        anchors.fill: parent; clip: contentWidth > width + 0.5
        contentWidth: content.implicitWidth + 12; contentHeight: height
        interactive: contentWidth > width; flickableDirection: Flickable.HorizontalFlick; boundsBehavior: Flickable.StopAtBounds
    Row {
        id: content
        x: 6; y: 6; spacing: 4
        FbButton {
            id: review; icon: "check"; label: "Review"; showLabel: bar.labels; caret: true; open: reviewMenu.opened
            onClicked: UiState.setMenu("fReview")
            Popover {
                id: reviewMenu; name: "fReview"
                PopItem { label: "Mark winner"; icon: "star"; kbd: "W"; onClicked: { photos.setDecision(bar.row, "win"); reviewMenu.close() } }
                PopItem { label: "Keep"; icon: "check"; kbd: "K"; onClicked: { photos.setDecision(bar.row, "keep"); reviewMenu.close() } }
                PopItem { label: "Reject"; icon: "x"; kbd: "X"; onClicked: { photos.setDecision(bar.row, "rej"); reviewMenu.close() } }
                PopDivider {}
                PopItem { label: "Clear decision"; icon: "minus"; kbd: "U"; onClicked: { photos.setDecision(bar.row, ""); reviewMenu.close() } }
            }
        }
        FbButton {
            id: view; icon: "view"; label: "View"; showLabel: bar.labels; caret: true; open: viewMenu.opened
            onClicked: UiState.setMenu("fView")
            Popover {
                id: viewMenu; name: "fView"
                PopItem { label: "Filenames"; mode: "switch"; checked: UiState.showNames; onClicked: UiState.showNames = !UiState.showNames }
                PopItem { label: "Decision badges"; mode: "switch"; checked: UiState.showBadges; onClicked: UiState.showBadges = !UiState.showBadges }
            }
        }
        FbButton {
            id: sortBtn; icon: "sort"; label: "Sort"; showLabel: bar.labels; caret: true; open: sortMenu.opened
            onClicked: UiState.setMenu("fSort")
            Popover {
                id: sortMenu; name: "fSort"
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
        Rectangle { width: 1; height: 22; y: 7; color: theme.line; anchors.verticalCenter: undefined }
        Seg {
            id: filters
            labels: ["All", "Winners", "Keep", "Rejected"]
            extras: [photos.total, photos.winCount, photos.keepCount, photos.rejCount]
            showExtras: bar.avail >= 860
            current: Math.max(0, bar.filterKeys.indexOf(UiState.filter))
            onActivated: (i) => { UiState.filter = bar.filterKeys[i]; photos.setFilter(bar.filterKeys[i] === "all" ? "all" : bar.filterKeys[i]) }
        }
        Rectangle { visible: bar.sizeSwitch; width: 1; height: 22; y: 7; color: theme.line }
        Seg {
            id: sizes
            visible: bar.sizeSwitch
            icons: ["densS", "densM", "densL"]; padH: 9
            current: [9, 4, 1].indexOf(UiState.cols)      // small, medium, large: 9, 4 and 1 columns
            onActivated: (i) => { UiState.cols = [9, 4, 1][i] }
        }
        Rectangle { width: 1; height: 22; y: 7; color: theme.line }
        AiChip { id: ai }
    }
    }
}
