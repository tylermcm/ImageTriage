import QtQuick
import QtQuick.Shapes
import QtQuick.Effects

// The left rail: four destinations with a sliding pill, then a hairline split, the pinned toolbar buttons, "+" to customise, settings.
Rectangle {
    id: rail
    property int row: 0                  // the grid's current row (what Accept / Reject act on)
    // Icon-only destinations, as the Widgets rail (docs/nav_rail_label_clipping.md, option 4): "Collections" and "PocketDrop" are wider than any
    // button the rail can afford, and the pinned buttons below have no text either. The names are the hover tooltips. true brings the labels back.
    readonly property bool showLabels: false
    width: UiState.compact ? 52 : (showLabels ? 72 : 64)
    readonly property real btnW: width - 10
    readonly property real btnH: UiState.compact ? 48 : (showLabels ? 56 : 44)
    readonly property real pitch: btnH + 8
    readonly property real spacing: 8                  // between the destinations (DragReorder reads it)
    color: theme.s1

    // The pinned buttons live between the destinations and the settings button, which keeps the bottom of the rail. They are 40 px when they
    // fit, a little smaller (down to 30) when the window is short, and when even that is not enough the mouse wheel scrolls them.
    readonly property real gearSize: UiState.compact ? 36 : 40
    readonly property real pinsTop: 10 + 4 * pitch
    readonly property real pinsAvail: Math.max(0, height - pinsTop - gearSize - 18)
    readonly property int pinCount: { var n = 0; for (var k in UiState.pins) { if (UiState.pins[k]) ++n } return n }
    readonly property real pinFit: Math.max(30, Math.min(UiState.compact ? 36 : 40, (pinsAvail - 21 - 4 * pinCount) / (pinCount + 1)))
    Binding { target: UiState; property: "railPin"; value: rail.pinFit }
    property real scrollY: 0
    readonly property real maxScroll: Math.max(0, lower.height - pinsAvail)
    onMaxScrollChanged: scrollY = Math.min(scrollY, maxScroll)
    WheelHandler {
        target: null; enabled: rail.maxScroll > 0
        onWheel: (event) => { rail.scrollY = Math.max(0, Math.min(rail.maxScroll, rail.scrollY - event.angleDelta.y / 2)) }
    }
    Item { id: pinsClip; objectName: "pinsClip"; y: rail.pinsTop; width: 360; height: rail.pinsAvail; clip: true }   // wide so the tooltips beside the buttons are not cut, short so nothing reaches the settings button
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }

    readonly property var dests: [
        { k: "lib", t: "Library", i: "library" }, { k: "faces", t: "Faces", i: "faces" },
        { k: "coll", t: "Collections", i: "collections" }, { k: "drop", t: "PocketDrop", i: "pocketdrop" }
    ]
    // where a destination sits (0 to 3): its place in the order the user dragged them into, else the order above
    function slotOf(k) {
        var saved = UiState.destOrder || [], keys = dests.map(function (d) { return d.k })
        keys = keys.map(function (key, i) { var r = saved.indexOf(key); return { key: key, rank: r < 0 ? 100 + i : r } }).sort(function (a, b) { return a.rank - b.rank }).map(function (e) { return e.key })
        return Math.max(0, keys.indexOf(k))
    }
    readonly property int tabIndex: slotOf(UiState.tab)
    // true when a point in window coordinates is over the PocketDrop destination (a dragged collection waits there for its page to open)
    function overDrop(gx, gy) {
        var p = rail.mapFromItem(null, gx, gy)
        return p.x >= 4.5 && p.x <= 4.5 + btnW && p.y >= 10 + slotOf("drop") * pitch && p.y <= 10 + slotOf("drop") * pitch + btnH
    }

    AccentPill {
        id: pill
        x: 4.5; width: rail.btnW; height: rail.btnH; radius: 14
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
            readonly property string dragId: modelData.k          // the four can be dragged into another order (DragReorder)
            DragReorder { button: parent; orderName: "dest" }
            Rectangle {   // a collection is being dragged over PocketDrop: accent ring that pulses
                visible: modelData.k === "drop" && UiState.pdRailHot; anchors.fill: parent; radius: 14; color: Qt.alpha(theme.accent, 0.18)
                border.width: 2; border.color: theme.accent
                SequentialAnimation on opacity { running: parent.visible; loops: Animation.Infinite; NumberAnimation { to: 0.55; duration: 550 } NumberAnimation { to: 1; duration: 550 } }
            }
            Column {
                anchors.centerIn: parent; spacing: 4
                Icon { anchors.horizontalCenter: parent.horizontalCenter; name: modelData.i; size: rail.showLabels || UiState.compact ? 22 : 24; color: on ? theme.accentFg : theme.fg }
                // the mockup's `.app button{font:inherit;color:inherit}` outranks `.rb`, so the labels render at 13px regular in the full text colour
                Text { visible: rail.showLabels && !UiState.compact; anchors.horizontalCenter: parent.horizontalCenter; height: 18.2; verticalAlignment: Text.AlignVCenter; text: modelData.t; font.pixelSize: 13; color: on ? theme.accentFg : theme.fg }
            }
            Tip { id: tip; text: on ? "Hide " + modelData.t : modelData.t; show: false }
            Timer { id: tipDelay; interval: 350; onTriggered: tip.show = hover.hovered }
            HoverHandler {
                id: hover; cursorShape: Qt.PointingHandCursor
                onHoveredChanged: { if (hovered) tipDelay.restart(); else { tipDelay.stop(); tip.show = false } }
            }
            TapHandler { onTapped: UiState.tab = on ? "" : modelData.k }      // the page that is open: hides the panel, leaving the rail; any tab brings it back
            Accessible.role: Accessible.Button
            Accessible.name: modelData.t
        }
    }

    Column {
        id: lower
        parent: pinsClip
        x: (rail.width - width) / 2 - 0.5
        y: -rail.scrollY
        spacing: 4

        Item { width: 40; height: 9; Rectangle { anchors.centerIn: parent; width: 28; height: 1; radius: 1; color: Qt.alpha(theme.lift, 0.20) } }

        RailButton { visible: UiState.pins.open; icon: "openFolder"; tip: "Open folder"; onClicked: UiState.openFolderRequested() }
        RailButton { visible: UiState.pins.undo; icon: "undo"; tip: "Undo"; opacity: photos.canUndo ? 1 : 0.45; onClicked: UiState.undoRequested() }
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
                PopDivider {}
                PopHeader { caption: "Thumbnail size" }
                Repeater {
                    model: [["s", "Small"], ["m", "Medium"], ["l", "Large"]]
                    delegate: PopItem {
                        required property var modelData
                        label: modelData[1]; mode: "tick"; checked: UiState.cols === 0 && UiState.dens === modelData[0]
                        onClicked: UiState.setDens(modelData[0])
                    }
                }
                PopDivider {}
                PopHeader { caption: "Columns" }
                Row {
                    spacing: 3; leftPadding: 4; bottomPadding: 4
                    Repeater {
                        model: 9
                        delegate: Rectangle {
                            id: chip
                            required property int index
                            readonly property bool on: UiState.cols === index + 1
                            width: 25; height: 28; radius: 8
                            color: on ? Qt.alpha(theme.accent, 0.2) : Qt.alpha(theme.lift, chipHover.hovered ? 0.1 : 0)
                            border.color: on ? Qt.alpha(theme.accent, 0.6) : "transparent"
                            Text { anchors.centerIn: parent; text: chip.index + 1; font.pixelSize: 13; font.weight: chip.on ? Font.DemiBold : Font.Normal; color: chip.on ? theme.accentFg : theme.fg2 }
                            HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: UiState.cols = chip.index + 1 }
                            Accessible.role: Accessible.Button; Accessible.name: (chip.index + 1) + " columns"
                        }
                    }
                }
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
                        label: modelData[1]; mode: "tick"; checked: UiState.cols === 0 && UiState.dens === modelData[0]
                        onClicked: UiState.setDens(modelData[0])
                    }
                }
                PopDivider {}
                PopHeader { caption: "Columns" }
                Repeater {
                    model: 9
                    delegate: PopItem {
                        required property int index
                        label: (index + 1) + (index === 0 ? " column" : " columns"); mode: "tick"; checked: UiState.cols === index + 1
                        onClicked: UiState.cols = index + 1
                    }
                }
            }
        }
        RailButton { visible: UiState.pins.compare; icon: "compare"; tip: "Compare photos"; on: UiState.compare; onClicked: UiState.compare = !UiState.compare }
        RailButton { visible: UiState.pins.preview; icon: "eye"; tip: "Open preview"; onClicked: popout.open(rail.row) }
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
            icon: "plus"; dragId: ""; tip: "Customize pinned buttons"; on: pinMenu.opened
            onClicked: UiState.setMenu("pins")
            Popover {
                id: pinMenu; name: "pins"; placement: 1; alignBottom: true; minWidth: 250; maxHeight: Math.max(240, rail.Window.height - 140)
                // it grows upward from the button's bottom edge, so it may be no taller than the room above that edge (the window's top is where it was cut off)
                Connections { target: pinMenu; function onAboutToShow() { pinMenu.maxHeight = Math.max(160, add.mapToItem(null, 0, add.height).y - 12) } }
                PopHeader { caption: "Pinned buttons" }
                Text {
                    width: parent.width - 20; x: 10; wrapMode: Text.WordWrap; bottomPadding: 6
                    text: "The toolbar's actions, kept under the destinations. Press a switch to pin or unpin."
                    color: theme.fg3; font.pixelSize: 12
                }
                Item {   // "Menus" with the lock across from it: locked, the pins cannot be switched and the rail's buttons cannot be dragged
                    width: parent.width; height: 23
                    PopHeader { caption: "Menus" }
                    Item {
                        id: lockBtn
                        anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                        width: 26; height: 22
                        Rectangle { anchors.fill: parent; radius: 7; color: UiState.pinsLocked ? Qt.alpha(theme.accent, 0.16) : Qt.alpha(theme.lift, lockHover.hovered ? 0.1 : 0) }
                        Icon { anchors.centerIn: parent; name: UiState.pinsLocked ? "lock" : "unlock"; size: 15; strokeWidth: 1.7; color: UiState.pinsLocked ? theme.accentFg : theme.fg2 }
                        HoverHandler { id: lockHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: UiState.pinsLocked = !UiState.pinsLocked }
                        Tip { text: UiState.pinsLocked ? "Unlock the rail" : "Lock the rail"; show: lockHover.hovered; leftSide: true }
                        Accessible.role: Accessible.CheckBox; Accessible.checked: UiState.pinsLocked; Accessible.name: "Lock the pinned buttons"
                    }
                }
                Repeater { model: [["review", "Review"], ["view", "View"], ["filters", "Filters"], ["sort", "Sort"], ["columns", "Columns"]]; delegate: pinRow }
                PopHeader { caption: "Selection" }
                Repeater { model: [["accept", "Accept selection"], ["reject", "Reject selection"], ["rename", "Rename selection"]]; delegate: pinRow }
                PopHeader { caption: "Files" }
                Repeater { model: [["open", "Open folder"], ["newfolder", "New folder"], ["undo", "Undo"], ["convert", "Resize and convert"], ["share", "Share to phone"]]; delegate: pinRow }
                PopHeader { caption: "Tools" }
                Repeater { model: [["compare", "Compare photos"], ["preview", "Open preview"], ["editor", "Open in editor"], ["palette", "Command palette"], ["shortcuts", "Keyboard shortcuts"]]; delegate: pinRow }
            }
        }
    }

    Component {
        id: pinRow
        PopItem {
            required property var modelData
            label: modelData[1]; mode: "switch"; checked: UiState.pins[modelData[0]]
            opacity: UiState.pinsLocked ? 0.55 : 1
            onClicked: { if (!UiState.pinsLocked) UiState.togglePin(modelData[0]) }
        }
    }

    RailButton {
        anchors.horizontalCenter: parent.horizontalCenter; anchors.horizontalCenterOffset: -0.5
        y: rail.height - 10 - height                                           // always the bottom of the rail; the pinned buttons above it shrink or scroll
        size: rail.gearSize
        icon: "settings"; dragId: ""; tip: "Settings"; on: UiState.settingsOpen; onClicked: UiState.settingsOpen = true
    }
}
