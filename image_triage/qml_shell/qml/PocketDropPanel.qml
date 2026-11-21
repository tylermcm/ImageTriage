import QtQuick

// The PocketDrop page of the side panel: PocketDrop's own screen (PocketDropView.qml, shared with the standalone app and vendored from
// the PocketDrop repository by scripts/sync_pocketdrop.py), with what is particular to Image Triage added: the Library button and
// where phone uploads are saved. Everything it shows comes from `pocketDrop` (pocketdrop_model.py).
Item {
    id: page
    readonly property bool on: UiState.tab === "drop"
    opacity: on ? 1 : 0
    visible: opacity > 0.01
    transform: Translate { y: page.on ? 0 : 8; Behavior on y { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } } }
    Behavior on opacity { NumberAnimation { duration: 250 } }
    // Opening a port is not something to do at launch: the engine starts the first time this page is shown.
    onOnChanged: { if (on) { pocketDrop.start(); pocketDrop.refreshLibrary(UiState.selRow) } }
    Component.onCompleted: { if (on) pocketDrop.start() }

    function chooseFiles() { view.chooseFiles() }
    function chooseFolder() { view.chooseFolder() }
    function chooseInbox() { view.chooseInbox() }

    PocketDropView {
        id: view
        anchors.fill: parent
        showTitle: false            // the side panel has its own header
        handleRisk: false           // Main.qml asks before Anywhere
        autoStart: false
        externalDrag: UiState.pdDragging || photoDrop.containsDrag
        thumb: Component { ThumbImage { bucket: 160 } }
        extraButtons: PdButton {
            id: libBtn
            height: 38; width: 96; label: "Library"; iconPath: "M12 4l8.5 4.5L12 13 3.5 8.5zM3.5 12.5L12 17l8.5-4.5"; lifted: library.opened
            onClicked: UiState.setMenu("pdlib")
            PocketDropLibrary { id: library; panel: page; panelW: page.width }
        }
        menuExtras: PdMenuEntry {
            popup: view.menuPopup
            label: "Save phone uploads in the open folder"; tick: true; stay: true; ticked: pocketDrop.receiveIntoFolder
            onActivated: pocketDrop.setReceiveIntoFolder(!pocketDrop.receiveIntoFolder)
        }
    }
    DropArea {   // photos dragged from the grid (PhotoGrid's drag, with this page open): added to PocketDrop to share, all in one step
        id: photoDrop
        objectName: "pocketDropPhotoDrop"
        anchors.fill: parent
        enabled: page.on
        keys: ["image-triage/photos"]
        onDropped: (drop) => {
            var rows = drop.source && drop.source.dragRows !== undefined ? drop.source.dragRows : [UiState.selRow]
            var paths = []
            for (var i = 0; i < rows.length; ++i) { var p = photos.get(rows[i]).path; if (p) paths.push(p) }
            drop.accept()
            if (paths.length) pocketDrop.addUrls(paths)
        }
    }
}
