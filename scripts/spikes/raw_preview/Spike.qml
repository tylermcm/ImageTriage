import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 1440; height: 900; visible: true
    color: "#0c0e10"
    title: "RAW preview spike"
    function updatePreview() { preview.adjust(exposure.value, shadows.value, mode.currentIndex === 1 ? "cpu" : "auto") }
    component EditSlider: Slider {
        id: control
        padding: 0
        Layout.preferredHeight: 24
        background: Rectangle {
            x: control.leftPadding; y: control.height/2-1.5
            width: control.availableWidth; height: 3; radius: 1.5; color: "#59646f"
        }
        handle: Rectangle {
            x: control.leftPadding + control.visualPosition * (control.availableWidth-width)
            y: control.height/2-height/2
            width: 10; height: 10; radius: 5; color: "#60a5fa"
        }
    }
    header: Rectangle {
        height: 52; color: "#111416"
        Text { anchors.left: parent.left; anchors.leftMargin: 20; anchors.verticalCenter: parent.verticalCenter
            text: "Edit RAW  /  Preview experiment"; color: "#e0e5eb"; font.pixelSize: 15 }
        Text { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter
            text: "Working preview · source files are read only"; color: "#8b959f"; font.pixelSize: 12 }
    }
    Image {
        id: photo
        anchors { left: parent.left; top: parent.top; bottom: footer.top; right: dock.left; margins: 12 }
        source: preview.source; cache: false; asynchronous: false
        fillMode: Image.PreserveAspectFit
        onStatusChanged: if (status === Image.Ready) preview.imageReady(Number(source.toString().split("/").pop()))
    }
    Rectangle {
        id: dock; width: 320
        anchors { top: parent.top; bottom: parent.bottom; right: parent.right }
        color: "#141719"; border.color: "#292e33"
        ColumnLayout {
            anchors { fill: parent; margins: 18 }
            spacing: 16
            Text { text: "Adjustments"; color: "#e0e5eb"; font.pixelSize: 15 }
            Canvas {
                id: histogram
                Layout.fillWidth: true; Layout.preferredHeight: 110
                Connections { target: preview; function onChanged() { histogram.requestPaint() } }
                onPaint: {
                    let c = getContext("2d"); c.clearRect(0,0,width,height)
                    c.fillStyle="#101214"; c.fillRect(0,0,width,height)
                    let channels=preview.histogram
                    if (channels.length !== 3) return
                    let peak=1
                    for(let channel of channels) for(let i=1;i<255;i++) peak=Math.max(peak,channel[i])
                    let colors=["#b65c61","#5aa77e","#5b92d1"]
                    for(let k=0;k<3;k++) {
                        c.beginPath(); c.moveTo(0,height)
                        for(let i=0;i<256;i++) c.lineTo(i*width/255,height-Math.min(1,channels[k][i]/peak)*height)
                        c.lineTo(width,height); c.closePath()
                        c.globalAlpha=.4; c.fillStyle=colors[k]; c.fill()
                        c.globalAlpha=1; c.strokeStyle=colors[k]; c.stroke()
                    }
                }
            }
            Text { text: "LIGHT"; color: "#8b959f"; font.pixelSize: 11 }
            RowLayout {
                Layout.fillWidth: true
                Text { text: "Exposure"; color: "#bfc7d0" }
                Item { Layout.fillWidth: true }
                Text { text: exposure.value.toFixed(2); color: "#bfc7d0" }
            }
            EditSlider {
                id: exposure; objectName: "exposureSlider"; Layout.fillWidth: true
                enabled: preview.ready; from: -5; to: 5; value: 0; stepSize: .05
                onMoved: window.updatePreview()
            }
            RowLayout {
                Layout.fillWidth: true
                Text { text: "Shadows"; color: "#bfc7d0" }
                Item { Layout.fillWidth: true }
                Text { text: Math.round(shadows.value); color: "#bfc7d0" }
            }
            EditSlider {
                id: shadows; objectName: "shadowsSlider"; Layout.fillWidth: true
                enabled: preview.ready; from: -100; to: 100; value: 0; stepSize: 1
                onMoved: window.updatePreview()
            }
            Text { text: "PROCESSING"; color: "#8b959f"; font.pixelSize: 11 }
            ComboBox {
                id: mode; objectName: "processingMode"; Layout.fillWidth: true; model: ["Auto · GPU when supported", "CPU"]
                enabled: preview.ready; onActivated: window.updatePreview()
            }
            Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#8b959f"; font.pixelSize: 12
                text: "The developed preview stays in the backend between edits. Shadows uses PhotoCraft’s CPU raw-tone stage; supported remaining stages may use the GPU." }
            Item { Layout.fillHeight: true }
            Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#8b959f"; font.pixelSize: 12
                text: "Spike scope: working preview only. Full-resolution detail, Save, and grid updates are later acceptance gates." }
        }
    }
    Rectangle {
        id: footer; height: 40; color: "#111416"
        anchors { left: parent.left; right: dock.left; bottom: parent.bottom }
        Text { anchors { left: parent.left; leftMargin: 16; verticalCenter: parent.verticalCenter }
            text: preview.status; color: "#a7b7c9"; font.pixelSize: 12 }
    }
}
