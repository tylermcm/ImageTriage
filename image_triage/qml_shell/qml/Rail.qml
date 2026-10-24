import QtQuick
import QtQuick.Shapes
import QtQuick.Effects

// The left rail: four destinations with a sliding pill, then a hairline split, the pinned toolbar buttons, "+" to customise, settings.
Rectangle {
    id: rail
    property int row: 0                  // the grid's current row (what Accept / Reject act on)
    width: UiState.compact ? 52 : 72
    readonly property real btnW: width - 10
    readonly property real btnH: UiState.compact ? 48 : 56
    readonly property real pitch: btnH + 8
    readonly property real pinSize: UiState.compact ? 36 : 40
    color: theme.s1
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }

    readonly property var dests: [
        { k: "lib", t: "Library", i: "library" }, { k: "faces", t: "Faces", i: "faces" },
        { k: "coll", t: "Collections", i: "collections" }, { k: "drop", t: "PocketDrop", i: "pocketdrop" }
    ]
    readonly property int tabIndex: Math.max(0, UiState.tabs.indexOf(UiState.tab))
    // true when a point in window coordinates is over the PocketDrop destination (a dragged collection waits there for its page to open)
    function overDrop(gx, gy) {
        var p = rail.mapFromItem(null, gx, gy)
        return p.x >= 4.5 && p.x <= 4.5 + btnW && p.y >= 10 + 3 * pitch && p.y <= 10 + 3 * pitch + btnH
    }

    AccentPill {
        id: pill
        x: 5; width: rail.btnW; height: rail.btnH; radius: 14
        y: 10 + rail.tabIndex * rail.pitch
        opacity: UiState.tab === "" ? 0 : 1
        Behavior on y { SpringAnimation { spring: 3.4; damping: 0.22; epsilon: 0.25 } }
        Behavior on opacity { NumberAnimation { duration: 250 } }
    }
    Repeater {
        model: rail.dests
        delegate: Item {
            required property int index
            required property var modelData
            x: 4.5; y: 10 + index * rail.pitch; width: rail.btnW; height: rail.btnH
            readonly property bool on: UiState.tab === modelData.k
            Rectangle {   // a collection is being dragged over PocketDrop: accent ring that pulses
                visible: modelData.k === "drop" && UiState.pdRailHot; anchors.fill: parent; radius: 14; color: Qt.alpha(theme.accent, 0.18)
                border.width: 2; border.color: theme.accent
                SequentialAnimation on opacity { running: parent.visible; loops: Animation.Infinite; NumberAnimation { to: 0.55; duration: 550 } NumberAnimation { to: 1; duration: 550 } }
            }
            Column {
                anchors.centerIn: parent; spacing: 4
                Icon { anchors.horizontalCenter: parent.horizontalCenter; name: modelData.i; size: 22; color: on ? theme.accentFg : theme.fg }
                // the mockup's `.app button{font:inherit;color:inherit}` outranks `.rb`, so the labels render at 13px regular in the full text colour
                Text { visible: !UiState.compact; anchors.horizontalCenter: parent.horizontalCenter; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData.t; font.pixelSize: 13; color: on ? theme.accentFg : theme.fg }
            }
            HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: UiState.tab = modelData.k }
            Accessible.role: Accessible.Button
            Accessible.name: modelData.t
        }
    }

    Column {
        id: lower
        y: 10 + 4 * rail.pitch
        anchors.horizontalCenter: parent.horizontalCenter; anchors.horizontalCenterOffset: -0.5
        spacing: 4

        Item { width: 40; height: 9; Rectangle { anchors.centerIn: parent; width: 28; height: 1; radius: 1; color: Qt.alpha(theme.lift, 0.20) } }

        RailButton { visible: UiState.pins.open; icon: "openFolder"; tip: "Open folder" }
        RailButton { visible: UiState.pins.undo; icon: "undo"; tip: "Undo" }
        RailButton {
            visible: UiState.pins.review; icon: "check"; tip: "Review"; menu: true; on: reviewMenu.opened
            onClicked: UiState.setMenu("review")
            Popover {
                id: reviewMenu; name: "review"; placement: 1
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
            onClicked: UiState.setMenu("view")
            Popover {
                id: viewMenu; name: "view"; placement: 1
                PopHeader { caption: "On each thumbnail" }
                PopItem { label: "Filenames"; mode: "switch"; checked: UiState.showNames; onClicked: UiState.showNames = !UiState.showNames }
                PopItem { label: "Decision badges"; mode: "switch"; checked: UiState.showBadges; onClicked: UiState.showBadges = !UiState.showBadges }
            }
        }
        RailButton {
            visible: UiState.pins.filters; icon: "funnel"; tip: "Filters"; menu: true; on: filtersMenu.opened
            onClicked: UiState.setMenu("filters")
            Popover {
                id: filtersMenu; name: "filters"; placement: 1
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
                    label: "Advanced filters\u2026"; icon: "sliders"
                    onClicked: { filtersMenu.close(); UiState.filtersOpen = true }
                }
                PopItem {
                    label: "Clear all filters"; icon: "x"
                    onClicked: { UiState.filter = "all"; UiState.minRate = 0; UiState.fmt = "all"; photos.clearFilters(); advFilters.clearApplied(); filtersMenu.close() }
                }
            }
        }
        RailButton { visible: UiState.pins.accept; icon: "accept"; tip: "Accept selection"; hoverColor: theme.win; onClicked: photos.setDecision(rail.row, "keep") }
        RailButton { visible: UiState.pins.reject; icon: "reject"; tip: "Reject selection"; hoverColor: theme.rej; onClicked: photos.setDecision(rail.row, "rej") }
        RailButton {
            visible: UiState.pins.sort; icon: "sort"; tip: "Sort"; menu: true; on: sortMenu.opened
            onClicked: UiState.setMenu("sort")
            Popover {
                id: sortMenu; name: "sort"; placement: 1
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
            onClicked: UiState.setMenu("columns")
            Popover {
                id: columnsMenu; name: "columns"; placement: 1
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
        RailButton { visible: UiState.pins.rename; icon: "tag"; tip: "Rename selection"; onClicked: UiState.batchMode = "rename" }
        RailButton { visible: UiState.pins.convert; icon: "convert"; tip: "Resize and convert"; onClicked: UiState.batchMode = "resize" }
        RailButton {   // opens PocketDrop with the current photo added
            visible: UiState.pins.share; icon: "pocketdrop"; tip: "Share to phone"
            onClicked: { pocketDrop.refreshLibrary(rail.row); pocketDrop.addLibrary("sel"); UiState.tab = "drop" }
        }
        RailButton { visible: UiState.pins.editor; icon: "editor"; tip: "Open in editor" }
        RailButton { visible: UiState.pins.newfolder; icon: "newfolder"; tip: "New folder" }
        RailButton { visible: UiState.pins.palette; icon: "palette"; tip: "Command palette (Ctrl K)"; onClicked: UiState.openPalette("main") }
        RailButton { visible: UiState.pins.shortcuts; icon: "keyboard"; tip: "Keyboard shortcuts" }

        Item { width: 1; height: 4 }   // the customise button sits 8px below the last pin, the pins 4px apart
        RailButton {
            id: add
            icon: "plus"; tip: "Customize pinned buttons"; on: pinMenu.opened
            onClicked: UiState.setMenu("pins")
            Popover {
                id: pinMenu; name: "pins"; placement: 1; alignBottom: true; minWidth: 250; maxHeight: Math.max(240, rail.Window.height - 140)
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
        anchors.horizontalCenter: parent.horizontalCenter; anchors.horizontalCenterOffset: -0.5
        y: Math.max(lower.y + lower.height + 8, rail.height - 10 - height)    // margin-top: auto: bottom of the rail, or right after the content when that overflows
        icon: "settings"; tip: "Settings"; on: UiState.settingsOpen; onClicked: UiState.settingsOpen = true
    }
}
