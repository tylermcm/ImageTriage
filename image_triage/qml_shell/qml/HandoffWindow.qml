import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Effects

// Deliver / Handoff Builder: one window over the dimmed and blurred application (the same overlay as settings, filters and batch). The left
// side is compact enough to need no scrolling (it tightens its spacing as the window gets shorter); the right side says what the recipe will do.
// The recipes, their wording and their storage are the Widgets app's own, held by `handoff` (handoff_model.py).
FocusScope {
    id: root
    objectName: "handoffWindow"
    property Item sourceItem
    readonly property bool shown: UiState.handoffOpen
    anchors.fill: parent
    visible: opacity > 0.01
    opacity: shown ? 1 : 0
    z: 107
    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

    readonly property real winW: Math.min(1100, width - 96)
    readonly property real winH: Math.min(780, height - 68 - 24)
    // spacing level: 0 full, 1 and 2 tighter as the window gets shorter, so the left side never needs to scroll
    readonly property int lv: winH >= 770 ? 0 : (winH >= 690 ? 1 : 2)
    readonly property real fh: [32, 30, 28][lv]          // height of every field
    readonly property real rowGap: [8, 5, 3][lv]
    readonly property real secGap: [14, 8, 4][lv]
    readonly property real togH: [36, 28, 24][lv]
    readonly property int rev: handoff.revision
    readonly property var steps: { void rev; return handoff.steps() }

    function dis(key) { void rev; return !handoff.enabled(key) }
    function open() { UiState.handoffOpen = true }
    function close() { UiState.handoffOpen = false }
    function run() { handoff.run(); close() }
    onShownChanged: if (shown) { handoff.begin(); forceActiveFocus() }
    Keys.onEscapePressed: root.close()

    ShaderEffectSource { id: snap; anchors.fill: parent; sourceItem: root.sourceItem; live: true; visible: false }
    MultiEffect { anchors.fill: parent; source: snap; blurEnabled: true; blur: 1.0; blurMax: 20 }
    Rectangle { anchors.fill: parent; color: theme.light ? Qt.rgba(20 / 255, 22 / 255, 28 / 255, 0.34) : Qt.rgba(6 / 255, 7 / 255, 9 / 255, 0.52) }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onClicked: root.close(); onWheel: (w) => { w.accepted = true } }

    // ---- small pieces ------------------------------------------------------------------------------------------------------
    component Head: Item {
        id: hd
        property string title
        default property alias extra: right.data
        width: parent ? parent.width : 389; height: root.lv === 2 ? 20 : 24
        Text { anchors.verticalCenter: parent.verticalCenter; text: hd.title; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
        Row { id: right; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 0 }
    }
    component Mini: Item {
        id: mb
        property string text
        signal clicked()
        implicitWidth: mt.implicitWidth + 16; width: implicitWidth; height: 24
        opacity: enabled ? 1 : 0.4
        Rectangle { anchors.fill: parent; radius: 8; color: Qt.alpha(theme.lift, mh.hovered && mb.enabled ? 0.08 : 0) }
        Text { id: mt; anchors.centerIn: parent; text: mb.text; color: mh.hovered && mb.enabled ? theme.fg : theme.fg2; font.pixelSize: 12 }
        HoverHandler { id: mh; enabled: mb.enabled; cursorShape: Qt.PointingHandCursor }
        TapHandler { enabled: mb.enabled; onTapped: mb.clicked() }
        Accessible.role: Accessible.Button; Accessible.name: mb.text
    }
    // label on the left, the control on the right
    component Bf: Item {
        id: bf
        property string label
        property bool off: false
        default property alias control: slot.data
        width: parent ? parent.width : 389; height: root.fh
        opacity: off ? 0.42 : 1
        enabled: !off
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Text { width: 100; height: parent.height; verticalAlignment: Text.AlignVCenter; text: bf.label; color: theme.fg2; font.pixelSize: 12; elide: Text.ElideRight }
        Item { id: slot; x: 110; width: parent.width - 110; height: parent.height }
    }
    component Pick: Bf {
        id: pk
        property string key
        SSelect { width: parent.width; height: parent.height; menuMax: 268; options: { void root.rev; return handoff.options(pk.key) }
                  current: { void root.rev; return handoff.get(pk.key) }
                  onPicked: (v) => handoff.set(pk.key, v) }
    }
    component Tog: Item {
        id: tg
        property string label
        property string key
        property bool off: false
        width: parent ? parent.width : 389; height: root.togH
        opacity: off ? 0.42 : 1
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Text { anchors.verticalCenter: parent.verticalCenter; text: tg.label; color: theme.fg; font.pixelSize: 13 }
        SSwitch { x: parent.width - width; anchors.verticalCenter: parent.verticalCenter; checked: { void root.rev; return !!handoff.get(tg.key) }
                  enabled: !tg.off; onToggled: (v) => handoff.set(tg.key, v) }
    }
    component Pt: Item {   // one step of the preview
        id: st
        required property var modelData
        required property int index
        readonly property bool last: index === root.steps.length - 1
        width: parent.width; height: Math.max(32, 5 + col.height) + (last ? 0 : 22)
        Rectangle { visible: !st.last; x: 15; y: 34; width: 1; height: parent.height - 34 - 2; color: theme.line }
        Rectangle {
            width: 32; height: 32; radius: 10
            color: modelData.tone === "acc" ? Qt.alpha(theme.accent, 0.16) : modelData.tone === "warn" ? Qt.alpha(theme.rev, 0.16) : theme.s3
            border.width: 1; border.color: modelData.tone === "acc" ? Qt.alpha(theme.accent, 0.30) : modelData.tone === "warn" ? Qt.alpha(theme.rev, 0.32) : theme.lineSoft
            Icon { anchors.centerIn: parent; size: 17; strokeWidth: 1.6; path: modelData.d
                   color: modelData.tone === "acc" ? theme.accentFg : modelData.tone === "warn" ? theme.rev : theme.fg2 }
        }
        Column {
            id: col; x: 46; y: 5; width: parent.width - 46; spacing: 2
            Text { text: modelData.head; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; height: 18.2; verticalAlignment: Text.AlignVCenter }
            Text { width: parent.width; wrapMode: Text.WrapAnywhere; text: modelData.text; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight }
        }
    }

    FolderDialog { id: rootDialog; title: "Choose Destination Root"; onAccepted: handoff.set("root", selectedFolder.toString().replace(/^file:\/\/\/?/, Qt.platform.os === "windows" ? "" : "/")) }

    Item {
        id: win
        x: (root.width - width) / 2; y: Math.max(40, (root.height - height) / 2 + 8)
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
                Icon { x: 14; anchors.verticalCenter: parent.verticalCenter; size: 16; color: theme.fg2; path: "M12 15V4M8 8l4-4 4 4M5 14v4a2 2 0 002 2h10a2 2 0 002-2v-4" }
                Text { x: 40; anchors.verticalCenter: parent.verticalCenter; text: "Deliver / Handoff Builder"; color: theme.fg2; font.pixelSize: 13; font.weight: Font.Medium }
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

            // ---- recipe ---------------------------------------------------------------------------------------------------
            Rectangle {
                id: nav; x: 0; y: 40; width: 430; height: parent.height - 98; color: theme.s2
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }
                Flickable {
                    id: opts
                    width: parent.width - 1; height: parent.height
                    contentWidth: width; contentHeight: col.y + col.height + 12
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { id: obar; policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: obar.active || obar.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 300 } } } background: Item {} }
                    Column {
                        id: col
                        x: 20; y: 8; width: 389; spacing: 0
                        // RECIPE
                        Head { title: "RECIPE"
                            Mini { text: handoff.saveLabel; onClicked: handoff.save() }
                            Mini { text: "Delete saved"; enabled: handoff.canDelete; onClicked: handoff.remove() } }
                        Item { width: 1; height: root.rowGap }
                        Pick { label: "Load"; key: "load" }
                        Item { width: 1; height: root.rowGap }
                        Bf { label: "Name"; FInput { width: parent.width; height: parent.height; placeholder: "Export Recipe"; text: { void root.rev; return String(handoff.get("name")) }
                                                     onEdited: (t) => handoff.set("name", t) } }
                        Item { width: 1; height: root.rowGap }
                        Bf { label: "Description"; FInput { width: parent.width; height: parent.height; placeholder: "What this recipe is for"; text: { void root.rev; return String(handoff.get("desc")) }
                                                           onEdited: (t) => handoff.set("desc", t) } }
                        Item { width: 1; height: root.secGap }
                        // WORKFLOW
                        Head { title: "WORKFLOW" }
                        Item { width: 1; height: root.rowGap }
                        Bf { label: "Content"
                            Seg { width: parent.width; height: parent.height; fill: true; showExtras: false; labels: handoff.contentLabels()
                                  current: { void root.rev; return handoff.get("content") === "full_bundle" ? 1 : 0 }
                                  onActivated: (i) => handoff.set("content", i === 1 ? "full_bundle" : "export_primary") } }
                        Item { width: 1; height: root.rowGap }
                        Bf { label: "Transfer"; off: root.dis("transfer")
                            Seg { width: parent.width; height: parent.height; fill: true; showExtras: false; labels: handoff.transferLabels()
                                  current: { void root.rev; var t = handoff.get("transfer"); return t === "move" ? 1 : t === "archive" ? 2 : 0 }
                                  onActivated: (i) => handoff.set("transfer", ["copy", "move", "archive"][i]) } }
                        Item { width: 1; height: root.rowGap }
                        Bf { label: "Destination root"
                            Row { width: parent.width; height: parent.height; spacing: 8
                                FInput { width: parent.width - browse.width - 8; height: parent.height; placeholder: "Choose a destination root"; text: { void root.rev; return String(handoff.get("root")) }
                                         onEdited: (t) => handoff.set("root", t) }
                                SButton { id: browse; text: "Browse…"; h: parent.height; hpad: 14; onClicked: rootDialog.open() } } }
                        Item { width: 1; height: root.rowGap }
                        Bf { label: "Subfolder"; FInput { width: parent.width; height: parent.height; placeholder: "Optional subfolder"; text: { void root.rev; return String(handoff.get("sub")) }
                                                          onEdited: (t) => handoff.set("sub", t) } }
                        Item { width: 1; height: root.lv === 0 ? 6 : 2 }
                        Tog { label: "Create one folder per selected record"; key: "group"; off: root.dis("group") }
                        Item { width: 1; height: root.secGap }
                        // OUTPUT
                        Head { title: "OUTPUT" }
                        Item { width: 1; height: root.rowGap }
                        Pick { label: "Resize"; key: "resize"; off: root.dis("resize") }
                        Item { width: 1; height: root.rowGap }
                        Pick { label: "Convert"; key: "convert"; off: root.dis("convert") }
                        Item { width: 1; height: root.rowGap }
                        Bf { label: "Filename"; off: root.dis("affixes")
                            Row { width: parent.width; height: parent.height; spacing: 8
                                FInput { width: (parent.width - 8) / 2; height: parent.height; placeholder: "Prefix"; text: { void root.rev; return String(handoff.get("pre")) }
                                         onEdited: (t) => handoff.set("pre", t) }
                                FInput { width: (parent.width - 8) / 2; height: parent.height; placeholder: "Suffix"; text: { void root.rev; return String(handoff.get("suf")) }
                                         onEdited: (t) => handoff.set("suf", t) } } }
                        Item { width: 1; height: root.lv === 0 ? 6 : 2 }
                        Tog { label: "Strip metadata from exported files"; key: "strip"; off: root.dis("strip") }
                        Tog { label: "Archive exported output"; key: "arch"; off: root.dis("arch") }
                        Item { width: 1; height: root.rowGap }
                        Pick { label: "Archive format"; key: "archFmt"; off: root.dis("archFmt") }
                    }
                }
            }

            // ---- preview ------------------------------------------------------------------------------------------------
            Item {
                id: main; x: 430; y: 40; width: parent.width - 430; height: parent.height - 98
                Item {
                    id: mh; width: parent.width; height: 44
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Row {
                        x: 24; spacing: 10; height: parent.height
                        Text { height: parent.height; verticalAlignment: Text.AlignVCenter; text: "Preview"; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: -0.14 }
                        Text { height: parent.height; verticalAlignment: Text.AlignVCenter; text: "What this recipe will do"; color: theme.fg3; font.pixelSize: 13 }
                        Rectangle {
                            height: 24; width: pt.implicitWidth + 31; radius: 12; color: theme.s2; border.color: theme.lineSoft; border.width: 1
                            anchors.verticalCenter: parent.verticalCenter
                            Row { anchors.centerIn: parent; spacing: 6
                                Rectangle { width: 7; height: 7; radius: 4; color: theme.accent; anchors.verticalCenter: parent.verticalCenter }
                                Text { id: pt; text: handoff.countText; color: theme.fg2; font.pointSize: UiState.pt(11.5) } }
                        }
                    }
                }
                Flickable {
                    y: 44; width: parent.width; height: parent.height - 44
                    contentWidth: width; contentHeight: body.height + 50
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    Column {
                        id: body
                        x: 32; y: 22; width: parent.width - 64; spacing: 0
                        Text { width: parent.width; text: handoff.title; color: theme.fg; font.pixelSize: 18; font.weight: Font.DemiBold; font.letterSpacing: -0.27
                               lineHeight: 23.4; lineHeightMode: Text.FixedHeight; elide: Text.ElideRight }
                        Text { width: Math.min(560, parent.width); topPadding: 4; wrapMode: Text.WordWrap; text: handoff.blurb; color: theme.fg2; font.pixelSize: 13
                               lineHeight: 18.2; lineHeightMode: Text.FixedHeight }
                        Item { width: 1; height: 26 }
                        Column { width: parent.width; spacing: 0
                            Repeater { model: root.steps; delegate: Pt {} } }
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
                        readonly property color tone: handoff.hasRoot ? theme.win : theme.rej
                        Rectangle { anchors.centerIn: parent; width: 16; height: 16; radius: 8; color: Qt.alpha(parent.tone, 0.22) }
                        Rectangle { anchors.fill: parent; radius: 4; color: parent.tone }
                    }
                    Text { font.pixelSize: 12; width: 640; elide: Text.ElideRight; text: handoff.foot; color: handoff.hasRoot ? theme.fg3 : theme.rej }
                }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: root.close() }
                    SButton { text: "Run workflow"; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: handoff.hasRoot; onClicked: root.run() }
                }
            }
        }
    }
}
