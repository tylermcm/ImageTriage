import QtQuick
import QtQuick.Window

// The startup splash as its own frameless window, shown before the main window is built. Its state is `reference.view.splash`.
Window {
    id: win
    readonly property var sp: reference.view.splash
    readonly property real aspect: 1633 / 963
    readonly property real baseW: {
        var w = Math.min(1100, Math.max(560, Screen.width * 0.72))
        w = Math.min(w, Math.max(1, Screen.width - 48))
        return Math.min(w, Math.max(1, Screen.height - 48) * aspect)
    }
    width: Math.max(1, Math.round(baseW * 0.75)); height: Math.round(width / aspect)
    x: Screen.virtualX + (Screen.width - width) / 2; y: Screen.virtualY + (Screen.height - height) / 2
    visible: sp.on && !sp.preview
    flags: Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: "transparent"
    title: "Image Triage"
    SplashCard { anchors.fill: parent; status: win.sp.status; progress: win.sp.p; version: win.sp.version }
}
