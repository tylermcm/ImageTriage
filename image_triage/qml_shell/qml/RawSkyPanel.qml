import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

// Creative > Sky Replacement: choose a sky picture; the photo's own sky is found once and swapped by the renderer.
Column {
    id: sky
    required property var model
    required property var raw
    readonly property var values: model.settings
    readonly property bool editable: raw.loaded && !raw.saving
    property bool advanced: false
    property bool picking: false
    spacing: 8
    enabled: editable
    Component.onCompleted: model.refresh()

    Item {
        visible: sky.model.active
        width: parent.width; height: visible ? 24 : 0
        Rectangle {
            objectName: "skyRemove"
            visible: sky.model.active
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: removeLabel.implicitWidth + 20; height: 24; radius: 7; color: removeHover.hovered ? theme.s3 : "transparent"; border.color: theme.line
            Text { id: removeLabel; anchors.centerIn: parent; text: "Remove"; color: theme.fg2; font.pixelSize: 11 }
            HoverHandler { id: removeHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: sky.model.remove() }
            Accessible.role: Accessible.Button; Accessible.name: "Remove Sky Replacement"
            Accessible.onPressAction: sky.model.remove()
        }
    }

    Text {
        objectName: "skyUnavailable"
        visible: !!sky.model.unavailable
        width: parent.width; wrapMode: Text.WordWrap
        text: sky.model.unavailable; color: theme.fg3; font.pixelSize: 11
    }

    Flow {
        objectName: "skyLibrary"
        width: parent.width; spacing: 6
        Repeater {
            model: sky.model.skies
            delegate: Rectangle {
                id: card
                required property var modelData
                objectName: "skyCard"
                width: (sky.width - 12) / 3; height: width * 0.66; radius: 7; clip: true
                color: theme.base; border.width: modelData.chosen ? 2 : 1; border.color: modelData.chosen ? theme.accent : theme.line
                Image { anchors.fill: parent; anchors.margins: 1; source: card.modelData.thumb; sourceSize.width: 240; asynchronous: true; fillMode: Image.PreserveAspectCrop; smooth: true }
                HoverHandler { id: cardHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: sky.model.choose(card.modelData.path) }
                TapHandler { acceptedButtons: Qt.RightButton; onTapped: sky.model.forget(card.modelData.path) }
                ToolTip.text: card.modelData.name + " (right-click to remove from the library)"; ToolTip.visible: cardHover.hovered; ToolTip.delay: 600
                Accessible.role: Accessible.Button; Accessible.name: "Use sky " + card.modelData.name
                Accessible.onPressAction: sky.model.choose(card.modelData.path)
            }
        }
        Rectangle {
            objectName: "skyImport"
            width: (sky.width - 12) / 3; height: width * 0.66; radius: 7; color: importHover.hovered ? theme.s3 : "transparent"
            border.color: theme.line
            Text { anchors.centerIn: parent; text: "+ Add sky"; color: theme.fg2; font.pixelSize: 12 }
            HoverHandler { id: importHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: sky.picking = true }
            Accessible.role: Accessible.Button; Accessible.name: "Add a sky picture"
            Accessible.onPressAction: sky.picking = true
        }
    }
    Loader {
        active: sky.picking
        sourceComponent: FileDialog {
            title: "Choose sky pictures"; fileMode: FileDialog.OpenFiles
            nameFilters: ["Pictures (*.jpg *.jpeg *.png *.webp *.tif *.tiff *.bmp)"]
            Component.onCompleted: open()
            onAccepted: { sky.model.importSkies(selectedFiles); sky.picking = false }
            onRejected: sky.picking = false
        }
    }

    Text {
        objectName: "skyStatus"
        visible: sky.model.busy || sky.model.waiting || !!sky.model.error
        width: parent.width; wrapMode: Text.WordWrap; font.pixelSize: 11
        color: sky.model.error ? theme.rej : theme.fg3
        text: sky.model.error || sky.model.message
    }
    Text {
        objectName: "skyHint"
        visible: !sky.model.active && !sky.model.busy && !sky.model.error && !sky.model.unavailable && sky.model.skies.length === 0
        width: parent.width; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 11
        text: "Add a picture of a sky to start. Image Triage finds the sky in your photo and swaps it."
    }

    RawColorSlider {
        objectName: "sky_amount"; editModel: sky.raw; width: parent.width; controlHeight: 32
        label: "Amount"; from: 0; to: 100; value: sky.values.amount
        onCommitted: (next) => sky.model.adjust("amount", next)
    }
    RawColorSlider {
        objectName: "sky_fadeEdge"; editModel: sky.raw; width: parent.width; controlHeight: 32
        label: "Fade Edge"; from: 0; to: 100; value: sky.values.fadeEdge
        onCommitted: (next) => sky.model.adjust("fadeEdge", next)
    }
    RawColorSlider {
        objectName: "sky_shiftEdge"; editModel: sky.raw; width: parent.width; controlHeight: 32
        label: "Shift Edge"; from: -100; to: 100; value: sky.values.shiftEdge
        onCommitted: (next) => sky.model.adjust("shiftEdge", next)
    }
    RawColorSlider {
        objectName: "sky_brightness"; editModel: sky.raw; width: parent.width; controlHeight: 32
        label: "Sky Brightness"; from: -100; to: 100; value: sky.values.brightness
        onCommitted: (next) => sky.model.adjust("brightness", next)
    }
    RawColorSlider {
        objectName: "sky_warmth"; editModel: sky.raw; width: parent.width; controlHeight: 32
        label: "Sky Warmth"; from: -100; to: 100; colors: ["#608fc3", "#a7a7a0", "#d3bd64"]; value: sky.values.warmth
        onCommitted: (next) => sky.model.adjust("warmth", next)
    }
    RawColorSlider {
        objectName: "sky_colorMatch"; editModel: sky.raw; width: parent.width; controlHeight: 32
        label: "Match Colors"; from: 0; to: 100; value: sky.values.colorMatch
        onCommitted: (next) => sky.model.adjust("colorMatch", next)
    }
    RawColorSlider {
        objectName: "sky_relight"; editModel: sky.raw; width: parent.width; controlHeight: 32
        label: "Relight Scene"; from: 0; to: 100; value: sky.values.relight
        onCommitted: (next) => sky.model.adjust("relight", next)
    }

    Item {
        objectName: "skyAdvanced"
        width: parent.width; height: 30; activeFocusOnTab: true
        Text { anchors.verticalCenter: parent.verticalCenter; text: "Advanced Settings"; color: theme.fg2; font.pixelSize: 12 }
        Icon { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; size: 12; name: sky.advanced ? "chevUp" : "chevDown"; color: theme.fg2 }
        TapHandler { onTapped: sky.advanced = !sky.advanced }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Keys.onReturnPressed: sky.advanced = !sky.advanced
        Keys.onSpacePressed: sky.advanced = !sky.advanced
        Accessible.role: Accessible.Button; Accessible.name: "Advanced Settings"
        Accessible.onPressAction: sky.advanced = !sky.advanced
    }
    Column {
        visible: sky.advanced
        width: parent.width; spacing: 4
        RawColorSlider {
            objectName: "sky_scale"; editModel: sky.raw; width: parent.width; controlHeight: 32
            label: "Sky Size"; from: 25; to: 400; value: sky.values.scale
            onCommitted: (next) => sky.model.adjust("scale", next)
        }
        RawColorSlider {
            objectName: "sky_offsetX"; editModel: sky.raw; width: parent.width; controlHeight: 32
            label: "Sky Horizontal"; from: -100; to: 100; value: sky.values.offsetX
            onCommitted: (next) => sky.model.adjust("offsetX", next)
        }
        RawColorSlider {
            objectName: "sky_offsetY"; editModel: sky.raw; width: parent.width; controlHeight: 32
            label: "Sky Vertical"; from: -100; to: 100; value: sky.values.offsetY
            onCommitted: (next) => sky.model.adjust("offsetY", next)
        }
        Row {
            spacing: 8
            CheckBox {
                objectName: "sky_flip"; text: "Flip sky"; checked: sky.values.flip
                onToggled: sky.model.setFlip(checked)
            }
        }
    }
}
