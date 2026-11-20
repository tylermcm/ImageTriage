import QtQuick
import QtQuick.Window

// The viewer (and the RAW editor in it) in a window of its own, as Camera Raw beside Photoshop: moved, resized and snapped like any
// window, while the library stays usable in the main one. Main.qml moves the one Viewer item in here while UiState.viewerDetached is
// on; its place and size are kept between sessions (uiMemory "viewerWindow").
Window {
    id: viewerWindow
    objectName: "viewerWindow"
    property Item viewer
    property string photoName: ""
    title: photoName ? photoName + " — Image Triage" : "Image Triage"
    flags: Qt.Window
    color: theme.base
    minimumWidth: 640; minimumHeight: 480
    width: 1100; height: 800
    visible: UiState.viewerDetached && viewer !== null && viewer.shown
    transientParent: null                            // its own window on the taskbar, free of the main one

    property bool placed: false
    function place() {
        if (placed) return
        placed = true
        var kept = uiMemory.get("viewerWindow", null)
        var screens = Qt.application.screens
        function onScreen(r) {
            for (var i = 0; i < screens.length; ++i) {
                var s = screens[i]
                if (r.x + 80 > s.virtualX && r.x < s.virtualX + s.width - 80 && r.y >= s.virtualY - 10 && r.y < s.virtualY + s.height - 80) return true
            }
            return false
        }
        if (kept && typeof kept.w === "number" && kept.w >= minimumWidth && onScreen(Qt.rect(kept.x, kept.y, kept.w, kept.h))) {
            x = kept.x; y = kept.y; width = kept.w; height = kept.h
            if (kept.max === true) Qt.callLater(showMaximized)
        } else {
            // first time: the left part of the screen, as Camera Raw is usually put beside Photoshop
            var s = Screen
            width = Math.round(s.desktopAvailableWidth * 0.55); height = Math.round(s.desktopAvailableHeight * 0.9)
            x = s.virtualX + 16; y = s.virtualY + Math.round(s.desktopAvailableHeight * 0.05)
        }
    }
    function remember() {
        if (!placed) return
        var maximized = visibility === Window.Maximized
        var kept = uiMemory.get("viewerWindow", null) || ({})
        uiMemory.set("viewerWindow", maximized ? { x: kept.x || x, y: kept.y || y, w: kept.w || width, h: kept.h || height, max: true }
                                               : { x: x, y: y, w: width, h: height, max: false })
    }
    onVisibleChanged: {
        if (visible) { place(); requestActivate(); if (viewer) Qt.callLater(viewer.refocus) }
        else remember()
    }
    onClosing: (close) => { remember(); if (viewer && viewer.shown) viewer.close() }
    Timer { id: keep; interval: 600; onTriggered: viewerWindow.remember() }
    onXChanged: if (visible) keep.restart()
    onYChanged: if (visible) keep.restart()
    onWidthChanged: if (visible) keep.restart()
    onHeightChanged: if (visible) keep.restart()
}
