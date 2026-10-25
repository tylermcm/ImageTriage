import QtQuick
import QtQuick.Controls
import QtQuick.Effects

// Batch rename, resize and convert: one window over the dimmed and blurred application (the same overlay as settings and filters). Layout,
// sizes and colours follow the HTML mockup; the plans are the Widgets app's own, built by `batch` (batch_model.py).
FocusScope {
    id: root
    objectName: "batchWindow"
    property Item sourceItem
    readonly property bool shown: UiState.batchMode !== ""
    anchors.fill: parent
    visible: opacity > 0.01
    opacity: shown ? 1 : 0
    z: 106
    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

    readonly property real winW: Math.min(1040, width - 96)
    readonly property real winH: Math.min(680, height - 68 - 64)
    readonly property int rev: batch.revision
    readonly property string mode: shown ? UiState.batchMode : batch.mode
    readonly property var rows: { void rev; return batch.rows() }
    readonly property var modes: [["rename", "Rename"], ["resize", "Resize"], ["convert", "Convert"]]
    readonly property var scopeParts: { void rev; return batch.scope(photos.folderName).split("|") }
    property string applied: ""

    function open(mode) { UiState.batchMode = mode }
    function close() { UiState.batchMode = ""; batch.end() }
    function apply() { batch.apply(); close() }
    onShownChanged: if (shown) { batch.begin(UiState.batchMode); forceActiveFocus() }
    Keys.onEscapePressed: root.close()

    ShaderEffectSource { id: snap; anchors.fill: parent; sourceItem: root.sourceItem; live: true; visible: false }
    MultiEffect { anchors.fill: parent; source: snap; blurEnabled: true; blur: 1.0; blurMax: 15 }
    Rectangle { anchors.fill: parent; color: theme.light ? Qt.rgba(20 / 255, 22 / 255, 28 / 255, 0.34) : Qt.rgba(6 / 255, 7 / 255, 9 / 255, 0.52) }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onClicked: root.close(); onWheel: (w) => { w.accepted = true } }

    // ---- small pieces ------------------------------------------------------------------------------------------------------
    component Gh: Text { property alias title: g.text; id: g; width: 1; height: 13; verticalAlignment: Text.AlignVCenter
                         color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
    component Lbl: Text { height: 16.8; verticalAlignment: Text.AlignVCenter; color: theme.fg2; font.pixelSize: 12 }
    component Note: Text { width: parent ? parent.width : 299; topPadding: 1.5; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12; lineHeight: 17.4; lineHeightMode: Text.FixedHeight }
    // one labelled text field (label 16.8, 6, box 34)
    component Field: Column {
        id: fld
        property string label; property string key; property string placeholder; property string suffix
        spacing: 6; width: 299
        Lbl { text: fld.label }
        FInput { width: parent.width; placeholder: fld.placeholder; suffix: fld.suffix; text: { void root.rev; return String(batch.get(fld.key)) }
                 onEdited: (t) => batch.set(fld.key, t) }
    }
    component Pick: Column {
        id: pk
        property string label; property string key
        spacing: 6; width: 299
        Lbl { text: pk.label }
        SSelect { width: parent.width; menuMax: 268; options: { void root.rev; return batch.options(pk.key) }
                  current: { void root.rev; return batch.get(pk.key) }
                  onPicked: (v) => batch.set(pk.key, v) }
    }
    component Num: Column {
        id: nm
        property string label; property string key; property int lo: 0; property int hi: 999999
        spacing: 6; width: (299 - 16) / 3
        Lbl { text: nm.label }
        SStepper { width: parent.width; text: { void root.rev; return String(batch.get(nm.key)) }
                   onDecrement: { batch.set(nm.key, Math.max(nm.lo, batch.get(nm.key) - 1)) }
                   onIncrement: { batch.set(nm.key, Math.min(nm.hi, batch.get(nm.key) + 1)) } }
    }
    // a switch row: label, optional description, border below; the group's top border is drawn by Rows
    component SwRow: Item {
        id: sr
        property string label; property string desc; property string key; property bool locked: false
        width: 299
        implicitHeight: Math.max(46, 8 + lt.height + (desc !== "" ? 2 + dt.height : 0) + 8 + 1)
        height: implicitHeight
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
        Column {
            x: 0; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 0.5; width: 247; spacing: 2
            Text { id: lt; width: parent.width; wrapMode: Text.WordWrap; text: sr.label; color: theme.fg; font.pixelSize: 13; lineHeight: 18.2; lineHeightMode: Text.FixedHeight }
            Text { id: dt; visible: sr.desc !== ""; width: parent.width; wrapMode: Text.WordWrap; text: sr.desc; color: theme.fg3; font.pointSize: UiState.pt(11.5)
                   lineHeight: 16.1; lineHeightMode: Text.FixedHeight }
        }
        SSwitch { x: parent.width - width; anchors.verticalCenter: parent.verticalCenter; checked: { void root.rev; return batch.get(sr.key) }
                  onToggled: (v) => { if (!sr.locked) batch.set(sr.key, v) } }
    }

    Item {
        id: win
        x: (root.width - width) / 2; y: 42 + (root.height - 68 - height) / 2
        width: root.winW; height: root.winH
        scale: root.shown ? 1 : 0.96
        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }
        MouseArea { anchors.fill: parent; onClicked: win.forceActiveFocus() }

        Shadow { anchors.fill: parent; radius: 14; offsetA: 48; insetA: 24; blurA: 64; multA: 3; alphaA: 0.85; offsetB: 12; insetB: 12; blurB: 24; alphaB: 0.5 }
        Rectangle { anchors.fill: parent; radius: 14; color: theme.base }
        Rectangle { anchors.fill: parent; radius: 14; color: "transparent"; border.color: theme.line; border.width: 1; z: 10 }

        RoundClip {
            anchors.fill: parent; radius: 14

            // ---- title bar ----------------------------------------------------------------------------------------------
            Rectangle {
                x: 0; y: 0; width: parent.width; height: 40; color: theme.s1
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                Icon { x: 14; anchors.verticalCenter: parent.verticalCenter; size: 16; color: theme.fg2; path: "M4 7h16M4 12h10M4 17h16" }
                Text { x: 40; anchors.verticalCenter: parent.verticalCenter; text: batch.title; color: theme.fg2; font.pixelSize: 13; font.weight: Font.Medium }
                Row {
                    anchors.right: parent.right; height: parent.height
                    Item { width: 44; height: 40; Icon { anchors.centerIn: parent; size: 12; color: theme.fg3; path: "M5 12h14" } }
                    Item {
                        width: 44; height: 40
                        Rectangle { anchors.fill: parent; color: theme.rej; opacity: xh.hovered ? 1 : 0 }
                        Icon { anchors.centerIn: parent; size: 12; color: xh.hovered ? "#ffffff" : theme.fg3; path: "M6 6l12 12M18 6L6 18" }
                        HoverHandler { id: xh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.close() }
                        Accessible.role: Accessible.Button; Accessible.name: "Close"
                    }
                }
            }

            // ---- options ------------------------------------------------------------------------------------------------
            Rectangle {
                id: nav; x: 0; y: 40; width: 340; height: parent.height - 98; color: theme.s2
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }
                Item {
                    id: modeBox; x: 12; y: 12; width: 315; height: 32
                    Seg { anchors.fill: parent; fill: true; showExtras: false; labels: ["Rename", "Resize", "Convert"]
                          current: { var i = 0; for (var k = 0; k < root.modes.length; ++k) if (root.modes[k][0] === root.mode) i = k; return i }
                          onActivated: (i) => { UiState.batchMode = root.modes[i][0]; batch.setMode(root.modes[i][0]) } }
                }
                Flickable {
                    id: opts
                    y: 44; width: parent.width - 1; height: parent.height - 44
                    contentWidth: width; contentHeight: optCol.y + optCol.height + 24
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { id: obar; policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: obar.active || obar.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 300 } } } background: Item {} }
                    Column {
                        id: optCol
                        x: 20; y: 24; width: 299

                        // ---- rename
                        Column {
                            visible: root.mode === "rename"; width: parent.width
                            Gh { title: "RENAME RULES" }
                            Item { width: 1; height: 12 }
                            Field { label: "New name"; key: "newName"; placeholder: "Optional base name (without extension)" }
                            Item { width: 1; height: 12 }
                            Field { label: "Prefix"; key: "prefix"; placeholder: "Optional prefix" }
                            Item { width: 1; height: 12 }
                            Field { label: "Suffix"; key: "suffix"; placeholder: "Optional suffix" }
                            Item { width: 1; height: 12 }
                            Pick { label: "Case"; key: "case" }
                            Item { width: 1; height: 12 }
                            Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                            SwRow { label: "Collapse repeated spaces"; key: "collapse" }
                            Item { width: 1; height: 22 }
                            Gh { title: "SEQUENCE" }
                            Item { width: 1; height: 6 }
                            Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                            SwRow { label: "Append a number"; key: "seq"; locked: true; desc: "Required for batches, so every new name stays unique." }
                            Item { width: 1; height: 12 }
                            Row { spacing: 8
                                Num { label: "Start"; key: "start"; lo: 0 }
                                Num { label: "Step"; key: "step"; lo: 1 }
                                Num { label: "Digits"; key: "pad"; lo: 0; hi: 8 } }
                            Item { width: 1; height: 12 }
                            Field { label: "Separator"; key: "sep"; placeholder: "_" }
                        }

                        // ---- resize
                        Column {
                            visible: root.mode === "resize"; width: parent.width
                            Gh { title: "IMAGE SIZE" }
                            Item { width: 1; height: 12 }
                            Pick { label: "Preset"; key: "preset" }
                            Item { width: 1; height: 8 }
                            Note { text: { void root.rev; return batch.presetDesc() } }
                            Item { visible: batch.get("preset") === "custom"; width: 1; height: visible ? 12 : 0 }
                            Row { visible: batch.get("preset") === "custom"; spacing: 8
                                Field { width: 145.5; label: "Width"; key: "cw"; placeholder: "1920"; suffix: "px" }
                                Field { width: 145.5; label: "Height"; key: "ch"; placeholder: "1080"; suffix: "px" } }
                            Item { width: 1; height: 22 }
                            Gh { title: "OPTIONS" }
                            Item { width: 1; height: 6 }
                            Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                            SwRow { label: "Make pictures smaller but not larger"; key: "shrink" }
                            SwRow { label: "Ignore the orientation of pictures"; key: "ignOri" }
                            SwRow { label: "Overwrite files"; key: "over" }
                            SwRow { label: "Remove metadata that doesn’t affect rendering"; key: "strip" }
                            Item { width: 1; height: 14 }
                            Note { text: { void root.rev; return batch.note() } }
                        }

                        // ---- convert
                        Column {
                            visible: root.mode === "convert"; width: parent.width
                            Gh { title: "OUTPUT FORMAT" }
                            Item { width: 1; height: 12 }
                            Pick { label: "Format"; key: "fmt" }
                            Item { width: 1; height: 8 }
                            Note { text: { void root.rev; return batch.fmtDesc() } }
                            Item { width: 1; height: 22 }
                            Gh { title: "OPTIONS" }
                            Item { width: 1; height: 6 }
                            Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                            SwRow { label: "Overwrite files"; key: "over" }
                            SwRow { label: "Remove metadata that doesn’t affect rendering"; key: "strip" }
                            Item { width: 1; height: 14 }
                            Note { text: { void root.rev; return batch.note() } }
                        }
                    }
                }
            }

            // ---- preview -------------------------------------------------------------------------------------------------
            Item {
                id: main; x: 340; y: 40; width: parent.width - 340; height: parent.height - 98
                Item {
                    id: mh; width: parent.width; height: 44
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Row {
                        x: 24; spacing: 10; height: parent.height
                        Text { height: parent.height; verticalAlignment: Text.AlignVCenter; text: "Preview"; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: -0.14 }
                        Text { height: parent.height; verticalAlignment: Text.AlignVCenter; text: root.mode === "rename" ? "New names" : "Output files"; color: theme.fg3; font.pixelSize: 13 }
                        Rectangle {
                            height: 24; width: pt.implicitWidth + 31; radius: 12; color: theme.s2; border.color: theme.lineSoft; border.width: 1
                            anchors.verticalCenter: parent.verticalCenter
                            Row { anchors.centerIn: parent; spacing: 6
                                Rectangle { width: 7; height: 7; radius: 4; color: batch.errorCount > 0 ? theme.rej : theme.accent; anchors.verticalCenter: parent.verticalCenter }
                                Text { id: pt; text: batch.summary; color: theme.fg2; font.pointSize: UiState.pt(11.5) } }
                        }
                    }
                }
                Text {
                    id: scopeText
                    x: 24; y: 44 + 12; width: parent.width - 48; wrapMode: Text.WordWrap; textFormat: Text.StyledText
                    color: theme.fg3; font.pixelSize: 12; lineHeight: 17.4; lineHeightMode: Text.FixedHeight
                    text: "<b><font color='" + theme.fg2 + "'>" + root.scopeParts[0] + "</font></b> " + (root.scopeParts[1] || "")
                }
                ListView {
                    id: table
                    x: 0; y: 44 + 12 + scopeText.height + 10; width: parent.width; height: parent.height - y
                    clip: true; boundsBehavior: Flickable.StopAtBounds; model: root.rows
                    headerPositioning: ListView.OverlayHeader
                    ScrollBar.vertical: ScrollBar { id: tbar; policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: tbar.active || tbar.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 300 } } } background: Item {} }
                    readonly property real fr: (width - 48 - 28 - 16 - 104 - 4 * 12) / 2.2
                    header: Rectangle {
                        z: 2; width: table.width; height: 30; color: theme.base
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                        Text { x: 24 + 28 + 12; anchors.verticalCenter: parent.verticalCenter; text: "CURRENT NAME"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                        Text { x: 24 + 28 + 12 + table.fr + 12 + 16 + 12; anchors.verticalCenter: parent.verticalCenter; text: root.mode === "rename" ? "NEW NAME" : "OUTPUT FILE"; color: theme.fg3
                               font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                        Text { x: table.width - 24 - 104; anchors.verticalCenter: parent.verticalCenter; text: "STATUS"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                    }
                    delegate: Item {
                        id: row
                        required property var modelData
                        required property int index
                        readonly property bool second: modelData.note !== "" || modelData.err !== ""
                        width: table.width
                        height: Math.max(44, 6 + Math.max(28, nameCol.height, newCol.height) + 6 + 1)
                        Rectangle { anchors.fill: parent; color: Qt.alpha(theme.lift, rh.hovered ? 0.03 : 0) }
                        HoverHandler { id: rh }
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                        Item {
                            x: 24; y: (parent.height - 1 - 28) / 2; width: 28; height: 28
                            RoundClip { anchors.fill: parent; radius: 6; ThumbImage { anchors.fill: parent; path: row.modelData.thumb; bucket: 120 } }
                        }
                        Column {
                            id: nameCol
                            x: 24 + 28 + 12; y: (row.height - 1 - height) / 2; width: table.fr; spacing: 1
                            Text { width: parent.width; elide: Text.ElideRight; height: 18.2; verticalAlignment: Text.AlignVCenter; text: row.modelData.cur; color: theme.fg; font.pixelSize: 13 }
                            Text { visible: row.modelData.note !== ""; width: parent.width; elide: Text.ElideRight; height: visible ? 15.4 : 0; verticalAlignment: Text.AlignVCenter
                                   text: row.modelData.note; color: theme.fg3; font.pointSize: UiState.pt(11) }
                        }
                        Icon { x: 24 + 28 + 12 + table.fr + 12; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -0.5; size: 14; color: theme.fg3; path: "M5 12h14M13 6l6 6-6 6" }
                        Column {
                            id: newCol
                            x: 24 + 28 + 12 + table.fr + 12 + 16 + 12; y: (row.height - 1 - height) / 2; width: table.fr * 1.2; spacing: 1
                            Text { width: parent.width; elide: Text.ElideRight; height: 18.2; verticalAlignment: Text.AlignVCenter; text: row.modelData.next
                                   color: row.modelData.same ? theme.fg3 : theme.fg; font.pixelSize: 13 }
                            Text { visible: row.modelData.err !== ""; width: parent.width; wrapMode: Text.WordWrap; text: row.modelData.err; color: theme.rej; font.pointSize: UiState.pt(11)
                                   lineHeight: 14.85; lineHeightMode: Text.FixedHeight }
                        }
                        Row {
                            x: table.width - 24 - 104; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -0.5; spacing: 8
                            Rectangle { width: 7; height: 7; radius: 4; anchors.verticalCenter: parent.verticalCenter
                                color: row.modelData.tone === "g1" ? theme.win : row.modelData.tone === "g2" ? theme.keep : row.modelData.tone === "g3" ? theme.rev : row.modelData.tone === "g4" ? theme.rej : theme.fg3 }
                            Text { text: row.modelData.status; font.pixelSize: 12; color: row.modelData.tone === "g4" ? theme.rej : theme.fg2; anchors.verticalCenter: parent.verticalCenter }
                        }
                    }
                }
            }

            // ---- footer -------------------------------------------------------------------------------------------------
            Rectangle {
                x: 0; y: parent.height - 58; width: parent.width; height: 58; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                Row {
                    x: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                    Item {
                        width: 8; height: 8; anchors.verticalCenter: parent.verticalCenter
                        readonly property color tone: batch.errorCount > 0 ? theme.rej : (batch.canApply ? theme.win : theme.fg3)
                        readonly property bool lit: batch.errorCount > 0 || batch.canApply
                        Rectangle { anchors.centerIn: parent; width: 16; height: 16; radius: 8; color: Qt.alpha(parent.tone, 0.22); visible: parent.lit }
                        Rectangle { anchors.fill: parent; radius: 4; color: parent.tone; opacity: parent.lit ? 1 : 0.5 }
                    }
                    Text {
                        font.pixelSize: 12; width: 640; elide: Text.ElideRight
                        text: batch.foot + (batch.canApply && batch.sampleNote !== "" ? "  " + batch.sampleNote : "")
                        color: batch.errorCount > 0 ? theme.rej : theme.fg3
                    }
                }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { text: "Reset"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: batch.reset() }
                    SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: root.close() }
                    SButton { text: batch.applyLabel; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: batch.canApply && !batch.busy; onClicked: root.apply() }
                }
            }
        }
    }
}
