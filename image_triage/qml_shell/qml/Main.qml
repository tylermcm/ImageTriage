import QtQuick
import QtQuick.Window

Window {
    id: win
    visible: true
    width: 1440; height: 900
    minimumWidth: 640; minimumHeight: 520
    title: "Image Triage"
    color: theme.base

    readonly property bool compact: width <= 900          // the mockup's @media (max-width: 900px)
    Binding { target: UiState; property: "compact"; value: win.compact }
    readonly property real panelTarget: UiState.tab !== "" ? 300 : 0
    readonly property real inspTarget: UiState.insp ? 352 : 0
    property real panelW: panelTarget
    property real inspW: inspTarget
    Behavior on panelW { NumberAnimation { duration: 600; easing.type: Easing.OutQuint } }
    Behavior on inspW { NumberAnimation { duration: 600; easing.type: Easing.OutQuint } }

    TopBar { id: top; width: parent.width }
    StatusBar { id: status; width: parent.width; anchors.bottom: parent.bottom }

    Item {
        id: shell
        y: top.height; width: parent.width; height: parent.height - top.height - status.height

        Rail { id: rail; z: 25; height: parent.height; row: grid.view.currentIndex }
        SidePanel {
            id: sidePanel
            // wide: a column that animates its width; compact: a 320px sheet that slides in over the grid
            readonly property real sheetW: Math.min(320, shell.width - rail.width)
            fullW: win.compact ? sheetW : 300
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
            clip: true

            Rectangle {
                id: head
                width: parent.width; height: 44; color: theme.base
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                Row {
                    anchors.verticalCenter: parent.verticalCenter; x: 14; spacing: 10
                    Text { text: photos.folderName || "Open a folder"; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: -0.14 }
                    Text { visible: !win.compact; text: photos.folderPath; color: theme.fg3; font.pixelSize: 13; width: Math.min(implicitWidth, Math.max(0, main.width - 330 - x)); elide: Text.ElideRight }
                }
                Row {
                    anchors.verticalCenter: parent.verticalCenter; anchors.right: parent.right; anchors.rightMargin: 14; spacing: 10
                    Rectangle {
                        height: 24; width: shown.implicitWidth + 31; radius: 12; color: theme.s2; border.color: theme.lineSoft
                        Row { anchors.centerIn: parent; spacing: 6
                            Rectangle { width: 7; height: 7; radius: 4; color: theme.win; anchors.verticalCenter: parent.verticalCenter }
                            Text { id: shown; text: photos.shown + " of " + photos.total + " shown"; color: theme.fg2; font.pointSize: UiState.pt(11.5) }
                        }
                    }
                    Rectangle {
                        height: 24; width: wins.implicitWidth + 20; radius: 12; color: theme.s2; border.color: theme.lineSoft
                        Text { id: wins; anchors.centerIn: parent; text: photos.winCount + " winners"; color: theme.fg; font.pointSize: UiState.pt(11.5) }
                    }
                }
            }
            PhotoGrid { id: grid; y: head.height; width: parent.width; height: parent.height - head.height; overlay: floatBar }
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
            fullW: win.compact ? sheetW : 352
            x: win.compact ? shell.width - (UiState.insp ? sheetW : -sheetW * 0.05) : shell.width - win.inspW
            Behavior on x { enabled: win.compact; NumberAnimation { duration: 550; easing.type: Easing.OutQuint } }
            width: win.compact ? sheetW : win.inspW; height: parent.height
            z: win.compact ? 30 : 0
            row: grid.view.currentIndex
            onStep: (d) => { grid.view.currentIndex = Math.max(0, Math.min(photos.shown - 1, grid.view.currentIndex + d)) }
        }
    }

    // Called once the window has its real size (the mockup's componentDidMount: both side sheets start closed when narrow), then the
    // test hook `--ui key=value;...` applies UiState settings so layouts can be checked headlessly.
    function applyStartup(w) {
        if (w <= 900) { UiState.tab = ""; UiState.insp = false }
        if (typeof startup === "string" && startup.length) {
            var parts = startup.split(";");
            for (var i = 0; i < parts.length; ++i) {
                var kv = parts[i].split("=");
                if (kv.length !== 2) continue;
                var v = kv[1] === "true" ? true : kv[1] === "false" ? false : (kv[1] === "" || isNaN(Number(kv[1])) ? kv[1] : Number(kv[1]));
                if (kv[0] === "inspTab") insp.tab = v; else if (kv[0] === "sel") grid.view.currentIndex = v; else UiState[kv[0]] = v;
            }
        }
    }
}
