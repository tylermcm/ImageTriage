import QtQuick
import QtQuick.Window
import QtQuick.Dialogs
import QtQuick.Dialogs

Window {
    id: win
    visible: true
    width: 1440; height: 900
    minimumWidth: 640; minimumHeight: 520
    title: "Image Triage"
    color: theme.base
    flags: Qt.Window | Qt.FramelessWindowHint      // no system title bar: TopBar is the title bar, WindowEdges the resize grips

    readonly property bool compact: width <= 900          // the mockup's @media (max-width: 900px)
    Binding { target: UiState; property: "compact"; value: win.compact }
    Binding { target: UiState; property: "selRow"; value: grid.view.currentIndex }
    // a collection dragged over the PocketDrop button opens that page after a moment
    readonly property bool railHot: UiState.pdSim ? UiState.tab !== "drop" : UiState.pdDragging && rail.overDrop(UiState.pdGhostX, UiState.pdGhostY)
    Binding { target: UiState; property: "pdRailHot"; value: win.railHot }
    Timer { interval: 450; running: UiState.pdRailHot && UiState.tab !== "drop" && !UiState.pdSim; onTriggered: UiState.tab = "drop" }
    // The side panel and the inspector have the widths the user dragged them to, within a minimum (below it their contents would clip)
    // and a maximum, and never so wide that the grid keeps less than mainMin.
    readonly property real mainMin: 400
    readonly property real availSide: Math.max(0, shell.width - rail.width - mainMin)
    readonly property real panelWanted: Math.max(UiState.panelMin, Math.min(UiState.panelMax, UiState.panelW, availSide - (UiState.insp ? UiState.inspMin : 0)))
    readonly property real inspWanted: Math.max(UiState.inspMin, Math.min(UiState.inspMax, UiState.inspW, availSide - (UiState.tab !== "" ? panelWanted : 0)))
    property bool resizing: false                      // a handle is being dragged: follow the pointer, do not animate
    readonly property real panelTarget: UiState.tab !== "" ? panelWanted : 0
    readonly property real inspTarget: UiState.insp ? inspWanted : 0
    property real panelW: panelTarget
    property real inspW: inspTarget
    Behavior on panelW { enabled: !win.resizing; NumberAnimation { duration: 600; easing.type: Easing.OutQuint } }
    Behavior on inspW { enabled: !win.resizing; NumberAnimation { duration: 600; easing.type: Easing.OutQuint } }

    Item {
        id: appContent
        anchors.fill: parent
        TopBar { id: top; objectName: "topBar"; width: parent.width }
        StatusBar { id: status; width: parent.width; anchors.bottom: parent.bottom }

        Item {
            id: shell
            y: top.height; width: parent.width; height: parent.height - top.height - status.height

            Rail { id: rail; z: 25; height: parent.height; row: grid.view.currentIndex }
            SidePanel {
                id: sidePanel
                // wide: a column that animates its width; compact: a 320px sheet that slides in over the grid
                readonly property real sheetW: Math.min(320, shell.width - rail.width)
                fullW: win.compact ? sheetW : win.panelWanted
                x: win.compact ? rail.width + (UiState.tab !== "" ? 0 : -sheetW * 1.05) : rail.width
                Behavior on x { enabled: win.compact; NumberAnimation { duration: 550; easing.type: Easing.OutQuint } }
                width: win.compact ? sheetW : win.panelW
                height: parent.height
                z: win.compact ? 20 : 0
            }
            Rectangle {   // box-shadow: 0 0 40px rgba(0,0,0,.5) of the sheet, on its right side
                visible: win.compact && sidePanel.x + sidePanel.width > rail.width
                x: sidePanel.x + sidePanel.width; z: 19; width: 40; height: parent.height
                gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: "#80000000" } GradientStop { position: 1; color: "transparent" } }
            }

            Item {
                id: main
                x: win.compact ? rail.width : rail.width + win.panelW
                width: win.compact ? shell.width - rail.width : shell.width - x - win.inspW
                height: shell.height
                z: win.compact ? 0 : 1               // the bar's AI button may overhang the inspector, as in the mockup

                Rectangle {
                    id: head
                    readonly property string kind: { void photos.scopeKind; return photos.scopeKind }
                    readonly property var col: { void collections.revision; void photos.scopeId; return kind === "collection" ? collections.get(photos.scopeId) : ({}) }
                    readonly property var person: { void people.revision; void photos.scopeId; return kind === "person" ? people.card(photos.scopeId) : ({}) }
                    width: parent.width; height: 44; color: theme.base
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    component Chip: Rectangle { property alias label: cl.text; height: 18.5; width: cl.implicitWidth + 16; radius: 10; color: theme.s2; border.color: theme.lineSoft
                                                Text { id: cl; anchors.centerIn: parent; color: theme.fg2; font.pixelSize: 11; font.weight: Font.Medium } }
                    component HBtn: Item { property alias text: bl.text; signal clicked(); implicitWidth: bl.implicitWidth + 22; width: implicitWidth; height: 28
                                           Rectangle { anchors.fill: parent; radius: 9; color: bh.hovered ? Qt.tint(theme.s3, Qt.alpha(theme.lift, 0.06)) : theme.s3; border.color: theme.lineSoft }
                                           Text { id: bl; anchors.centerIn: parent; color: theme.fg; font.pixelSize: 12 }
                                           HoverHandler { id: bh; cursorShape: Qt.PointingHandCursor }
                                           TapHandler { onTapped: parent.clicked() }
                                           Accessible.role: Accessible.Button; Accessible.name: bl.text }
                    Row {   // the folder
                        visible: head.kind === ""
                        anchors.verticalCenter: parent.verticalCenter; x: 14; spacing: 10
                        Text { text: photos.folderName || "Open a folder"; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: -0.14 }
                        Text { visible: !win.compact; text: photos.folderPath; color: theme.fg3; font.pixelSize: 13; width: Math.min(implicitWidth, Math.max(0, main.width - 330 - x)); elide: Text.ElideRight }
                    }
                    Row {   // a collection, the winners of every shoot, a person or a best-of shortlist
                        visible: head.kind !== ""
                        anchors.verticalCenter: parent.verticalCenter; x: 14; spacing: 10
                        CollMark { visible: head.kind === "collection"; anchors.verticalCenter: parent.verticalCenter; color: head.col.color || ""; iconPath: head.col.iconPath || ""; ring: 16; iconSize: 18 }
                        Icon { visible: head.kind === "winners"; anchors.verticalCenter: parent.verticalCenter; name: "heart"; size: 18; strokeWidth: 1.8; color: theme.fg2 }
                        Icon { visible: head.kind === "best"; anchors.verticalCenter: parent.verticalCenter; name: "star"; size: 18; strokeWidth: 1.8; color: theme.fg2 }
                        FaceDisc { visible: head.kind === "person"; anchors.verticalCenter: parent.verticalCenter; width: 22; height: 22; hue: head.person.hue || 0; initial: head.person.init || "?"; fontSize: 10.5 }
                        Text { anchors.verticalCenter: parent.verticalCenter; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: -0.14
                               width: Math.min(implicitWidth, Math.max(60, main.width - 560))
                               elide: Text.ElideRight
                               text: head.kind === "collection" ? (head.col.name || "") : head.kind === "winners" ? "Winners" : head.kind === "best" ? "Best of set" : people.labelOf(photos.scopeId) }
                        Chip { anchors.verticalCenter: parent.verticalCenter; label: head.kind === "collection" ? "Collection" : head.kind === "winners" ? "Every shoot" : head.kind === "best" ? "Shortlist" : "Person" }
                        Text { visible: !win.compact; anchors.verticalCenter: parent.verticalCenter; color: theme.fg3; font.pixelSize: 13
                               width: Math.min(implicitWidth, Math.max(0, main.width - (head.kind === "collection" ? 380 : 270) - x))
                               elide: Text.ElideRight
                               text: head.kind === "collection" ? "References only" : head.kind === "winners" ? "Grouped by folder" : head.kind === "best" ? bestOf.summary : "Photos with this face in this folder" }
                    }
                    Row {
                        anchors.verticalCenter: parent.verticalCenter; anchors.right: parent.right; anchors.rightMargin: 14; spacing: 10
                        HBtn { visible: head.kind !== ""; text: "Back to folder"; onClicked: collections.closeScope() }
                        HBtn { visible: head.kind === "collection"; text: "Remove selection"; onClicked: collections.removeSelection(photos.scopeId, grid.view.currentIndex) }
                        Rectangle {
                            height: 24; width: shown.implicitWidth + 31; radius: 12; color: theme.s2; border.color: theme.lineSoft
                            Row { anchors.centerIn: parent; spacing: 6
                                Rectangle { width: 7; height: 7; radius: 4; color: theme.win; anchors.verticalCenter: parent.verticalCenter }
                                Text { id: shown; text: photos.shown + " of " + photos.total + " shown"; color: theme.fg2; font.pointSize: UiState.pt(11.5) }
                            }
                        }
                        Rectangle {
                            visible: head.kind === ""
                            height: 24; width: wins.implicitWidth + 20; radius: 12; color: theme.s2; border.color: theme.lineSoft
                            Text { id: wins; anchors.centerIn: parent; text: photos.winCount + " winners"; color: theme.fg; font.pointSize: UiState.pt(11.5) }
                        }
                    }
                }
                Rectangle {   // narrowed the folder down? offer to put the whole result into a collection
                    id: addBar
                    visible: photos.scopeKind === "" && photos.shown < photos.total && photos.shown > 0
                    y: head.height; width: parent.width; height: visible ? 42 : 0
                    color: Qt.tint(theme.base, Qt.alpha(theme.accent, 0.09))
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 14; anchors.verticalCenter: parent.verticalCenter; text: photos.shown + (photos.shown === 1 ? " photo matches" : " photos match"); color: theme.fg2; font.pointSize: UiState.pt(12.5) }
                    SButton { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: "Add All to Collection\u2026"; variant: "primary"; h: 28; hpad: 11; radius: 9
                              onClicked: { UiState.collPickerQuery = ""; UiState.collPicker = true } }
                }
                PhotoGrid { id: grid; onContextRequested: (kind, row, x, y) => ctxHost.show(kind, row, x, y, ""); onOpenRequested: (row) => popout.open(row); y: head.height + addBar.height; width: parent.width; height: parent.height - head.height - addBar.height; overlay: floatBar }
                FloatingBar {
                    id: floatBar
                    sourceItem: grid.view
                    avail: main.width
                    row: grid.view.currentIndex
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom; anchors.bottomMargin: win.compact ? 12 : 20
                }
            }

            Rectangle {
                visible: win.compact && insp.x < shell.width
                x: insp.x - 40; z: 29; width: 40; height: parent.height
                gradient: Gradient { orientation: Gradient.Horizontal; GradientStop { position: 0; color: "transparent" } GradientStop { position: 1; color: "#80000000" } }
            }
            Inspector {
                id: insp
                readonly property real sheetW: Math.min(380, shell.width)
                fullW: win.compact ? sheetW : win.inspWanted
                x: win.compact ? shell.width - (UiState.insp ? sheetW : -sheetW * 0.05) : shell.width - win.inspW
                Behavior on x { enabled: win.compact; NumberAnimation { duration: 550; easing.type: Easing.OutQuint } }
                width: win.compact ? sheetW : win.inspW; height: parent.height
                z: win.compact ? 30 : 0
                row: grid.view.currentIndex
                onStep: (d) => { grid.view.currentIndex = Math.max(0, Math.min(photos.shown - 1, grid.view.currentIndex + d)) }
                onAnalyze: win.runCommand("ai.analyze")
                onPick: (r) => { grid.view.currentIndex = r }
            }
            SideResizer {   // the edge between the side panel and the grid
                objectName: "panelResizer"
                visible: !win.compact && UiState.tab !== ""
                x: rail.width + win.panelW - width / 2; z: 40; height: parent.height
                onMoved: (px) => { win.resizing = true; UiState.panelW = Math.max(UiState.panelMin, Math.min(UiState.panelMax, px - rail.width)) }
                onFinished: { win.resizing = false; settings.setLayoutWidth("panel", win.panelWanted) }
                onReset: { UiState.panelW = 300; settings.setLayoutWidth("panel", 300) }
            }
            SideResizer {   // the edge between the grid and the inspector
                objectName: "inspResizer"
                visible: !win.compact && UiState.insp
                x: shell.width - win.inspW - width / 2; z: 40; height: parent.height
                onMoved: (px) => { win.resizing = true; UiState.inspW = Math.max(UiState.inspMin, Math.min(UiState.inspMax, shell.width - px)) }
                onFinished: { win.resizing = false; settings.setLayoutWidth("inspector", win.inspWanted) }
                onReset: { UiState.inspW = 352; settings.setLayoutWidth("inspector", 352) }
            }
        }
    }

    WindowEdges { z: 1000; anchors.fill: parent }

    // the drag ghost and its hint
    PocketDropGhost {
        z: 80; visible: UiState.pdDragging; info: UiState.pdDragInfo
        x: UiState.pdSim ? (UiState.tab === "drop" ? 150 : 58) : UiState.pdGhostX + 14
        y: UiState.pdSim ? (UiState.tab === "drop" ? 290 : 236) : UiState.pdGhostY + 10
    }
    Rectangle {
        z: 80; visible: UiState.pdDragging && UiState.pdRailHot; x: UiState.pdSim ? 78 : UiState.pdGhostX + 20; y: UiState.pdSim ? 318 : UiState.pdGhostY + 58
        width: tipText.implicitWidth + 22; height: 30; radius: 9; color: theme.s3; border.color: theme.lineSoft
        Text { id: tipText; anchors.centerIn: parent; text: "Hold to open PocketDrop"; color: theme.fg; font.pixelSize: 12 }
    }
    ConfirmCard {
        id: riskCard
        title: "Anyone with this link can download the shared files"
        body: "The link remains active until it expires, you create a new link, leave Anywhere mode, or close Image Triage. Sending files back to this PC is protected by a separate receive code shown only in PocketDrop."
        confirmText: "Continue"; cancelText: "Cancel"; checkText: "Don\u2019t show this warning again"
        onAccepted: (dismiss) => pocketDrop.confirmRisk(true, dismiss)
        onRejected: pocketDrop.confirmRisk(false, false)
    }
    ConfirmCard { id: infoCard; confirmText: "OK" }
    // delete, move and copy (file_actions.py): the folder to move or copy to, and the questions it may ask (delete for good)
    property string destMode: "move"
    property int destRow: -1
    function chooseDestination(mode, row) { destMode = mode; destRow = row; destDialog.title = mode === "copy" ? "Copy to folder" : "Move to folder"; destDialog.open() }
    FolderDialog { id: destDialog; onAccepted: { var path = selectedFolder.toString().replace(/^file:\/\/\/?/, Qt.platform.os === "windows" ? "" : "/"); if (win.destMode === "copy") fileActions.copyRow(win.destRow, path); else fileActions.moveRow(win.destRow, path); viewer.refocus() } onRejected: viewer.refocus() }
    property string fileQuestion: ""
    ConfirmCard { id: fileCard; cancelText: "Cancel"; onAccepted: { fileActions.answer(win.fileQuestion, true); viewer.refocus() } onRejected: { fileActions.answer(win.fileQuestion, false); viewer.refocus() } }
    Connections { target: fileActions; function onConfirmRequested(id, title, body, confirm) { win.fileQuestion = id; fileCard.title = title; fileCard.body = body; fileCard.confirmText = confirm; fileCard.open() } }
    Connections { target: pocketDrop; function onRiskChanged() { if (pocketDrop.riskPending) riskCard.open() } }
    Connections { target: pocketDrop; function onAlertRequested(title, message) { infoCard.title = title; infoCard.body = message; infoCard.open() } }
    Connections {   // the still frames of the drag need the photos for the stacked thumbnails
        target: photos
        function onCountsChanged() { if (UiState.pdSim) { var i = UiState.pdDragInfo; UiState.pdDragInfo = { key: i.key, title: i.title, sub: i.sub, thumbs: sidePanel.thumbPaths([1, 6, 10]) } } }
    }
    SettingsWindow { id: settingsWindow; sourceItem: appContent }
    BatchWindow { id: batchWindow; sourceItem: appContent }
    HandoffWindow { id: handoffWindow; sourceItem: appContent }
    TransferCards { id: transferCards; sourceItem: appContent }
    FiltersWindow { id: filtersWindow; sourceItem: appContent }
    CollectionDialog { id: collectionDialog; sourceItem: appContent }
    CollectionPicker { id: collectionPicker; sourceItem: appContent }
    BestOfDialog { id: bestOfDialog; sourceItem: appContent }
    TagPeopleWindow { id: tagPeopleWindow; sourceItem: appContent }
    CullWindow { id: cullWindow; sourceItem: appContent }
    AiCenterWindow { id: aiCenterWindow; sourceItem: appContent }
    CullMini { }
    HelpMenu { menuX: top.helpX }
    Rectangle {   // the splash scenes of the mockup, drawn over the window (the real splash is SplashWindow.qml)
        z: 120; anchors.fill: parent; color: "#05060a"; visible: reference.view.splash.on && reference.view.splash.preview
        Item { anchors.centerIn: parent; width: 765; height: 451
            Shadow { anchors.fill: parent; radius: 6; offsetA: 40; insetA: 20; blurA: 120; alphaA: 0.9; offsetB: 0; insetB: 0; blurB: 0; alphaB: 0 }
            SplashCard { anchors.fill: parent; status: reference.view.splash.status; progress: reference.view.splash.p; version: reference.view.splash.version } }
    }
    HelpWindow { id: helpWindow; sourceItem: appContent }
    ShortcutsWindow { id: shortcutsWindow; sourceItem: appContent }
    LibraryWindow { id: libraryWindow; sourceItem: appContent }
    FileAssocWindow { id: fileAssocWindow; sourceItem: appContent }
    Viewer { id: viewer; onMoveRequested: (r) => win.chooseDestination("move", r) }
    ContextMenu { id: ctxHost }
    Toasts { }
    Connections {
        target: ctxMenu
        function onUiRequest(what, arg) {
            if (what === "share") sidePanel.shareToPhone("sel")
            else if (what === "showDrop") UiState.tab = "drop"
            else if (what === "open") popout.open(arg)
            else if (what === "batch") UiState.batchMode = arg
            else if (what === "newCollection") { UiState.collDlgId = ""; UiState.collDlg = "new" }
            else if (what === "review") cull.reviewFrame(arg)
            else if (what === "clearFilters") { UiState.filter = "all"; UiState.minRate = 0; UiState.fmt = "all"; photos.clearFilters(); advFilters.clearApplied() }
            else if (what === "view") { if (arg === "view:names") UiState.showNames = !UiState.showNames; else if (arg === "view:badges") UiState.showBadges = !UiState.showBadges; else UiState.dens = arg.slice(5) }
            else if (what === "sort") UiState.sort = arg
            else if (what === "chooseDest") win.chooseDestination(String(arg).split(":")[0], Number(String(arg).split(":")[1]))
        }
    }
    Connections {   // a button in a help article opens a tab or a window of the shell
        target: reference
        function onUiRequest(what) {
            if (what === "settings") UiState.settingsOpen = true
            else if (what === "cull") cull.openCull()
            else if (what === "coll" || what === "faces" || what === "drop") UiState.tab = what
        }
    }
    Connections {   // the popout was put away: the grid goes to the photo it ended on and takes the keyboard back
        target: popout
        function onClosed(row) { grid.view.currentIndex = row; win.requestActivate(); grid.view.forceActiveFocus() }
    }
    Connections { target: UiState; function onRequestRowChanged() { if (UiState.requestRow >= 0) { grid.view.currentIndex = UiState.requestRow; UiState.requestRow = -1 } } }
    CommandPalette { id: commandPalette; sourceItem: appContent }
    Shortcut { sequence: "Ctrl+,"; onActivated: UiState.settingsOpen = !UiState.settingsOpen }
    Shortcut { sequence: "F1"; onActivated: reference.toggleHelp() }
    Shortcut { sequence: "Ctrl+/"; onActivated: reference.toggleKeys() }
    Shortcut { sequence: "Ctrl+Alt+H"; onActivated: UiState.handoffOpen = !UiState.handoffOpen }
    Shortcut { sequence: "Ctrl+Shift+R"; onActivated: UiState.batchMode = "rename" }
    Shortcut { sequence: "Ctrl+Shift+E"; onActivated: UiState.batchMode = "resize" }
    Shortcut { sequence: "Ctrl+Shift+C"; onActivated: UiState.batchMode = "convert" }
    Shortcut { sequence: "Ctrl+Alt+B"; onActivated: UiState.bestOfOpen = true }
    Shortcut { sequence: "Ctrl+K"; onActivated: { if (UiState.paletteOpen) UiState.paletteOpen = false; else UiState.openPalette("main") } }
    Shortcut { sequence: "Ctrl+O"; onActivated: UiState.openFolderRequested() }

    // Open a folder: the rail's Open folder pin, Ctrl+O and the palette ask; the Library page's drives, favorites and tree open folders themselves
    FolderDialog {
        id: folderDialog
        title: "Choose a folder"
        currentFolder: libraryNav.folderUrl
        onAccepted: libraryNav.openUrl(selectedFolder.toString())
    }
    Connections { target: UiState; function onOpenFolderRequested() { folderDialog.open() } }
    Connections { target: photos; function onFolderChanged() { grid.view.currentIndex = 0 } }

    // Session memory: the photo the person is on is saved a moment after it changes and when the window closes; opening the folder again puts the grid
    // back on it (library_nav.py, the Widgets' own keys).
    function savePositionNow() { var d = photos.get(grid.view.currentIndex); if (d && d.path) libraryNav.savePosition(d.path) }
    Timer { id: positionTimer; interval: 700; onTriggered: win.savePositionNow() }
    Connections { target: grid.view; function onCurrentIndexChanged() { positionTimer.restart() } }
    Connections { target: libraryNav; function onPositionRestored(row) { grid.view.currentIndex = row; grid.view.positionViewAtIndex(row, GridView.Center) } }
    onClosing: win.savePositionNow()

    // The commands the shell can carry out today, by the palette's command ids (the top bar's menus use the same ones). The palette lists every
    // other command too and then does nothing; the menus say they are not connected yet (docs/qml_unwired.md).
    function runCommand(id) {
        if (id === "file.workflow_settings") UiState.settingsOpen = true
        else if (id.indexOf("appearance.") === 0) { settings.set("theme", id.slice(11)); settings.save() }
        else if (id.indexOf("sort.") === 0) { UiState.sort = id.slice(5); photos.setSort(UiState.sort) }
        else if (id === "workflow.handoff_builder") UiState.handoffOpen = true
        else if (id === "workflow.best_of") UiState.bestOfOpen = true
        else if (id === "library.create_collection") { UiState.collDlgId = ""; UiState.collDlg = "new" }
        else if (id === "library.add_selected_to_collection") grid.addToCollection()
        else if (id === "ai.people") UiState.peopleOpen = true
        else if (id === "tools.batch_rename" || id === "edit.rename_selection") UiState.batchMode = "rename"
        else if (id === "tools.batch_resize") UiState.batchMode = "resize"
        else if (id === "tools.batch_convert") UiState.batchMode = "convert"
        else if (id === "search.advanced_filters" || id === "search.save_current") UiState.filtersOpen = true
        else if (id === "search.clear_filters") { UiState.filter = "all"; UiState.minRate = 0; UiState.fmt = "all"; photos.clearFilters(); advFilters.clearApplied() }
        else if (id === "file.open_folder") UiState.openFolderRequested()
        else if (id === "review.compare_mode") UiState.compare = !UiState.compare
        else if (id === "review.open_preview") popout.open(grid.view.currentIndex)
        else if (id === "window.reset_layout") { UiState.tab = "lib"; UiState.insp = true }
        else if (id === "review.accept_selection") photos.setDecision(grid.view.currentIndex, "win")
        else if (id === "review.reject_selection") photos.setDecision(grid.view.currentIndex, "rej")
        else if (id === "review.keep_selection") photos.setDecision(grid.view.currentIndex, "keep")
        else if (id === "file.refresh_folder") { if (libraryNav.openPath !== "") libraryNav.openFolder(libraryNav.openPath) }
        else if (id === "file.reveal_folder") libraryNav.reveal(libraryNav.openPath)
        else if (id === "file.browse_library") reference.open("lib")
        else if (id === "review.delete_selection") fileActions.deleteRow(grid.view.currentIndex)
        else if (id === "review.move_selection") chooseDestination("move", grid.view.currentIndex)
        else if (id === "edit.undo") { var what = photos.undo(); toasts.show(what !== "" ? "success" : "info", what !== "" ? "Undid the " + what : "Nothing to undo", "") }
        else if (id === "file.exit") win.close()
        else if (id === "workflow.share_to_phone") { pocketDrop.refreshLibrary(grid.view.currentIndex); pocketDrop.addLibrary("sel"); UiState.tab = "drop" }
        else if (id === "review.clear_decision") photos.setDecision(grid.view.currentIndex, "")
        else if (id.indexOf("review.rate.") === 0) photos.setRating(grid.view.currentIndex, Number(id.slice(12)))
        else if (id === "review.reveal") libraryNav.revealFile(photos.get(grid.view.currentIndex).path || "")
        else if (id === "view.names") UiState.showNames = !UiState.showNames
        else if (id === "view.badges") UiState.showBadges = !UiState.showBadges
        else if (id === "view.inspector") UiState.insp = !UiState.insp
        else if (id === "view.panel") UiState.tab = UiState.tab === "" ? "lib" : ""
        else if (id === "view.hidden") { settings.set("hidden", settings.value("hidden") !== true); settings.save(); libraryNav.refresh() }
        else if (id.indexOf("view.dens.") === 0) UiState.dens = id.slice(10)
        else if (id.indexOf("view.filter.") === 0) { UiState.filter = id.slice(12); photos.setFilter(UiState.filter) }
        else if (id.indexOf("view.fmt.") === 0) { UiState.fmt = id.slice(9); photos.setFormat(UiState.fmt) }
        else if (id === "ai.analyze") cull.openCull()
        else if (id === "ai.center") cull.openCenter()
        else if (id.indexOf("recent:") === 0) libraryNav.openFolder(id.slice(7))
        else if (id === "help.keyboard_help") reference.toggleHelp()
        else if (id === "help.about") reference.openAbout(false)
        else if (id === "help.check_updates") reference.openAbout(true)
        else if (id === "workflow.keyboard_shortcuts") reference.open("keys")
        else if (id === "review.reveal_in_explorer") libraryNav.revealFile(photos.get(grid.view.currentIndex).path || "")
        else if (id === "library.browse_catalog") reference.open("lib")
        else if (id === "ai.workflow_center") cull.openCenter()
        else if (id === "ai.run_pipeline") cull.openCull()
        else if (id === "ai.apply_culling") cull.openReview()
        else if (id === "ai.next_top_pick") { var tp = aiScores.nextTopPick(grid.view.currentIndex); if (tp >= 0) { grid.view.currentIndex = tp; grid.view.positionViewAtIndex(tp, GridView.Contain) } else toasts.show("info", "No AI top picks in this view", "Run the AI analysis first, or clear the filters.") }
        else if (id === "review.auto_advance") { settings.set("autoAdvance", settings.value("autoAdvance") !== true); settings.save(); cmdPalette.setState("review.auto_advance", settings.value("autoAdvance") === true ? "On" : "Off") }
        else if (id === "tools.performance_logging") { settings.set("perfLog", settings.value("perfLog") !== true); settings.save(); cmdPalette.setState("tools.performance_logging", settings.value("perfLog") === true ? "On" : "Off") }
        else if (id === "view.zen_mode") { UiState.zen = !UiState.zen; cmdPalette.setState("view.zen_mode", UiState.zen ? "On" : "Off") }
    }
    Connections { target: cmdPalette; function onExecuted(id) { win.runCommand(id) } }
    TopMenus { id: topMenus; onRun: (id) => win.runCommand(id) }
    Shortcut { sequence: "F5"; onActivated: win.runCommand("file.refresh_folder") }
    Shortcut { sequence: "Ctrl+Z"; enabled: !popout.isOpen; onActivated: win.runCommand("edit.undo") }
    Shortcut { sequence: "Delete"; enabled: !popout.isOpen; onActivated: win.runCommand("review.delete_selection") }
    Connections { target: UiState; function onUndoRequested() { win.runCommand("edit.undo") } }
    Connections { target: UiState; function onZenChanged() { if (UiState.zen) { UiState.zenTab = UiState.tab; UiState.zenInsp = UiState.insp; UiState.tab = ""; UiState.insp = false } else { UiState.tab = UiState.zenTab; UiState.insp = UiState.zenInsp } } }
    Connections { target: UiState; function onCompareChanged() { cmdPalette.setState("review.compare_mode", UiState.compare ? "On" : "Off") } }
    Component.onCompleted: {
        cmdPalette.setState("review.compare_mode", UiState.compare ? "On" : "Off")
        cmdPalette.setState("review.auto_advance", settings.value("autoAdvance") === true ? "On" : "Off")
        cmdPalette.setState("view.zen_mode", "Off")
        cmdPalette.setState("tools.performance_logging", settings.value("perfLog") === true ? "On" : "Off")
        UiState.panelW = settings.layoutWidth("panel", 300); UiState.inspW = settings.layoutWidth("inspector", 352)
    }

    // Called once the window has its real size (the mockup's componentDidMount: both side sheets start closed when narrow), then the
    // test hook `--ui key=value;...` applies UiState settings so layouts can be checked headlessly.
    property string pendingScope: ""
    property string pendingCull: ""
    property string pendingCtx: ""
    // the hook `scope=winners|c:<id>|p:<id>` waits for the photos to be loaded (opening a folder resets the view's scope)
    function applyScope() {
        if (pendingCull !== "" && photos.total > 0) { var cs = pendingCull; pendingCull = ""; cull.scene(cs) }
        if (pendingCtx !== "" && photos.total > 0) { var cx = pendingCtx; pendingCtx = ""; var r = ctxMenu.scene(cx); if (r.kind) ctxHost.show(r.kind, r.row, r.x, r.y, r.sub) }
        if (pendingScope === "" || photos.total === 0) return
        var sc = pendingScope; pendingScope = ""
        if (sc === "winners") collections.openWinners()
        else if (sc.indexOf("c:") === 0) collections.open(sc.slice(2))
        else if (sc.indexOf("p:") === 0) people.openPerson(sc.slice(2))
    }
    Connections { target: photos; function onCountsChanged() { win.applyScope() } }
    function applyStartup(w) {
        if (w <= 900) { UiState.tab = ""; UiState.insp = false }
        var sample = ({})
        var handoffRecipe = ""
        var scope = ""
        var cullScene = ""
        var ctxScene = ""
        var refScene = ""
        if (typeof startup === "string" && startup.length) {
            var parts = startup.split(";");
            for (var i = 0; i < parts.length; ++i) {
                var kv = parts[i].split("=");
                if (kv.length !== 2) continue;
                var v = kv[1] === "true" ? true : kv[1] === "false" ? false : (kv[1] === "" || isNaN(Number(kv[1])) ? kv[1] : Number(kv[1]));
                if (kv[0] === "inspTab") insp.tab = v; else if (kv[0] === "sel") grid.view.currentIndex = v
                else if (["pdMode", "pdState", "pdActivity", "pdList"].indexOf(kv[0]) >= 0) sample[kv[0]] = String(v)
                else if (kv[0] === "hoRecipe") handoffRecipe = String(kv[1])
                else if (kv[0] === "scope") scope = String(kv[1])
                else if (kv[0] === "cullScene") cullScene = String(kv[1])
                else if (kv[0] === "ctxScene") ctxScene = String(kv[1])
                else if (kv[0] === "refScene") refScene = String(kv[1])
                else if (kv[0] === "filter") { UiState.filter = String(v); photos.setFilter(String(v)) }
                else UiState[kv[0]] = v;
            }
            if (handoffRecipe !== "") handoff.setRecipe(handoffRecipe)
            pendingScope = scope
            pendingCull = cullScene
            pendingCtx = ctxScene
            if (refScene !== "") reference.scene(refScene)
            win.applyScope()
            if (UiState.txState !== "") transfers.show(UiState.txState, UiState.txKind, UiState.txCount, UiState.txP)
            if (Object.keys(sample).length) pocketDrop.setSample(sample.pdMode || "", sample.pdState || "", sample.pdActivity || "", sample.pdList || "mixed")
            if (UiState.pdSim) {
                var i = UiState.pdDragInfo
                UiState.pdDragInfo = { key: i.key, title: i.title, sub: i.sub, thumbs: sidePanel.thumbPaths([1, 6, 10]) }
            }
        }
    }
}
