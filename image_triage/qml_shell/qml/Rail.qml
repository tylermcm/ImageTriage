import QtQuick
import QtQuick.Shapes

// The left rail: four destinations with a sliding pill, then a hairline split, the pinned toolbar buttons, "+" to customise, settings.
Rectangle {
    id: rail
    property int row: 0                  // the grid's current row (what Accept / Reject act on)
    width: 72
    color: theme.s1
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }

    readonly property var dests: [
        { k: "lib", t: "Library", i: "library" }, { k: "faces", t: "Faces", i: "faces" },
        { k: "coll", t: "Collections", i: "collections" }, { k: "drop", t: "PocketDrop", i: "pocketdrop" }
    ]
    readonly property int tabIndex: Math.max(0, UiState.tabs.indexOf(UiState.tab))

    Rectangle {
        id: pill
        x: 5; width: 62; height: 56; radius: 14
        y: 10 + rail.tabIndex * 64
        opacity: UiState.tab === "" ? 0 : 1
        color: Qt.alpha(theme.accent, 0.15); border.color: Qt.alpha(theme.accent, 0.30)
        Behavior on y { SpringAnimation { spring: 3.4; damping: 0.22; epsilon: 0.25 } }
        Behavior on opacity { NumberAnimation { duration: 250 } }
    }
    Repeater {
        model: rail.dests
        delegate: Item {
            required property int index
            required property var modelData
            x: 4.5; y: 10 + index * 64; width: 62; height: 56
            readonly property bool on: UiState.tab === modelData.k
            Column {
                anchors.centerIn: parent; spacing: 4
                Icon { anchors.horizontalCenter: parent.horizontalCenter; name: modelData.i; size: 22; color: on ? theme.accentFg : theme.fg }
                // the mockup's `.app button{font:inherit;color:inherit}` outranks `.rb`, so the labels render at 13px regular in the full text colour
                Text { anchors.horizontalCenter: parent.horizontalCenter; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData.t; font.pixelSize: 13; color: on ? theme.accentFg : theme.fg }
            }
            HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: UiState.tab = modelData.k }
            Accessible.role: Accessible.Button
            Accessible.name: modelData.t
        }
    }

    Column {
        id: lower
        y: 10 + 4 * 64 - 8 + 8
        anchors.horizontalCenter: parent.horizontalCenter; anchors.horizontalCenterOffset: -0.5
        spacing: 4

        Item { width: 40; height: 9; Rectangle { anchors.centerIn: parent; width: 28; height: 1; radius: 1; color: Qt.alpha(theme.lift, 0.20) } }

        RailButton { visible: UiState.pins.open; icon: "openFolder"; tip: "Open folder" }
        RailButton { visible: UiState.pins.undo; icon: "undo"; tip: "Undo" }
        RailButton {
            visible: UiState.pins.review; icon: "check"; tip: "Review"; menu: true; on: reviewMenu.opened
            onClicked: reviewMenu.opened ? reviewMenu.close() : reviewMenu.open()
            Popover {
                id: reviewMenu; placement: 1
                PopHeader { caption: "Decision for selection" }
                PopItem { label: "Mark winner"; icon: "star"; kbd: "W"; onClicked: { photos.setDecision(rail.row, "win"); reviewMenu.close() } }
                PopItem { label: "Keep"; icon: "check"; kbd: "K"; onClicked: { photos.setDecision(rail.row, "keep"); reviewMenu.close() } }
                PopItem { label: "Reject"; icon: "x"; kbd: "X"; onClicked: { photos.setDecision(rail.row, "rej"); reviewMenu.close() } }
                PopDivider {}
                PopItem { label: "Clear decision"; icon: "minus"; kbd: "U"; onClicked: { photos.setDecision(rail.row, ""); reviewMenu.close() } }
            }
        }
        RailButton {
            visible: UiState.pins.view; icon: "view"; tip: "View"; menu: true; on: viewMenu.opened
            onClicked: viewMenu.opened ? viewMenu.close() : viewMenu.open()
            Popover {
                id: viewMenu; placement: 1
                PopHeader { caption: "On each thumbnail" }
                PopItem { label: "Filenames"; mode: "switch"; checked: UiState.showNames; onClicked: UiState.showNames = !UiState.showNames }
                PopItem { label: "Decision badges"; mode: "switch"; checked: UiState.showBadges; onClicked: UiState.showBadges = !UiState.showBadges }
            }
        }
        RailButton {
            visible: UiState.pins.filters; icon: "funnel"; tip: "Filters"; menu: true; on: filtersMenu.opened
            onClicked: filtersMenu.opened ? filtersMenu.close() : filtersMenu.open()
            Popover {
                id: filtersMenu; placement: 1
                PopHeader { caption: "Minimum rating" }
                Repeater {
                    model: [[0, "Any"], [3, "3 stars and up"], [4, "4 stars and up"], [5, "5 stars"]]
                    delegate: PopItem {
                        required property var modelData
                        label: modelData[1]; mode: "tick"; checked: UiState.minRate === modelData[0]
                        onClicked: { UiState.minRate = modelData[0]; photos.setMinRating(modelData[0]) }
                    }
                }
                PopDivider {}
                PopHeader { caption: "Format" }
                Repeater {
                    model: [["all", "RAW and JPG"], ["raw", "RAW only"], ["jpg", "JPG only"]]
                    delegate: PopItem {
                        required property var modelData
                        label: modelData[1]; mode: "tick"; checked: UiState.fmt === modelData[0]
                        onClicked: { UiState.fmt = modelData[0]; photos.setFormat(modelData[0]) }
                    }
                }
                PopDivider {}
                PopItem {
                    label: "Clear all filters"; icon: "x"
                    onClicked: { UiState.filter = "all"; UiState.minRate = 0; UiState.fmt = "all"; photos.clearFilters(); filtersMenu.close() }
                }
            }
        }
        RailButton { visible: UiState.pins.accept; icon: "accept"; tip: "Accept selection"; hoverColor: theme.win; onClicked: photos.setDecision(rail.row, "keep") }
        RailButton { visible: UiState.pins.reject; icon: "reject"; tip: "Reject selection"; hoverColor: theme.rej; onClicked: photos.setDecision(rail.row, "rej") }
        RailButton {
            visible: UiState.pins.sort; icon: "sort"; tip: "Sort"; menu: true; on: sortMenu.opened
            onClicked: sortMenu.opened ? sortMenu.close() : sortMenu.open()
            Popover {
                id: sortMenu; placement: 1
                PopHeader { caption: "Sort photos by" }
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
        RailButton {
            visible: UiState.pins.columns; icon: "columns"; tip: "Columns"; menu: true; on: columnsMenu.opened
            onClicked: columnsMenu.opened ? columnsMenu.close() : columnsMenu.open()
            Popover {
                id: columnsMenu; placement: 1
                PopHeader { caption: "Thumbnail size" }
                Repeater {
                    model: [["s", "Small"], ["m", "Medium"], ["l", "Large"]]
                    delegate: PopItem {
                        required property var modelData
                        label: modelData[1]; mode: "tick"; checked: UiState.dens === modelData[0]
                        onClicked: UiState.dens = modelData[0]
                    }
                }
            }
        }
        RailButton { visible: UiState.pins.compare; icon: "compare"; tip: "Compare photos"; on: UiState.compare; onClicked: UiState.compare = !UiState.compare }
        RailButton { visible: UiState.pins.preview; icon: "eye"; tip: "Open preview" }
        RailButton { visible: UiState.pins.ladder; icon: "ladder"; tip: "Winner ladder" }
        RailButton { visible: UiState.pins.rename; icon: "tag"; tip: "Rename selection" }
        RailButton { visible: UiState.pins.convert; icon: "convert"; tip: "Resize and convert" }
        RailButton { visible: UiState.pins.share; icon: "pocketdrop"; tip: "Share to phone" }
        RailButton { visible: UiState.pins.editor; icon: "editor"; tip: "Open in editor" }
        RailButton { visible: UiState.pins.newfolder; icon: "newfolder"; tip: "New folder" }
        RailButton { visible: UiState.pins.palette; icon: "palette"; tip: "Command palette (Ctrl K)" }
        RailButton { visible: UiState.pins.shortcuts; icon: "keyboard"; tip: "Keyboard shortcuts" }

        Item { width: 1; height: 4 }   // the customise button sits 8px below the last pin, the pins 4px apart
        RailButton {
            id: add
            icon: "plus"; tip: "Customize pinned buttons"; on: pinMenu.opened
            onClicked: pinMenu.opened ? pinMenu.close() : pinMenu.open()
            Popover {
                id: pinMenu; placement: 1; alignBottom: true; minWidth: 250; maxHeight: Math.max(240, rail.Window.height - 140)
                PopHeader { caption: "Pinned buttons" }
                Text {
                    width: parent.width - 20; x: 10; wrapMode: Text.WordWrap; bottomPadding: 6
                    text: "The toolbar's actions, kept under the destinations. Press a switch to pin or unpin."
                    color: theme.fg3; font.pixelSize: 12
                }
                PopHeader { caption: "Menus" }
                Repeater { model: [["review", "Review"], ["view", "View"], ["filters", "Filters"], ["sort", "Sort"], ["columns", "Columns"]]; delegate: pinRow }
                PopHeader { caption: "Selection" }
                Repeater { model: [["accept", "Accept selection"], ["reject", "Reject selection"], ["rename", "Rename selection"]]; delegate: pinRow }
                PopHeader { caption: "Files" }
                Repeater { model: [["open", "Open folder"], ["newfolder", "New folder"], ["undo", "Undo"], ["convert", "Resize and convert"], ["share", "Share to phone"]]; delegate: pinRow }
                PopHeader { caption: "Tools" }
                Repeater { model: [["compare", "Compare photos"], ["preview", "Open preview"], ["ladder", "Winner ladder"], ["editor", "Open in editor"], ["palette", "Command palette"], ["shortcuts", "Keyboard shortcuts"]]; delegate: pinRow }
            }
        }
    }

    Component {
        id: pinRow
        PopItem {
            required property var modelData
            label: modelData[1]; mode: "switch"; checked: UiState.pins[modelData[0]]
            onClicked: UiState.togglePin(modelData[0])
        }
    }

    RailButton {
        anchors.horizontalCenter: parent.horizontalCenter; anchors.horizontalCenterOffset: -0.5; anchors.bottom: parent.bottom; anchors.bottomMargin: 10
        icon: "settings"; tip: "Settings"
    }
}
