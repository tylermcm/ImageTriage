import QtQuick
import QtQuick.Window

// The menus under the top bar's File, Review, View and AI buttons (the Help menu is HelpMenu.qml). They list what the Widgets menu bar has,
// in the shell's words. A row either runs something the shell can do, through `run(id)` with the command ids the palette uses, so both do the
// same thing, or it is dimmed and says it is not connected yet (docs/qml_unwired.md). `UiState.topMenu` is the open menu and `topSub` the row
// whose fly-out is showing.
Item {
    id: root
    objectName: "topMenus"
    signal run(string id)
    readonly property string which: UiState.topMenu
    readonly property var rows: { void libraryNav.recentFolders; return which === "" ? [] : build(which) }
    readonly property var subRows: UiState.topSub >= 0 && UiState.topSub < rows.length && rows[UiState.topSub].items ? rows[UiState.topSub].items : []
    property real subY: 0
    anchors.fill: parent
    visible: which !== "" || card.opacity > 0.01
    z: 100

    // ---- the rows: mr a row that runs `id`, mo one that is not connected, mt one that is ticked while `on()` is true, mh a heading, ms a separator, mf a fly-out ----
    function mr(label, id, kbd, icon) { return { kind: "row", label: label, id: id, kbd: kbd || "", icon: icon || "" } }
    function mo(label, kbd, icon) { return { kind: "row", label: label, id: "", kbd: kbd || "", icon: icon || "", off: true } }       // not connected yet
    function mt(label, id, on, kbd) { return { kind: "row", label: label, id: id, kbd: kbd || "", icon: "", on: on } }                   // ticked while `on()`
    function mh(caption) { return { kind: "head", label: caption } }
    function ms() { return { kind: "sep" } }
    function mf(label, icon, items) { return { kind: "sub", label: label, icon: icon, items: items } }

    // the ten appearance modes of the settings window (settings_model.APPEARANCE_MODES)
    readonly property var modes: [["Slate", "slate"], ["Indigo", "indigo"], ["Dark", "dark"], ["Midnight", "midnight"], ["Graphite", "graphite"], ["Forest", "forest"],
                                  ["High Contrast", "high_contrast"], ["Warm Neutral", "warm_neutral"], ["Light", "light"], ["Auto", "auto"]]

    function build(name) {
        if (name === "file") {
            var out = [mr("Open Folder…", "file.open_folder", "Ctrl O", "openFolder")]
            var recent = libraryNav.recentFolders
            if (recent.length > 0) {
                out.push(mh("Recent folders"))
                for (var i = 0; i < recent.length; ++i) out.push(mr(recent[i].name, "recent:" + recent[i].path, "", "library"))
            }
            return out.concat([
                ms(), mr("Refresh Folder", "file.refresh_folder", "F5", "convert"), mo("New Folder…", "Ctrl Shift N", "newfolder"),
                mr("Show in File Explorer", "file.reveal_folder", "", "library"),
                ms(), mr("Browse Library…", "file.browse_library", "", "collections"),
                mr("Batch Rename…", "tools.batch_rename", "Ctrl Shift R", "tag"), mr("Batch Resize…", "tools.batch_resize", "Ctrl Shift E", "convert"),
                mr("Batch Convert…", "tools.batch_convert", "Ctrl Shift C", "convert"),
                ms(), mr("Deliver / Handoff Builder…", "workflow.handoff_builder", "Ctrl Alt H", "pocketdrop"), mr("Share to Phone", "workflow.share_to_phone", "", "pocketdrop"),
                ms(), mr("Settings…", "file.workflow_settings", "Ctrl ,", "settings"),
                ms(), mr("Exit", "file.exit", "", "x")])
        }
        if (name === "review") {
            return [
                mr("Open Preview", "review.open_preview", "Space", "eye"),
                ms(), mr("Mark Winner", "review.accept_selection", "W", "star"), mr("Keep", "review.keep_selection", "K", "check"),
                mr("Reject", "review.reject_selection", "X", "x"), mr("Clear Decision", "review.clear_decision", "U", "minus"),
                mf("Rating", "star", [mr("1 star", "review.rate.1", "1"), mr("2 stars", "review.rate.2", "2"), mr("3 stars", "review.rate.3", "3"),
                                     mr("4 stars", "review.rate.4", "4"), mr("5 stars", "review.rate.5", "5"), ms(), mr("No rating", "review.rate.0", "0")]),
                ms(), mr("Rename Selection…", "edit.rename_selection", "", "tag"), mo("Move…", "M", "newfolder"), mo("Delete", "Del", "x"),
                ms(), mr("Add to Collection…", "library.add_selected_to_collection", "B", "collections"), mr("New Collection…", "library.create_collection", "", "plus"),
                ms(), mr("Show in File Explorer", "review.reveal", "", "library"), mo("Open in Photoshop", "", "editor"), mo("Open in Editor", "E", "editor")]
        }
        if (name === "view") {
            var themes = [], sizes = [["Small", "s"], ["Medium", "m"], ["Large", "l"]], sorts = [["Name", "name"], ["AI score", "ai"], ["Rating", "rating"], ["Decision", "decision"]]
            var shows = [["All photos", "all"], ["Winners", "win"], ["Keep", "keep"], ["Rejects", "rej"]], fmts = [["RAW and JPG", "all"], ["RAW only", "raw"], ["JPG only", "jpg"]]
            return [
                mf("Appearance", "sun", modes.map(function (m) { return mt(m[0], "appearance." + m[1], function () { return settings.themeMode === m[1] }) })),
                mf("Thumbnail Size", "view", sizes.map(function (m) { return mt(m[0], "view.dens." + m[1], function () { return UiState.cols === 0 && UiState.dens === m[1] }) })),
                mf("Columns", "view", [1, 2, 3, 4, 5, 6, 7, 8, 9].map(function (n) { return mt(n + (n === 1 ? " column" : " columns"), "view.cols." + n, function () { return UiState.cols === n }) })),
                mf("Sort By", "sort", sorts.map(function (m) { return mt(m[0], "sort." + m[1], function () { return UiState.sort === m[1] }) })),
                mf("Show Photos", "funnel", shows.map(function (m) { return mt(m[0], "view.filter." + m[1], function () { return UiState.filter === m[1] }) })
                    .concat([ms()], fmts.map(function (m) { return mt(m[0], "view.fmt." + m[1], function () { return UiState.fmt === m[1] }) }),
                            [ms(), mr("Advanced Filters…", "search.advanced_filters", "", "sliders"), mr("Clear All Filters", "search.clear_filters", "", "x")])),
                ms(), mt("Filenames", "view.names", function () { return UiState.showNames }), mt("Decision Badges", "view.badges", function () { return UiState.showBadges }),
                mt("Inspector", "view.inspector", function () { return UiState.insp }, "I"), mt("Side Panel", "view.panel", function () { return UiState.tab !== "" }),
                mt("Show Hidden Folders", "view.hidden", function () { return settings.value("hidden") === true }),
                ms(), mr("Reset Layout", "window.reset_layout", "", "undo")]
        }
        if (name === "ai") {
            return [
                mr("Analyze Folder…", "ai.analyze", "", "spark"), mr("AI Center…", "ai.center", "", "bars"),
                ms(), mr("People…", "ai.people", "", "people"), mr("Best-of-Set Auto Assembly…", "workflow.best_of", "Ctrl Alt B", "star"),
                ms(), mo("Re-rank AI Culling", "", "sort"), mo("Sort into Semantic Folders", "", "library"), mo("Next AI Pick", "", "spark")]
        }
        return []
    }

    function close() { UiState.topMenu = ""; UiState.topSub = -1 }
    function pick(item) {
        close()
        if (item.off) toasts.show("warn", item.label.replace(/…$/, "") + " isn’t connected yet", "Nothing was changed.")
        else root.run(item.id)
    }

    MouseArea { x: 0; y: 42; width: parent.width; height: parent.height - 42; acceptedButtons: Qt.AllButtons; onClicked: root.close() }   // a click anywhere below the bar puts the menu away
    Shortcut { sequence: "Esc"; enabled: root.which !== ""; onActivated: root.close() }

    // one row of a menu, in the Help menu's look
    component MenuRow: Item {
        id: row
        property var item
        property int at: 0
        property bool flyout: false
        signal picked(var item)                // a row was chosen
        signal subShown(real y)                // a row with a fly-out wants it shown beside it, at this height of the window
        readonly property string kind: item ? item.kind : "sep"
        readonly property bool ticked: item && item.on !== undefined ? item.on() : false
        readonly property bool live: kind !== "sep" && kind !== "head"
        width: parent ? parent.width : 240
        height: kind === "sep" ? 13 : (kind === "head" ? 30 : 34)
        objectName: kind === "row" ? (item.id ? "menuRow-" + item.id : "menuOff-" + item.label) : (kind === "sub" ? "menuSub-" + item.label : "")
        Rectangle { visible: row.kind === "sep"; x: 4; y: 6; width: parent.width - 8; height: 1; color: theme.lineSoft }
        Text { visible: row.kind === "head"; x: 12; y: 12; text: row.item ? (row.item.label || "").toUpperCase() : ""; color: theme.fg3; font.pointSize: UiState.pt(10); font.weight: Font.DemiBold; font.letterSpacing: 0.8 }
        Rectangle { visible: row.live; anchors.fill: parent; radius: 9; color: Qt.alpha(theme.lift, hover.hovered || (row.kind === "sub" && UiState.topSub === row.at && !row.flyout) ? 0.08 : 0) }
        Icon { visible: row.live && (row.ticked || (row.item.icon || "") !== ""); x: 10; anchors.verticalCenter: parent.verticalCenter; size: 16; strokeWidth: 1.7
               name: row.ticked ? "check" : (row.item && row.item.icon ? row.item.icon : ""); color: row.ticked ? theme.accentFg : (hover.hovered ? theme.fg : theme.fg2); opacity: row.item && row.item.off ? 0.5 : 1 }
        Text { visible: row.live; x: 36; anchors.verticalCenter: parent.verticalCenter; text: row.item ? (row.item.label || "") : ""; font.pixelSize: 13
               color: row.item && row.item.off ? theme.fg3 : (hover.hovered ? theme.fg : theme.fg2) }
        Text { visible: row.live && row.kind === "row" && (row.item.kbd || "") !== ""; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
               text: row.item ? (row.item.kbd || "") : ""; color: theme.fg3; font.pointSize: UiState.pt(10); font.weight: Font.Medium; leftPadding: 6; rightPadding: 6; topPadding: 1.5; bottomPadding: 1.5
               Rectangle { anchors.fill: parent; z: -1; radius: 5; color: "transparent"; border.color: theme.line }
               Rectangle { x: 1; y: parent.height - 2; width: parent.width - 2; height: 1.5; radius: 1; color: theme.line } }
        Icon { visible: row.kind === "sub"; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; name: "chevR"; size: 16; color: theme.fg3 }
        HoverHandler {
            id: hover; enabled: row.live; cursorShape: row.item && row.item.off ? Qt.ArrowCursor : Qt.PointingHandCursor
            onHoveredChanged: {
                if (!hovered || row.flyout) return
                UiState.topSub = row.kind === "sub" ? row.at : -1                       // a row with a fly-out shows it; any other row puts it away
                if (row.kind === "sub") row.subShown(row.mapToItem(null, 0, 0).y - 6)
            }
        }
        TapHandler {
            enabled: row.live
            onTapped: { if (row.kind === "sub") { UiState.topSub = row.at; row.subShown(row.mapToItem(null, 0, 0).y - 6) } else row.picked(row.item) }
        }
        Accessible.role: row.kind === "sep" ? Accessible.Separator : Accessible.MenuItem
        Accessible.name: row.item ? (row.item.label || "") : ""
    }

    component MenuCard: Item {
        id: shell
        default property alias content: col.data
        property alias contentHeight: col.height
        Glass { anchors.fill: parent; radius: 14; frosted: false; tint: theme.s2; shadowAlphaA: 0.7; shadowAlphaB: 0; shadowOffsetA: 24; shadowInset: 16 }
        Rectangle { anchors.fill: parent; radius: 14; color: "transparent"; border.color: Qt.alpha(theme.lift, 0.11) }
        Flickable {
            anchors.fill: parent; anchors.margins: 6
            contentHeight: col.height; clip: true; interactive: contentHeight > height; boundsBehavior: Flickable.StopAtBounds
            Column { id: col; width: parent.width }
        }
    }

    MenuCard {
        id: card
        objectName: "topMenuCard"
        x: Math.max(8, Math.min(root.width - width - 8, UiState.topMenuX)); y: 41; width: 292
        height: Math.min(contentHeight + 12, root.height - 60)
        opacity: root.which !== "" ? 1 : 0; scale: root.which !== "" ? 1 : 0.96; transformOrigin: Item.TopLeft
        Behavior on scale { NumberAnimation { duration: 280; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }
        Behavior on opacity { NumberAnimation { duration: 160 } }
        Repeater { model: root.rows; delegate: MenuRow { required property var modelData; required property int index; item: modelData; at: index
                                                    onPicked: (it) => root.pick(it); onSubShown: (y) => root.subY = y } }
    }

    MenuCard {
        id: flyout
        objectName: "topMenuFlyout"
        visible: root.subRows.length > 0 && root.which !== ""
        x: card.x + card.width + 4; y: Math.max(8, Math.min(root.height - height - 8, root.subY)); width: 236
        height: Math.min(contentHeight + 12, root.height - 60)
        Repeater { model: root.subRows; delegate: MenuRow { required property var modelData; required property int index; item: modelData; at: index; flyout: true
                                                    onPicked: (it) => root.pick(it) } }
    }
}
