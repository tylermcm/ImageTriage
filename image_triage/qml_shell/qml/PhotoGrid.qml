import QtQuick
import QtQuick.Controls
import QtQuick.Shapes

// The photo grid: a virtualized GridView over the model, thumbnails from the disk cache (ThumbImage), edge-to-edge with a 2 px gutter.
Item {
    id: root
    property alias view: gv
    // The grid's list is the open folder's child folders first, then its photos (GridModel). Everything outside this file speaks in photo
    // rows, so `row` is the current photo's row (-1 while a folder card is current) and `nf` is how many folder cards come first.
    readonly property int nf: gridModel.folders
    property int row: -1
    onRowChanged: if (gv.currentIndex !== row + nf) gv.currentIndex = row + nf
    Connections { target: gv; function onCurrentIndexChanged() { root.row = gv.currentIndex - root.nf } }
    onNfChanged: row = gv.currentIndex - nf
    // The list is rebuilt (a scope or filter change, a refresh) and the number of folder cards in front can change with it: stay on the same photo,
    // not the same position in the list, or the current photo slips by the difference. (`gridModel.folders` is read directly: `nf` follows a moment later.)
    property int keepRow: -1
    property string keepPath: ""
    Connections {
        target: gridModel
        // Results that arrive on their own (AI scores, search matches, a refresh) reorder the list under the cursor: follow the photo then, or the
        // next W or R lands on a different one. A decision or a delete keeps the position, so the next photo comes under the cursor.
        function onModelAboutToBeReset() { root.stopAutoScroll(); root.keepRow = root.row; root.keepPath = root.row >= 0 && photos.resetFollowsPhoto() ? (photos.get(root.row).path || "") : "" }
        function onModelReset() {
            if (root.renameRow >= 0 && !root.renameBusy) {      // the list changed under the name being edited: stay with that photo, or give up when it is gone
                var editing = photos.rowForPath(root.renamePath)
                if (editing >= 0) root.renameRow = editing; else root.cancelRename()
            }
            if (root.restoringScope || root.keepRow < 0) return
            var same = root.keepPath !== "" ? photos.rowForPath(root.keepPath) : -1
            gv.currentIndex = (same >= 0 ? same : root.keepRow) + gridModel.folders
        }
    }
    property bool restoringScope: false
    property var scopePositions: ({})
    property string bookmarkFolder: ""
    function scopeKey() { return JSON.stringify([photos.scopeKind, photos.scopeId]) }
    function saveScopePosition() {
        if (bookmarkFolder !== photos.folderPath) scopePositions = ({})
        bookmarkFolder = photos.folderPath
        var topIndex = gv.indexAt(1, gv.contentY + 1)
        var top = topIndex >= root.nf ? photos.get(topIndex - root.nf) : ({})
        var current = root.row >= 0 ? photos.get(root.row) : ({})
        var states = Object.assign({}, scopePositions)
        states[scopeKey()] = {
            path: current.path || "", index: gv.currentIndex, y: gv.contentY,
            topPath: top.path || "", cellHeight: gv.cellHeight,
            offset: topIndex >= 0 ? gv.contentY - gv.originY - Math.floor(topIndex / root.cols) * gv.cellHeight : 0
        }
        scopePositions = states
    }
    function restoreScopePosition() {
        if (!restoringScope) return
        gv.forceLayout()
        var saved = scopePositions[scopeKey()]
        var index = 0
        if (saved) {
            var row = saved.path ? photos.rowForPath(saved.path) : -1
            index = row >= 0 ? row + root.nf : saved.index
        }
        gv.currentIndex = gv.count > 0 ? Math.max(0, Math.min(gv.count - 1, index)) : -1
        gv.forceLayout()
        var y = gv.originY
        if (saved) {
            var topRow = saved.topPath ? photos.rowForPath(saved.topPath) : -1
            y = topRow >= 0 ? gv.originY + Math.floor((topRow + root.nf) / root.cols) * gv.cellHeight
                    + saved.offset * gv.cellHeight / saved.cellHeight : saved.y
        }
        var high = Math.max(gv.originY, gv.originY + gv.contentHeight - gv.height + gv.bottomMargin)
        gv.contentY = Math.max(gv.originY, Math.min(high, y))
        Qt.callLater(function () { root.restoringScope = false })
    }
    Connections {
        target: photos
        function onScopeAboutToChange(kind, ident) {
            wheelEase.stop(); gv.cancelFlick(); root.stopAutoScroll()
            root.saveScopePosition()
            root.restoringScope = true
        }
        function onScopeChanged() { if (root.restoringScope) Qt.callLater(root.restoreScopePosition) }
        function onFolderChanged() {
            if (root.bookmarkFolder !== photos.folderPath) {
                root.scopePositions = ({})
                root.bookmarkFolder = photos.folderPath
                root.restoringScope = false
            }
        }
    }
    readonly property int minCell: UiState.dens === "s" ? 150 : UiState.dens === "m" ? 230 : 340
    readonly property int cols: UiState.cols > 0 ? UiState.cols : Math.max(1, Math.floor(gv.width / (minCell + 2)))   // auto-fill: n tracks of min width plus (n-1) gaps
    // One thumbnail size per density, not per cell width: opening a panel or resizing the window changes the cells' width, and a size that
    // followed it asked for a new set of thumbnails (decoded from the originals) for every card on screen each time.
    readonly property int cellBucket: bucket(Math.ceil((UiState.cols > 0 ? Math.min(Screen.width, 2000) * 0.75 / UiState.cols : minCell * 1.5) * Screen.devicePixelRatio))
    property int bottomSpace: 132
    property Item overlay                // something floating over the grid (the bar): taps inside it are not the grid's
    signal openRequested(int row)
    signal contextRequested(string kind, int row, real x, real y)
    property bool autoScrolling: false
    readonly property bool dimRejected: { void settings.revision; return settings.value("dimRejects") === true }
    readonly property var scrollCursor: typeof nativeScrollCursor !== "undefined" ? nativeScrollCursor : null
    onAutoScrollingChanged: {
        if (!scrollCursor) return
        if (autoScrolling) scrollCursor.begin()
        else scrollCursor.end()
    }
    Component.onDestruction: if (scrollCursor) scrollCursor.end()
    property point scrollOrigin: Qt.point(0, 0)
    property real scrollPointerY: 0
    onScrollPointerYChanged: if (autoScrolling && scrollCursor) {
        var delta = scrollPointerY - scrollOrigin.y
        scrollCursor.direction(Math.abs(delta) <= 16 ? 0 : Math.sign(delta))
    }
    function stopAutoScroll() { autoScrolling = false }
    onEnabledChanged: if (!enabled) stopAutoScroll()
    Connections {
        target: root.Window.window
        function onActiveChanged() { if (!root.Window.active) root.stopAutoScroll() }
    }

    // ---- F2 with one photo: its name is edited on its card. The rename itself is rename_one.py, through fileActions.renameRow ----
    property int renameRow: -1                  // the photo whose name is being edited (-1: none)
    property string renamePath: ""
    property bool renameBusy: false             // Enter was pressed and the files are being renamed
    property string renameError: ""             // why the rename was refused (a name that is taken, a file that would not move)
    property var renameVerdict: ({})            // photos.renameCheck of what is typed: {error, warning, same}
    property string followPath: ""
    readonly property bool renaming: renameRow >= 0
    function beginRename(row) {
        if (renameRow >= 0) return true
        if (row === undefined) row = root.row
        var d = photos.get(row)
        if (!d.path) return false
        photos.clearSelection()
        gv.currentIndex = row + root.nf
        gv.revealCurrent()
        renamePath = d.path; renameError = ""; renameBusy = false; renameVerdict = ({})
        renameRow = row
        nameField.text = d.title || d.name
        nameField.selectAll()
        nameField.forceActiveFocus()
        return true
    }
    function cancelRename() { renameRow = -1; renameBusy = false; renameError = "" }
    function endRename() { cancelRename(); gv.forceActiveFocus() }
    function commitRename() {
        if (renameRow < 0 || renameBusy) return
        var verdict = photos.renameCheck(renameRow, nameField.text)
        renameVerdict = verdict
        if (verdict.error !== "") return
        if (verdict.same) { endRename(); return }
        var refused = fileActions.renameRow(renameRow, nameField.text)
        if (refused !== "") { renameError = refused; return }
        renameBusy = true
    }
    Connections {
        target: photos
        function onRenameFinished(from, to, error) {
            if (error === "" && to !== from) root.followPath = to
            if (root.renameRow < 0 || from !== root.renamePath) return
            if (error !== "") { root.renameBusy = false; root.renameError = error; nameField.forceActiveFocus() }
            else root.endRename()
        }
        // The folder has been read again after a rename: the grid goes with the photo to its new place in the order. Last, after the
        // handlers that put the cursor back at the start or on the photo's old path.
        function onLoadingChanged() {
            if (photos.loading || root.followPath === "") return
            Qt.callLater(function () {
                var renamed = root.followPath !== "" ? photos.rowForPath(root.followPath) : -1
                if (renamed >= 0) { libraryNav.savePosition(root.followPath); gv.currentIndex = renamed + root.nf; gv.revealCurrent() }
                root.followPath = ""
            })
        }
    }

    // ---- dragging photos onto a collection in the side panel (its DropArea reads `dragRows`) ----
    // A photo that is part of the pick drags the whole pick; any other photo drags alone and the pick stays as it is.
    property var dragRows: []
    property bool dragging: false
    property bool dragCancelled: false
    property point dragAt: Qt.point(0, 0)           // where the pointer is, in the window
    property string tabBeforeDrag: ""
    property bool dragOpenedTab: false
    readonly property var dragInfo: {
        if (!dragging) return ({ title: "", sub: "", thumbs: ["", "", ""] })
        void collections.revision
        var over = UiState.cldrop !== "" ? (collections.get(UiState.cldrop).name || "") : ""
        var paths = []
        for (var i = 0; i < 3; ++i) paths.push(i < dragRows.length ? (photos.get(dragRows[i]).path || "") : "")
        return { title: dragRows.length === 1 ? (photos.get(dragRows[0]).name || "1 photo") : dragRows.length + " photos",
                 sub: over !== "" ? "Add to " + over : "Drop on a collection", thumbs: paths }
    }
    function beginDrag(press) {
        if (root.blocked(press)) return
        var p = gv.mapFromItem(null, press.x, press.y)
        var index = gv.indexAt(p.x + gv.contentX, p.y + gv.contentY)
        if (index < root.nf) return                                  // a folder card, or the space under the last row
        var row = index - root.nf
        var picked = photos.selectionRows(row)
        dragRows = picked.indexOf(row) >= 0 ? picked : [row]
        dragAt = press
        dragCancelled = false; dragOpenedTab = false; tabBeforeDrag = UiState.tab
        dragging = true
    }
    function endDrag(drop) {
        if (!dragging) return
        if (drop && !dragCancelled) dragProxy.Drag.drop(); else dragProxy.Drag.cancel()
        dragging = false
        UiState.cldrop = ""
        if (dragOpenedTab) tabBack.restart()
    }
    // The Collections page opens when the photos are carried off the grid towards the panel, and the page that was open comes back afterwards.
    onDragAtChanged: if (dragging && !dragOpenedTab && UiState.tab !== "coll" && root.mapFromItem(null, dragAt.x, dragAt.y).x < 0) { dragOpenedTab = true; UiState.tab = "coll" }
    Timer { id: tabBack; interval: 1400; onTriggered: if (UiState.tab === "coll" && !root.dragging) UiState.tab = root.tabBeforeDrag }
    Item {   // what is dragged: the thing the side panel's DropArea sees, and the badge that follows the pointer
        id: dragProxy
        objectName: "gridDragBadge"
        parent: root.Window.contentItem ? root.Window.contentItem : root
        x: root.dragAt.x; y: root.dragAt.y; width: 1; height: 1; z: 90
        visible: root.dragging
        Drag.active: root.dragging
        Drag.source: root
        Drag.keys: ["image-triage/photos"]
        PocketDropGhost {
            x: 14; y: 10; info: root.dragInfo
            Rectangle {   // how many photos are being carried
                objectName: "gridDragCount"
                readonly property int count: root.dragRows.length
                anchors.right: parent.right; anchors.rightMargin: -8; y: -8
                width: Math.max(20, countText.implicitWidth + 12); height: 20; radius: 10; color: theme.accent
                Text { id: countText; anchors.centerIn: parent; text: parent.count; color: theme.onAccent; font.pointSize: UiState.pt(11); font.weight: Font.DemiBold }
            }
        }
    }

    // B: the photo you are on goes into the collection that collects (or, when there is none, you choose one)
    function addToCollection() {
        if (root.row < 0) return
        if (collections.activeId !== "") collections.addSelection(collections.activeId, root.row)
        else { UiState.collPickerQuery = ""; UiState.collPicker = true }
    }

    // Return, Space and a double click: a folder card opens that folder, a photo opens in the viewer
    function openCurrent() {
        if (gv.currentIndex < root.nf) { if (gv.currentItem) libraryNav.openFolder(gv.currentItem.path) }
        else root.openRequested(root.row)
    }

    function blocked(scenePoint) {
        if (!overlay) return false;
        var p = overlay.mapFromItem(null, scenePoint.x, scenePoint.y);
        return p.x >= 0 && p.y >= 0 && p.x <= overlay.width && p.y <= overlay.height;
    }

    function bucket(px) {
        var sizes = [160, 240, 360, 540, 800];
        for (var i = 0; i < sizes.length; ++i) if (px <= sizes[i]) return sizes[i];
        return 800;
    }

    GridView {
        id: gv
        objectName: "photoGrid"
        anchors.fill: parent
        anchors.margins: 1
        clip: true
        focus: true
        activeFocusOnTab: true                // Tab leaves the grid for the controls around it and comes back to it
        keyNavigationEnabled: true
        boundsBehavior: Flickable.StopAtBounds
        // Scrolling belongs to the wheel handler and middle-button autoscroll.
        // Flickable's default left-button drag competes with photo selection.
        interactive: false
        model: gridModel
        cellWidth: width / root.cols
        cellHeight: (cellWidth - 2) * 2 / 3 + 2   // a 3:2 card plus the 2px gutter
        cacheBuffer: cellHeight * 2
        bottomMargin: root.bottomSpace
        reuseItems: true
        highlightFollowsCurrentItem: false
        currentIndex: 0
        ScrollBar.vertical: ScrollBar {
            policy: gv.contentHeight + gv.bottomMargin > gv.height + 1 ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
            contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.28) }
            background: Item {}
        }
        // The wheel. Qt's own step is a fraction of a row. "Use free smooth scrolling" (Settings, Navigation and preview) scrolls pixel by pixel,
        // a notch being 150 px, or 0.65 of a card once the cards are bigger than that (never a whole row, which would look like the
        // snapping mode); without it a notch snaps by whole rows, two with many columns, one with few. Both ease over a moment. A touchpad keeps
        // the distance its own gesture reports.
        NumberAnimation { id: wheelEase; target: gv; property: "contentY"; duration: 140; easing.type: Easing.OutCubic }
        WheelHandler {
            target: null
            onWheel: (e) => {
                root.stopAutoScroll()
                var notches = e.angleDelta.y / 120
                var from = wheelEase.running ? wheelEase.to : gv.contentY
                var low = gv.originY, high = Math.max(low, gv.originY + gv.contentHeight - gv.height + gv.bottomMargin)
                var to
                if (e.pixelDelta.y !== 0) to = from - e.pixelDelta.y
                else if (settings.value("smooth") === true) to = from - notches * Math.max(150, gv.cellHeight * 0.65)
                else {
                    var rows = Math.round((from - low) / gv.cellHeight) - Math.sign(notches) * Math.max(1, Math.round(Math.abs(notches))) * (root.cols >= 7 ? 2 : 1)
                    to = low + rows * gv.cellHeight
                }
                wheelEase.stop()
                wheelEase.from = gv.contentY
                wheelEase.to = Math.max(low, Math.min(high, to))
                wheelEase.start()
                e.accepted = true
            }
        }
        DragHandler {   // a plain drag that starts on a photo carries it (or the pick it is part of) to a collection; clicks and Ctrl/Shift picks are not drags
            id: photoDrag
            target: null
            acceptedButtons: Qt.LeftButton
            acceptedModifiers: Qt.NoModifier
            dragThreshold: 12
            enabled: !root.renaming && !root.autoScrolling
            onActiveChanged: { if (active) root.beginDrag(centroid.scenePressPosition); else root.endDrag(true) }
            onCentroidChanged: if (active && root.dragging) root.dragAt = centroid.scenePosition
            onCanceled: root.dragCancelled = true
        }
        property int anchorIndex: 0       // where a Shift+click or Shift+arrow range starts: the last photo picked on its own
        function pick() { return root.row < 0 ? [] : photos.selectionRows(root.row) }
        // Review advances a single photo; a multi-photo selection stays in place. A press that took the decision off (W on a winner) stays too:
        // moving on would leave the photo undecided and out of sight.
        function advance(rows, decision) { if (rows.length === 1 && photos.get(rows[0]).decision === decision && settings.value("autoAdvance") === true && currentIndex < count - 1) stepTo(currentIndex + 1, false) }
        function revealCurrent() {
            if (root.restoringScope) return
            if (currentIndex < 0 || currentIndex >= count) return
            wheelEase.stop(); cancelFlick(); forceLayout()
            positionViewAtIndex(currentIndex, GridView.Contain)
            // Leave the current card above the floating controls as well as inside the viewport.
            var bottom = originY + (Math.floor(currentIndex / root.cols) + 1) * cellHeight
            var available = Math.max(cellHeight, height - root.bottomSpace)
            if (bottom > contentY + available)
                contentY = Math.min(Math.max(originY, originY + contentHeight - height + bottomMargin), bottom - available)
        }
        function stepTo(target, extend) {
            target = Math.max(0, Math.min(count - 1, target))
            if (extend) { target = Math.max(root.nf, target); photos.selectRange(Math.max(0, anchorIndex - root.nf), target - root.nf) }
            else { photos.clearSelection(); anchorIndex = target }
            currentIndex = target
            revealCurrent()
        }
        Keys.onPressed: (e) => {
            var k = e.key;
            if (root.autoScrolling) {
                root.stopAutoScroll()
                if (k === Qt.Key_Escape) { e.accepted = true; return }
            }
            if (root.dragging) { if (k === Qt.Key_Escape) { root.dragCancelled = true; root.endDrag(false) } e.accepted = true; return }
            var shift = (e.modifiers & Qt.ShiftModifier) !== 0, ctrl = (e.modifiers & Qt.ControlModifier) !== 0;
            if (settings.keyIs("accept_selection", k, e.modifiers)) { var rows = pick(); photos.setDecisionMany(rows, "win"); if (collections.activeId !== "") { var won = rows.filter(function (r) { return photos.get(r).decision === "win" }); if (won.length) collections.addRows(collections.activeId, won) } advance(rows, "win") }
            else if (settings.keyIs("collect_at_cursor", k, e.modifiers)) root.addToCollection();
            else if (settings.keyIs("keep_at_cursor", k, e.modifiers)) { var kr = pick(); photos.setDecisionMany(kr, "keep"); advance(kr, "keep") }
            else if (settings.keyIs("reject_selection", k, e.modifiers)) { var xr = pick(); photos.setDecisionMany(xr, "rej"); advance(xr, "rej") }
            else if (settings.keyIs("open_preview", k, e.modifiers)) { if (!e.isAutoRepeat) root.openCurrent() }
            else if (ctrl && k === Qt.Key_A) photos.selectAll();
            else if (ctrl) return;
            else if (shift && (k === Qt.Key_Left || k === Qt.Key_Right || k === Qt.Key_Up || k === Qt.Key_Down))
                stepTo(currentIndex + (k === Qt.Key_Left ? -1 : k === Qt.Key_Right ? 1 : k === Qt.Key_Up ? -root.cols : root.cols), true);
            else if (k === Qt.Key_Home) stepTo(0, shift);
            else if (k === Qt.Key_End) stepTo(count - 1, shift);
            else if (k === Qt.Key_PageUp || k === Qt.Key_PageDown) {
                var page = Math.max(1, Math.floor(height / Math.max(1, cellHeight))) * root.cols;
                stepTo(currentIndex + (k === Qt.Key_PageUp ? -page : page), shift);
            }
            else if (k === Qt.Key_Escape && photos.picked > 0) photos.clearSelection();
            else if (k === Qt.Key_U) photos.setDecisionMany(pick(), "");
            else if (k >= Qt.Key_0 && k <= Qt.Key_5) photos.setRatingMany(pick(), k - Qt.Key_0);      // 0 takes the rating off (in the viewer 0 is Fit)
            else if (k === Qt.Key_I) UiState.insp = !UiState.insp;
            else if (k === Qt.Key_Return || k === Qt.Key_Space) { if (!e.isAutoRepeat) root.openCurrent() }
            else return;
            e.accepted = true;
        }
        onCurrentIndexChanged: { if (!photos.picked) anchorIndex = currentIndex; if (!root.restoringScope) Qt.callLater(revealCurrent) }

        delegate: Item {
            id: cell
            GridView.onPooled: img.release()
            GridView.onReused: img.rev++
            required property int index
            required property string path
            required property string name
            required property string title
            required property string ext
            required property bool isRaw
            required property string decision
            required property int rating
            required property int ai
            required property bool selected
            required property bool isFolder
            width: GridView.view.cellWidth
            height: GridView.view.cellHeight
            readonly property bool current: GridView.isCurrentItem
            readonly property var flag: { void cull.flagRev; return cell.isFolder ? ({}) : (cull.gridFlag(cell.path) || ({})) }
            readonly property bool hot: hover.hovered || current
            Accessible.role: Accessible.ListItem
            Accessible.name: cell.isFolder ? "Folder " + cell.name
                : cell.name + (cell.decision === "win" ? ", winner" : cell.decision === "keep" ? ", keep" : cell.decision === "rej" ? ", rejected" : "")
                  + (cell.rating > 0 ? ", " + cell.rating + (cell.rating === 1 ? " star" : " stars") : "") + (cell.selected ? ", selected" : "")
            Accessible.selected: cell.selected || cell.current

            Item {
                anchors.fill: parent
                anchors.margins: 1
                clip: true
                Rectangle { anchors.fill: parent; color: theme.s2 }
                ThumbImage {
                    id: img
                    anchors.fill: parent
                    path: cell.isFolder ? "" : cell.path
                    bucket: root.cellBucket
                    blurFill: true
                    imageOpacity: UiState.filter === "rej" ? 1 : cell.decision === "rej" ? (root.dimRejected ? 0.38 : 1) : (cell.flag.label !== undefined ? 0.38 : 1)
                    desaturate: UiState.filter === "rej" ? 0 : cell.decision === "rej" ? (root.dimRejected ? 0.75 : 0) : (cell.flag.label !== undefined ? 0.7 : 0)
                    scale: hover.hovered && !cell.isFolder ? 1.04 : 1
                    Behavior on scale { NumberAnimation { duration: 500; easing.type: Easing.OutCubic } }
                }
                Item {   // a folder: the flat folder from the old grid (ui/prototype_style.folder_icon_pixmap) at 55% of the card, its name under it, both centred
                    visible: cell.isFolder
                    anchors.fill: parent
                    readonly property real edge: Math.max(40, Math.min(width, height) * 0.55)
                    readonly property real nameSize: Math.max(13, Math.min(20, width * 0.058))
                    readonly property real groupTop: (height - edge - 10 - nameSize * 1.5) / 2
                    id: folderCard
                    property int summaryRev: 0
                    Connections { target: photos; function onFolderSummaryReady(p) { if (p === cell.path) folderCard.summaryRev++ } }
                    Rectangle { anchors.fill: parent; color: theme.s3; opacity: hover.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 150 } } }
                    Icon {
                        x: (parent.width - parent.edge) / 2; y: parent.groupTop
                        path: "M2.4 19.2V6.24h7.2l2.16 2.64h9.84V19.2z"; size: parent.edge; filled: true; strokeWidth: 2.04; color: "#d3b15b"
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter; y: parent.groupTop + parent.edge + 10
                        width: parent.width - 24; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideMiddle
                        text: cell.name; color: theme.fg; font.pixelSize: parent.nameSize
                    }
                    Rectangle {   // what is in it, bottom left, on hover (the strip the photo cards use)
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 38
                        opacity: hover.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 200 } }
                        gradient: Gradient { GradientStop { position: 0; color: "transparent" } GradientStop { position: 1; color: "#b8000000" } }
                        Text { anchors.left: parent.left; anchors.leftMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7
                               text: { void parent.parent.summaryRev; return cell.isFolder && hover.hovered ? (photos.folderSummary(cell.path) || "Counting…") : "" }
                               color: "white"; font.pointSize: UiState.pt(11) }
                    }
                }
                Rectangle {
                    // the name / stars / format strip: padding 22 9 7, 11.5px text, a 0.72 black ramp
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    height: 51
                    opacity: cell.hot && !cell.isFolder ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 200 } }
                    gradient: Gradient { GradientStop { position: 0; color: "transparent" } GradientStop { position: 1; color: "#b8000000" } }
                    Text {
                        visible: UiState.showNames
                        anchors.left: parent.left; anchors.leftMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7
                        width: parent.width - 140; elide: Text.ElideRight; height: 18.5; verticalAlignment: Text.AlignVCenter
                        text: cell.title; color: "white"; font.pointSize: UiState.pt(13)
                    }
                    Row {
                        anchors.right: parent.right; anchors.rightMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7; spacing: 7
                        Item {
                            width: 74; height: 15
                            Text { y: 1; text: "\u2605\u2605\u2605\u2605\u2605"; color: Qt.rgba(1, 1, 1, 0.28); font.pixelSize: 13; font.letterSpacing: 1.2; height: 13 }
                            Item {
                                y: 1; width: parent.width * cell.rating / 5; height: 13; clip: true
                                Text { text: "\u2605\u2605\u2605\u2605\u2605"; color: theme.star; font.pixelSize: 13; font.letterSpacing: 1.2; height: 13 }
                            }
                        }
                        Rectangle {
                            visible: UiState.showBadges
                            width: fmt.implicitWidth + 13.5; height: 15.5; radius: 5.5; color: cell.isRaw ? Qt.alpha(theme.accent, 0.7) : Qt.rgba(1, 1, 1, 0.18)
                            Text { id: fmt; anchors.centerIn: parent; text: cell.ext; color: "white"; font.pointSize: UiState.pt(11); font.weight: Font.DemiBold; font.letterSpacing: 0.44 }
                        }
                    }
                }
                Rectangle {   // what the cull flagged this frame for (it is dimmed, never hidden)
                    visible: UiState.showBadges && cell.flag.label !== undefined
                    x: 8; y: 8; height: 20; width: fgl.implicitWidth + 25; radius: 10; color: Qt.rgba(10 / 255, 12 / 255, 16 / 255, 0.72)
                    Row { x: 6; anchors.verticalCenter: parent.verticalCenter; spacing: 5
                        Rectangle { width: 7; height: 7; radius: 3.5; color: cell.flag.color || "transparent"; anchors.verticalCenter: parent.verticalCenter }
                        Text { id: fgl; text: cell.flag.label || ""; color: "white"; font.pointSize: UiState.pt(11); font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter } }
                }
                Item {   // the collection that collects new winners: a small dark disc with its mark, on photos that are in it
                    id: collMark
                    readonly property var mark: { void collections.revision; void photos.scopeKind; return cell.isFolder ? ({}) : collections.markFor(cell.path, cell.name) }
                    visible: UiState.showBadges && mark.name !== undefined
                    x: 8; y: cell.flag.label !== undefined ? 34 : 8; width: 22; height: 22
                    Rectangle { x: -1; y: -1; width: 24; height: 24; radius: 12; color: Qt.rgba(1, 1, 1, 0.08) }
                    Rectangle { anchors.fill: parent; radius: 11; color: Qt.rgba(10 / 255, 12 / 255, 16 / 255, 0.6) }
                    CollMark { anchors.centerIn: parent; anchors.alignWhenCentered: false; color: collMark.mark.color || ""; iconPath: collMark.mark.iconPath || ""; ring: 11; iconSize: 13 }
                }
                Item {
                    // decision badge: a 22px disc with a 3px 40% black ring outside it (box-shadow), glyph drawn like the mockup's CSS
                    id: badge
                    visible: UiState.showBadges && cell.decision !== ""
                    x: parent.width - 8 - 22; y: 8; width: 22; height: 22
                    readonly property color tone: cell.decision === "win" ? theme.win : cell.decision === "keep" ? theme.keep : cell.decision === "rej" ? theme.rej : theme.rev
                    Rectangle { x: -3; y: -3; width: 28; height: 28; radius: 14; color: Qt.rgba(0, 0, 0, 0.4) }
                    Rectangle { anchors.fill: parent; radius: 11; color: badge.tone }
                    Shape {
                        visible: cell.decision === "win"
                        x: 5.5; y: 5.5; width: 11; height: 11; preferredRendererType: Shape.CurveRenderer
                        ShapePath {
                            strokeColor: "transparent"; fillColor: "#10261b"
                            PathPolyline { path: [Qt.point(5.5, 0), Qt.point(6.82, 3.96), Qt.point(11, 4.18), Qt.point(7.7, 6.6), Qt.point(8.8, 11), Qt.point(5.5, 8.36), Qt.point(2.2, 11), Qt.point(3.3, 6.6), Qt.point(0, 4.18), Qt.point(4.18, 3.96)] }
                        }
                    }
                    Item {
                        visible: cell.decision === "keep"
                        x: 7.5; y: 4; width: 7; height: 12; rotation: 45; transformOrigin: Item.Center   // ::before is content-box: 5x10 plus 2px borders
                        Rectangle { x: 5; width: 2; height: 12; color: "white" }
                        Rectangle { y: 10; width: 7; height: 2; color: "white" }
                    }
                    Item {
                        visible: cell.decision === "rej"
                        anchors.fill: parent
                        Rectangle { anchors.centerIn: parent; width: 10; height: 2; radius: 1; rotation: 45; color: "white" }
                        Rectangle { anchors.centerIn: parent; width: 10; height: 2; radius: 1; rotation: -45; color: "white" }
                    }
                    Rectangle { visible: cell.decision === "rev"; anchors.centerIn: parent; width: 12; height: 12; radius: 6; color: "transparent"; border.width: 2; border.color: "#2a1f06" }
                }
                Item {
                    // selection: 3px accent ring with a 1px 45% black line inside it, like the two inset shadows
                    anchors.fill: parent
                    opacity: cell.current || cell.selected ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 200 } }
                    Rectangle { anchors.fill: parent; color: "transparent"; radius: 3; border.color: theme.accent; border.width: 3 }
                    Rectangle { anchors.fill: parent; anchors.margins: 3; color: "transparent"; border.color: Qt.rgba(0, 0, 0, 0.45); border.width: 1 }
                }
            }
            HoverHandler { id: hover }
            TapHandler {   // a click: this photo alone
                acceptedModifiers: Qt.NoModifier
                onTapped: (point) => { if (root.blocked(point.scenePosition)) return; photos.clearSelection(); gv.currentIndex = cell.index; gv.anchorIndex = cell.index; gv.forceActiveFocus() }
                onDoubleTapped: (point) => { if (root.blocked(point.scenePosition)) return; if (cell.isFolder) libraryNav.openFolder(cell.path); else root.openRequested(cell.index - root.nf) }
            }
            TapHandler {   // Ctrl+click: add it to the pick or take it out
                acceptedModifiers: Qt.ControlModifier
                onTapped: (point) => { if (root.blocked(point.scenePosition) || cell.isFolder) return; photos.toggleSelected(cell.index - root.nf, Math.max(0, gv.currentIndex - root.nf)); gv.currentIndex = cell.index; gv.anchorIndex = cell.index; gv.forceActiveFocus() }
            }
            TapHandler {   // Shift+click: everything from the last photo picked on its own to this one
                acceptedModifiers: Qt.ShiftModifier
                onTapped: (point) => { if (root.blocked(point.scenePosition) || cell.isFolder) return; photos.selectRange(Math.max(0, gv.anchorIndex - root.nf), cell.index - root.nf); gv.currentIndex = cell.index; gv.forceActiveFocus() }
            }
        }
    }
    Item {   // the name being edited, over the bottom of its card; what is wrong with it (or would be on Windows) is said under it
        id: renameBox
        objectName: "gridRename"
        readonly property Item card: root.renameRow >= 0 ? gv.itemAtIndex(root.renameRow + root.nf) : null
        readonly property string problem: root.renameError !== "" ? root.renameError : (root.renameVerdict.error || "")
        readonly property string note: problem !== "" ? problem : (root.renameVerdict.warning || "")
        visible: card !== null
        z: 6
        x: card ? gv.x + card.x - gv.contentX + 7 : 0
        y: card ? gv.y + card.y - gv.contentY + card.height - height - 7 : 0
        width: card ? card.width - 14 : 0
        height: 34
        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: (w) => { w.accepted = true } }   // a click on the field is not a click on the card under it
        Rectangle {
            anchors.fill: parent; radius: 10; color: theme.base
            border.width: 1; border.color: renameBox.problem !== "" ? theme.rej : theme.accent
            Behavior on border.color { ColorAnimation { duration: 200 } }
        }
        TextInput {
            id: nameField
            objectName: "gridRenameInput"
            x: 10; anchors.verticalCenter: parent.verticalCenter
            width: parent.width - 20 - (extTag.visible ? extTag.width + 8 : 0)
            color: theme.fg; font.pointSize: UiState.pt(13); clip: true; selectByMouse: true
            selectionColor: theme.accent; selectedTextColor: theme.onAccent
            readOnly: root.renameBusy
            opacity: root.renameBusy ? 0.6 : 1
            onTextEdited: { root.renameError = ""; root.renameVerdict = photos.renameCheck(root.renameRow, text) }
            Keys.onShortcutOverride: (e) => { e.accepted = !(e.key >= Qt.Key_F1 && e.key <= Qt.Key_F35) }      // while a name is typed every key is text, not a command
            Keys.onPressed: (e) => {
                if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) { root.commitRename(); e.accepted = true }
                else if (e.key === Qt.Key_Escape) { root.endRename(); e.accepted = true }
            }
            onActiveFocusChanged: if (!activeFocus && root.renameRow >= 0 && !root.renameBusy) root.cancelRename()      // a click elsewhere leaves the name as it was
            Accessible.role: Accessible.EditableText
            Accessible.name: "New name"
            Accessible.description: renameBox.note
        }
        Rectangle {   // the extension stays as it is: shown, not edited
            id: extTag
            readonly property string ext: { var dot = root.renamePath.lastIndexOf("."); return dot > root.renamePath.lastIndexOf("/") && dot > root.renamePath.lastIndexOf("\\") ? root.renamePath.slice(dot) : "" }
            visible: ext !== "" && renameBox.width > 150
            anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
            width: extLabel.implicitWidth + 12; height: 18; radius: 6; color: theme.s3
            Text { id: extLabel; anchors.centerIn: parent; text: extTag.ext; color: theme.fg3; font.pointSize: UiState.pt(11) }
        }
        Rectangle {
            objectName: "gridRenameNote"
            visible: renameBox.note !== ""
            readonly property bool above: renameBox.y + renameBox.height + 6 + height > root.height - root.bottomSpace
            x: Math.max(-renameBox.x + 6, Math.min(0, root.width - renameBox.x - width - 6))
            y: above ? -height - 6 : parent.height + 6
            width: Math.max(Math.min(renameBox.width, root.width - 12), Math.min(320, noteText.implicitWidth + 20, root.width - 12)); height: noteText.height + 14
            radius: 9; color: theme.s3; border.color: renameBox.problem !== "" ? theme.rej : theme.rev
            Text { id: noteText; x: 10; y: 7; width: parent.width - 20; wrapMode: Text.Wrap; text: renameBox.note; color: theme.fg; font.pointSize: UiState.pt(11.5) }
        }
    }
    // Middle click latches an origin; distance above/below it controls speed.
    // Holding the button and dragging also works, ending on release.
    MouseArea {
        id: autoScrollInput
        objectName: "gridAutoScrollInput"
        anchors.fill: parent
        acceptedButtons: root.autoScrolling ? Qt.AllButtons : Qt.MiddleButton
        hoverEnabled: true
        preventStealing: true
        property bool moved: false
        property bool stopping: false
        onPressed: (event) => {
            stopping = root.autoScrolling
            if (event.button !== Qt.MiddleButton) {
                event.accepted = stopping
                return
            }
            if (stopping || root.blocked(mapToItem(null, event.x, event.y))) return
            wheelEase.stop(); gv.cancelFlick(); gv.forceActiveFocus()
            root.scrollOrigin = Qt.point(event.x, event.y)
            root.scrollPointerY = event.y
            moved = false
            root.autoScrolling = true
        }
        onPositionChanged: (event) => {
            if (!root.autoScrolling) return
            root.scrollPointerY = event.y
            if (Math.abs(event.x - root.scrollOrigin.x) + Math.abs(event.y - root.scrollOrigin.y) >= 10) moved = true
        }
        onReleased: if (stopping || moved) root.stopAutoScroll()
        onCanceled: root.stopAutoScroll()
        onExited: if (!pressed) root.stopAutoScroll()
        // Cancel passive photo/folder taps behind the consumed gesture.
        TapHandler {
            enabled: root.autoScrolling
            acceptedButtons: Qt.AllButtons
            gesturePolicy: TapHandler.WithinBounds
        }
        onWheel: (event) => { root.stopAutoScroll(); event.accepted = false }
    }
    Timer {
        interval: 16; repeat: true; running: root.autoScrolling
        onTriggered: {
            if (UiState.menu !== "" || UiState.topMenu !== "" || UiState.paletteOpen || !root.visible) { root.stopAutoScroll(); return }
            var delta = root.scrollPointerY - root.scrollOrigin.y
            var magnitude = Math.abs(delta) - 16
            if (magnitude <= 0) return
            var step = Math.sign(delta) * Math.min(130, magnitude / 9 * (1 + magnitude / 140))
            var high = Math.max(gv.originY, gv.originY + gv.contentHeight - gv.height + gv.bottomMargin)
            gv.contentY = Math.max(gv.originY, Math.min(high, gv.contentY + step))
        }
    }
    Image {
        objectName: "gridAutoScrollAnchor"
        visible: root.autoScrolling && source.toString() !== ""
        x: root.scrollOrigin.x - width / 2; y: root.scrollOrigin.y - height / 2
        source: root.scrollCursor ? root.scrollCursor.anchorSource : ""
        width: root.scrollCursor ? root.scrollCursor.anchorWidth : 0
        height: root.scrollCursor ? root.scrollCursor.anchorHeight : 0
    }
    Column {   // nothing to show: say why, instead of an empty grid
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -root.bottomSpace / 3
        spacing: 10
        visible: photos.shown === 0 && !photos.loading && photos.subfolders.length === 0
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: photos.folderPath === "" ? "library" : (photos.total > 0 ? "funnel" : "view"); size: 52; strokeWidth: 1.2; color: theme.fg3 }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; color: theme.fg2; font.pointSize: UiState.pt(14); font.weight: Font.Medium
            text: photos.folderPath === "" ? "Choose a folder to start triaging images" : photos.loadError !== "" ? "This folder could not be read" : (photos.total > 0 ? "No photos match the filters" : "No photos in this folder")
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; color: theme.fg3; font.pointSize: UiState.pt(11.5)
            text: photos.folderPath === "" ? "Pick a drive or folder in the Library on the left." : photos.loadError !== "" ? photos.loadError + " Press F5 to try again." : (photos.total > 0 ? "Clear the filters from the bar below to see them." : "It has no supported images and no folders. Pick another from the Library.")
        }
    }
    MouseArea {   // right click: the photo's menu, or the folder's menu on empty space (left clicks pass through to the cells)
        anchors.fill: parent; acceptedButtons: Qt.RightButton
        onPressed: (m) => {
            if (root.autoScrolling) { root.stopAutoScroll(); return }
            var idx = gv.indexAt(m.x - gv.x + gv.contentX, m.y - gv.y + gv.contentY)
            var p = mapToItem(null, m.x, m.y)
            if (idx >= root.nf) { gv.currentIndex = idx; gv.forceActiveFocus(); root.contextRequested("photo", idx - root.nf, p.x, p.y) }
            else root.contextRequested("folder", -1, p.x, p.y)
        }
    }
}
