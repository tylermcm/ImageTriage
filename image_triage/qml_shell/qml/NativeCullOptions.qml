import QtQuick
import QtQuick.Controls

Column {
    id: root
    objectName: "nativeCullOptions"
    readonly property var v: cull.view
    property bool advanced: false
    property bool customizing: false
    spacing: 8
    Connections { target: cull; function onChanged() {
        if (root.v.window !== "cull" || root.v.page !== "prep") { root.customizing = false; root.advanced = false }
    } }
    Text { text: "WHAT WILL RUN"; color: theme.fg3; font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.8 }
    Repeater { model: root.v.nativeFeatures || []
        delegate: Rectangle {
            id: feature
            required property var modelData
            width: root.width; height: Math.max(description.implicitHeight + 50, 80); radius: 14; color: theme.s1; border.color: theme.lineSoft
            Rectangle { x: 14; y: 14; width: 36; height: 36; radius: 11; color: Qt.tint(theme.s2, Qt.alpha(theme.accent, 0.14)); border.color: theme.lineSoft
                Icon { anchors.centerIn: parent; name: feature.modelData.icon; path: feature.modelData.key === "cleanup" ? "M12 4l9 5-9 5-9-5zM3 14l9 5 9-5" : ""; size: 19; color: theme.accentFg }
            }
            Text { x: 64; y: 14; text: feature.modelData.title; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
            Text { id: description; x: 64; y: 35; width: parent.width - 150; text: feature.modelData.description; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12; lineHeight: 1.4 }
            Row { visible: feature.modelData.fixed; anchors.right: parent.right; anchors.rightMargin: 14; y: 24; spacing: 7
                Rectangle { width: 8; height: 8; radius: 4; color: theme.win; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "Ready"; color: theme.fg2; font.pixelSize: 12 }
            }
            SSwitch { visible: !feature.modelData.fixed; anchors.right: parent.right; anchors.rightMargin: 14; y: 22; checked: feature.modelData.on; onToggled: cull.toggleNativeFeature(feature.modelData.key) }
        }
    }
    Item { width: parent.width; height: 18
        Text { text: "HOW STRICT"; color: theme.fg3; font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.8 }
        Text { objectName: "nativeFineTune"; anchors.right: parent.right; text: root.customizing ? "Hide fine-tuning" : "Fine-tune"; color: theme.fg3; font.pixelSize: 12
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: { if (!root.customizing) root.customizing = true; else if (cull.saveNativeDraft()) { root.customizing = false; root.advanced = false } } }
        }
    }
    Seg { width: parent.width; height: 38; fill: true; showExtras: false; labels: ["Careful", "Balanced", "Strict"]; current: root.v.nativePreset === undefined ? -1 : root.v.nativePreset
        onActivated: (i) => cull.setNativePreset(["careful", "balanced", "strict"][i])
    }
    Text { width: parent.width; text: root.v.nativePreset < 0 ? "Custom sensitivity. Your saved thresholds are shown in Fine-tune." : "Adjusts clipping, relative blur and closed-eye thresholds. Quality-floor levels and keeper counts stay as configured."; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12 }
    Text { visible: !!root.v.nativeError; width: parent.width; text: root.v.nativeError || ""; color: theme.rej; wrapMode: Text.WordWrap; font.pixelSize: 12 }
    Column { visible: root.customizing; width: root.width; spacing: 14
    Row { spacing: 12
        Text { text: "Run on"; color: theme.fg2; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
        SSelect { width: 220; options: [{label: "Runtime default", value: "auto"}, {label: "CPU", value: "cpu"}, {label: "CUDA GPU", value: "cuda"}]; current: root.v.nativeDevice || "auto"
            onPicked: (value) => cull.setNativeDevice(value) }
    }
    Repeater { model: root.v.nativeGroups || []
        delegate: Rectangle {
            id: section
            required property var modelData
            width: root.width; height: fields.implicitHeight + 28; radius: 14; color: theme.s1; border.color: theme.lineSoft
            Column { id: fields; x: 14; y: 14; width: parent.width - 28; spacing: 12
                Text { text: section.modelData.title; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold }
                Repeater { model: section.modelData.fields
                    delegate: Item {
                        id: field
                        required property var modelData
                        property real liveValue: typeof modelData.value === "number" ? modelData.value : 0
                        width: fields.width; height: Math.max(label.implicitHeight, 30)
                        Text { id: label; width: parent.width - Math.min(300, parent.width * 0.5) - 14; anchors.verticalCenter: parent.verticalCenter; text: field.modelData.label; wrapMode: Text.WordWrap; color: theme.fg2; font.pixelSize: 12 }
                        SSwitch { visible: field.modelData.kind === "toggle"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; checked: !!field.modelData.value; onToggled: cull.setNativeValue(field.modelData.key, !field.modelData.value) }
                        SSlider { objectName: "nativeControl_" + field.modelData.key; visible: field.modelData.kind === "slider"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            trackW: Math.min(220, field.width * 0.5 - 66); from: field.modelData.min; to: field.modelData.max; step: field.modelData.step; value: field.liveValue
                            text: field.modelData.step >= 1 ? String(Math.round(field.liveValue)) : field.liveValue.toFixed(2)
                            onMoved: (x) => { field.liveValue = x }
                            onCommitted: (x) => cull.setNativeValue(field.modelData.key, x)
                        }
                        TextField { visible: field.modelData.kind === "number"; anchors.right: parent.right; width: 110; height: 30; color: theme.fg; text: String(field.modelData.value); selectByMouse: true
                            background: Rectangle { radius: 7; color: theme.s2; border.color: theme.lineSoft }
                            onEditingFinished: if (visible) cull.setNativeValue(field.modelData.key, Number(text))
                        }
                        SSelect { visible: field.modelData.kind === "choice"; anchors.right: parent.right; width: Math.min(220, field.width * 0.5)
                            options: field.modelData.choices.map(x => ({label: x.replace(/_/g, " "), value: x})); current: field.modelData.value
                            onPicked: (value) => cull.setNativeValue(field.modelData.key, value)
                        }
                    }
                }
            }
        }
    }
    Text { width: parent.width; text: "Zero quality-floor levels leave the floor off. Quality levels require the corresponding model. Advanced preserves every native setting, including baselines and scoring weights."; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12 }
    Text { visible: !!root.v.nativeError; width: parent.width; text: root.v.nativeError || ""; wrapMode: Text.WordWrap; color: theme.rej; font.pixelSize: 12 }
    Row { spacing: 8
        SButton { text: root.v.nativeDirty ? "Save changes" : "Settings saved"; h: 32; hpad: 14; onClicked: cull.saveNativeDraft() }
        SButton { text: root.advanced ? "Hide Advanced" : "Advanced…"; h: 32; hpad: 14; onClicked: root.advanced = !root.advanced }
    }
    Column { visible: root.advanced; width: parent.width; spacing: 8
        ScrollView { width: parent.width; height: 240
            TextArea { id: jsonProfile; text: root.v.nativeSettings || "{}"; color: theme.fg; font.family: "Consolas"; font.pixelSize: 12; wrapMode: TextEdit.Wrap; selectByMouse: true
                background: Rectangle { color: theme.s2; radius: 8; border.color: theme.lineSoft }
            }
        }
        SButton { text: "Save Advanced settings"; h: 30; hpad: 12; onClicked: cull.saveNativeSettings(jsonProfile.text) }
    }
    }
}
