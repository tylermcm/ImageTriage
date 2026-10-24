import QtQuick
import QtQuick.Window

// Resize grips of the frameless main window: thin strips along the four edges and the four corners. The drag is handed to the system
// (`startSystemResize`), so Windows draws its own resize and snapping. Off while the window is maximised or full screen.
Item {
    id: edges
    readonly property var win: Window.window
    readonly property bool resizable: win !== null && win.visibility === Window.Windowed
    readonly property real thickness: 5
    readonly property real corner: 10

    component Grip: Item {
        id: grip
        property int edge: 0
        property int cursor: Qt.ArrowCursor
        HoverHandler { enabled: edges.resizable; cursorShape: grip.cursor }
        DragHandler { target: null; enabled: edges.resizable; onActiveChanged: if (active) edges.win.startSystemResize(grip.edge) }
    }

    Grip { edge: Qt.LeftEdge;   cursor: Qt.SizeHorCursor; x: 0; y: edges.corner; width: edges.thickness; height: edges.height - 2 * edges.corner }
    Grip { edge: Qt.RightEdge;  cursor: Qt.SizeHorCursor; x: edges.width - edges.thickness; y: edges.corner; width: edges.thickness; height: edges.height - 2 * edges.corner }
    Grip { edge: Qt.TopEdge;    cursor: Qt.SizeVerCursor; x: edges.corner; y: 0; width: edges.width - 2 * edges.corner; height: edges.thickness }
    Grip { edge: Qt.BottomEdge; cursor: Qt.SizeVerCursor; x: edges.corner; y: edges.height - edges.thickness; width: edges.width - 2 * edges.corner; height: edges.thickness }
    Grip { edge: Qt.LeftEdge | Qt.TopEdge;     cursor: Qt.SizeFDiagCursor; x: 0; y: 0; width: edges.corner; height: edges.corner }
    Grip { edge: Qt.RightEdge | Qt.BottomEdge; cursor: Qt.SizeFDiagCursor; x: edges.width - edges.corner; y: edges.height - edges.corner; width: edges.corner; height: edges.corner }
    Grip { edge: Qt.RightEdge | Qt.TopEdge;    cursor: Qt.SizeBDiagCursor; x: edges.width - edges.corner; y: 0; width: edges.corner; height: edges.corner }
    Grip { edge: Qt.LeftEdge | Qt.BottomEdge;  cursor: Qt.SizeBDiagCursor; x: 0; y: edges.height - edges.corner; width: edges.corner; height: edges.corner }
}
