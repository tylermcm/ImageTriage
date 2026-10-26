import QtQuick
import QtQuick.Shapes

// The histogram of a photo: its brightness (on the preview), or its red, green and blue (`three`, on the Quality tab). The counts come from
// histograms.py, the Widgets app's own counting, for the photo at `path`; until they arrive (or if the photo cannot be read) nothing is drawn.
Item {
    id: h
    property string path: ""
    property bool three: false
    property color luma: Qt.rgba(1, 1, 1, 0.42)
    property color red: Qt.rgba(235 / 255, 100 / 255, 100 / 255, 0.42)
    property color green: Qt.rgba(80 / 255, 200 / 255, 140 / 255, 0.42)
    property color blue: Qt.rgba(90 / 255, 150 / 255, 1.0, 0.5)
    property var counts: ({ ready: false })
    readonly property bool has: counts.ready === true
    readonly property string summary: has ? counts.summary : ""

    function refresh() {
        if (path === "" || !visible) { counts = ({ ready: false }); return }
        counts = histograms.get(path)
        if (!counts.ready) histograms.request(path)
    }
    onPathChanged: refresh()
    onVisibleChanged: if (visible) refresh()
    Component.onCompleted: refresh()
    Connections { target: histograms; function onReady(p) { if (p === h.path) h.refresh() } }

    // the 64 counts as a closed outline, as SVG path text, in a 100 x 40 box (the baseline at 40, a full bar 2 units from the top)
    function outline(bins) {
        var n = bins ? bins.length : 0
        if (n === 0) return ""
        var d = "M0 40"
        for (var i = 0; i < n; ++i) d += "L" + ((i + 0.5) * 100 / n).toFixed(2) + " " + (40 - bins[i] * 38).toFixed(2)
        return d + "L100 40z"
    }

    Shape {
        visible: h.has
        width: 100; height: 40; preferredRendererType: Shape.CurveRenderer
        transform: Scale { xScale: h.width / 100; yScale: h.height / 40 }
        ShapePath { strokeColor: "transparent"; fillColor: h.three ? h.red : h.luma; PathSvg { path: h.outline(h.three ? h.counts.r : h.counts.luma) } }
        ShapePath { strokeColor: "transparent"; fillColor: h.three ? h.green : "transparent"; PathSvg { path: h.three ? h.outline(h.counts.g) : "" } }
        ShapePath { strokeColor: "transparent"; fillColor: h.three ? h.blue : "transparent"; PathSvg { path: h.three ? h.outline(h.counts.b) : "" } }
    }
    Accessible.role: Accessible.Graphic
    Accessible.name: "Histogram of the selected photo"
    Accessible.description: h.summary
}
