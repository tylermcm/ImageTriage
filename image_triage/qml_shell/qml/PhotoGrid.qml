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
    Connections {
        target: gridModel
        function onModelAboutToBeReset() { root.keepRow = root.row }
        function onModelReset() { if (root.keepRow >= 0) gv.currentIndex = root.keepRow + gridModel.folders }
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
        keyNavigationEnabled: true
        boundsBehavior: Flickable.StopAtBounds
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
        property int anchorIndex: 0       // where a Shift+click or Shift+arrow range starts: the last photo picked on its own
        function pick() { return root.row < 0 ? [] : photos.selectionRows(root.row) }
        // "Advance after Accept or Reject" (the Widgets setting): on one photo, W and X go on to the next; on a pick they stay
        function advance(rows) { if (rows.length === 1 && settings.value("autoAdvance") === true && currentIndex < count - 1) { photos.clearSelection(); currentIndex = currentIndex + 1; anchorIndex = currentIndex } }
        function stepTo(target, extend) {
            target = Math.max(0, Math.min(count - 1, target))
            if (extend) { target = Math.max(root.nf, target); photos.selectRange(Math.max(0, anchorIndex - root.nf), target - root.nf) }
            else { photos.clearSelection(); anchorIndex = target }
            currentIndex = target
            if (target < root.cols) positionViewAtBeginning()      // the first row: show the folder cards above it too
            else positionViewAtIndex(target, GridView.Contain)
        }
        Keys.onPressed: (e) => {
            var k = e.key;
            var shift = (e.modifiers & Qt.ShiftModifier) !== 0, ctrl = (e.modifiers & Qt.ControlModifier) !== 0;
            if (settings.keyIs("accept_selection", k, e.modifiers)) { var rows = pick(); photos.setDecisionMany(rows, "win"); if (collections.activeId !== "") for (var i = 0; i < rows.length; ++i) collections.addSelection(collections.activeId, rows[i]); advance(rows) }
            else if (settings.keyIs("collect_at_cursor", k, e.modifiers)) root.addToCollection();
            else if (settings.keyIs("keep_at_cursor", k, e.modifiers)) photos.setDecisionMany(pick(), "keep");
            else if (settings.keyIs("reject_selection", k, e.modifiers)) { var xr = pick(); photos.setDecisionMany(xr, "rej"); advance(xr) }
            else if (settings.keyIs("open_preview", k, e.modifiers)) { if (!e.isAutoRepeat) root.openCurrent() }
            else if (ctrl && k === Qt.Key_A) photos.selectAll();
            else if (ctrl) return;
            else if (shift && (k === Qt.Key_Left || k === Qt.Key_Right || k === Qt.Key_Up || k === Qt.Key_Down))
                stepTo(currentIndex + (k === Qt.Key_Left ? -1 : k === Qt.Key_Right ? 1 : k === Qt.Key_Up ? -root.cols : root.cols), true);
            else if (k === Qt.Key_Escape && photos.picked > 0) photos.clearSelection();
            else if (k === Qt.Key_U) photos.setDecisionMany(pick(), "");
            else if (k >= Qt.Key_1 && k <= Qt.Key_5) photos.setRatingMany(pick(), k - Qt.Key_0);
            else if (k === Qt.Key_I) UiState.insp = !UiState.insp;
            else if (k === Qt.Key_Return || k === Qt.Key_Space) { if (!e.isAutoRepeat) root.openCurrent() }
            else return;
            e.accepted = true;
        }
        onCurrentIndexChanged: { if (!photos.picked) anchorIndex = currentIndex }

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
            // Contrast. The name, stars and format tag are drawn over the bottom strip of the photo, whatever it is: its brightness
            // (thumbs.tone, measured on the cached thumbnail, only for the card that shows them) decides between light ink on a dark scrim
            // and dark ink on a light one. A rejected or flagged photo is drawn dimmed, so it is measured the way it is shown.
            readonly property real surfaceLuma: 0.2126 * theme.s2.r + 0.7152 * theme.s2.g + 0.0722 * theme.s2.b
            readonly property real tone: { void img.rev; void img.bucket; return cell.hot && !cell.isFolder ? thumbs.tone(cell.path, img.bucket) : -1 }
            readonly property real shownTone: tone < 0 ? -1 : tone * img.imageOpacity + (1 - img.imageOpacity) * surfaceLuma
            readonly property bool bright: shownTone > 0.6

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
                    imageOpacity: cell.decision === "rej" || cell.flag.label !== undefined ? 0.38 : 1
                    desaturate: cell.decision === "rej" ? 0.75 : (cell.flag.label !== undefined ? 0.7 : 0)
                    scale: hover.hovered && !cell.isFolder ? 1.04 : 1
                    Behavior on scale { NumberAnimation { duration: 500; easing.type: Easing.OutCubic } }
                }
                Item {   // a folder: the flat folder from the old grid (ui/prototype_style.folder_icon_pixmap) at 55% of the card, its name under it, both centred
                    visible: cell.isFolder
                    anchors.fill: parent
                    readonly property real edge: Math.max(40, Math.min(width, height) * 0.55)
                    readonly property real nameSize: Math.max(13, Math.min(20, width * 0.058))
                    readonly property real groupTop: (height - edge - 10 - nameSize * 1.5) / 2
                    property int summaryRev: 0
                    Connections { target: photos; function onFolderSummaryReady(p) { if (p === cell.path) parent.summaryRev++ } }
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
                    height: 45.1
                    opacity: cell.hot && !cell.isFolder ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 200 } }
                    gradient: Gradient { GradientStop { position: 0; color: cell.bright ? "#00ffffff" : "transparent" } GradientStop { position: 1; color: cell.bright ? "#d9ffffff" : "#b8000000" } }
                    Text {
                        visible: UiState.showNames
                        anchors.left: parent.left; anchors.leftMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7
                        width: parent.width - 120; elide: Text.ElideRight; height: 16.1; verticalAlignment: Text.AlignVCenter
                        text: cell.title; color: cell.bright ? "#15181f" : "white"; font.pointSize: UiState.pt(11.5)
                        style: cell.bright ? Text.Outline : Text.Normal; styleColor: Qt.rgba(1, 1, 1, 0.6)
                    }
                    Row {
                        anchors.right: parent.right; anchors.rightMargin: 9; anchors.bottom: parent.bottom; anchors.bottomMargin: 7; spacing: 7
                        Item {
                            width: 62.5; height: 13
                            Text { y: 1; text: "\u2605\u2605\u2605\u2605\u2605"; color: cell.bright ? Qt.rgba(0, 0, 0, 0.30) : Qt.rgba(1, 1, 1, 0.28); font.pixelSize: 11; font.letterSpacing: 1; height: 11 }
                            Item {
                                y: 1; width: parent.width * cell.rating / 5; height: 11; clip: true
                                Text { text: "\u2605\u2605\u2605\u2605\u2605"; color: cell.bright ? Qt.darker(theme.star, 1.5) : theme.star; font.pixelSize: 11; font.letterSpacing: 1; height: 11 }
                            }
                        }
                        Rectangle {
                            visible: UiState.showBadges
                            width: fmt.implicitWidth + 11.7; height: 13; radius: 5; color: cell.isRaw ? Qt.alpha(theme.accent, cell.bright ? 0.92 : 0.7) : (cell.bright ? Qt.rgba(0, 0, 0, 0.62) : Qt.rgba(1, 1, 1, 0.18))
                            Text { id: fmt; anchors.centerIn: parent; text: cell.ext; color: "white"; font.pointSize: UiState.pt(9.5); font.weight: Font.DemiBold; font.letterSpacing: 0.38 }
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
    Column {   // nothing to show: say why, instead of an empty grid
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -root.bottomSpace / 3
        spacing: 10
        visible: photos.shown === 0 && !photos.loading && photos.subfolders.length === 0
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: photos.folderPath === "" ? "library" : (photos.total > 0 ? "funnel" : "view"); size: 52; strokeWidth: 1.2; color: theme.fg3 }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; color: theme.fg2; font.pointSize: UiState.pt(14); font.weight: Font.Medium
            text: photos.folderPath === "" ? "Choose a folder to start triaging images" : (photos.total > 0 ? "No photos match the filters" : "No photos in this folder")
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; color: theme.fg3; font.pointSize: UiState.pt(11.5)
            text: photos.folderPath === "" ? "Pick a drive or folder in the Library on the left." : (photos.total > 0 ? "Clear the filters from the bar below to see them." : "It has no supported images and no folders. Pick another from the Library.")
        }
    }
    MouseArea {   // right click: the photo's menu, or the folder's menu on empty space (left clicks pass through to the cells)
        anchors.fill: parent; acceptedButtons: Qt.RightButton
        onPressed: (m) => {
            var idx = gv.indexAt(m.x - gv.x + gv.contentX, m.y - gv.y + gv.contentY)
            var p = mapToItem(null, m.x, m.y)
            if (idx >= root.nf) { gv.currentIndex = idx; gv.forceActiveFocus(); root.contextRequested("photo", idx - root.nf, p.x, p.y) }
            else root.contextRequested("folder", -1, p.x, p.y)
        }
    }
}
