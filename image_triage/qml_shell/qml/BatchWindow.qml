import QtQuick
import QtQuick.Controls
import QtQuick.Effects
import QtQuick.Dialogs

// Batch rename and export (Export replaced resize and convert; their commands open it): one window over the dimmed and blurred application (the same overlay as settings and filters). Layout,
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

    readonly property real winW: Math.min(1180, width - 96)
    readonly property real winH: Math.min(1240, height - 68 - 64)     // tall: the export settings are long
    readonly property real navW: Math.round(Math.min(480, Math.max(340, winW * 0.4)))
    readonly property real colW: navW - 41              // the settings, 20px in from the left and clear of the scroll bar
    readonly property real half: (colW - 12) / 2        // two settings side by side
    readonly property real third: (colW - 16) / 3
    readonly property int rev: batch.revision
    readonly property string mode: batch.mode
    readonly property var rows: { void rev; return batch.rows() }
    readonly property var modes: [["rename", "Rename"], ["export", "Export"]]
    property bool showBefore: false
    property bool showDetail: false
    property bool picking: false
    property bool pickingMark: false
    property bool pickingProfile: false
    property string presetError: ""
    readonly property var scopeParts: { void rev; return batch.scope(photos.folderName).split("|") }
    property string applied: ""

    function open(mode) { UiState.batchMode = mode }
    function close() { UiState.batchMode = ""; UiState.batchOnly = ""; batch.end() }
    // "This photo": the photo the viewer or the grid is on, taken alone (a plain click picks nothing, so the usual scope is every photo)
    function currentPath() {
        if (UiState.batchOnly !== "") return UiState.batchOnly
        var row = UiState.selRow >= 0 ? photos.get(UiState.selRow) : null
        return row && row.path ? row.path : ""
    }
    // Resize and Convert have no undo, so writing over the originals asks first and says how many.
    function apply() {
        if (batch.mode === "export" && root.overwrites() > 0) { overwriteCard.open(); return }
        batch.apply(); close()
    }
    function overwrites() { var n = 0; for (var i = 0; i < rows.length; ++i) if (rows[i].status === "Overwrite") ++n; return n }
    onShownChanged: if (shown) { batch.setOnly(UiState.batchOnly); batch.begin(UiState.batchMode); forceActiveFocus() }
    ConfirmCard {
        id: overwriteCard; objectName: "batchOverwriteCard"; warn: true; cancelText: "Cancel"; confirmText: "Overwrite"
        title: "Write over the original files?"
        body: root.overwrites() + " file(s) already in the export folder will be replaced. This cannot be undone."
        onAccepted: { batch.apply(); root.close() }
        onRejected: root.forceActiveFocus()
    }
    Keys.onEscapePressed: root.close()
    Keys.onLeftPressed: batch.stepPreview(-1)
    Keys.onRightPressed: batch.stepPreview(1)

    Loader {
        active: root.picking
        sourceComponent: FolderDialog {
            title: "Export to"
            Component.onCompleted: open()
            onAccepted: { batch.set("folder", decodeURIComponent(String(selectedFolder).replace(/^file:\/\/\/?/, Qt.platform.os === "windows" ? "" : "/"))); root.picking = false }
            onRejected: root.picking = false
        }
    }
    Loader {
        active: root.pickingMark
        sourceComponent: FileDialog {
            title: "Watermark picture"; nameFilters: ["Pictures (*.png *.jpg *.jpeg *.webp *.tif *.tiff)"]
            Component.onCompleted: open()
            onAccepted: { batch.set("wmImage", decodeURIComponent(String(selectedFile).replace(/^file:\/\/\/?/, Qt.platform.os === "windows" ? "" : "/"))); root.pickingMark = false }
            onRejected: root.pickingMark = false
        }
    }
    Loader {
        active: root.pickingProfile
        sourceComponent: FileDialog {
            title: "Output profile"; nameFilters: ["ICC profiles (*.icc *.icm)"]
            Component.onCompleted: open()
            onAccepted: { batch.set("profileFile", decodeURIComponent(String(selectedFile).replace(/^file:\/\/\/?/, Qt.platform.os === "windows" ? "" : "/"))); root.pickingProfile = false }
            onRejected: root.pickingProfile = false
        }
    }
    ShaderEffectSource { id: snap; anchors.fill: parent; sourceItem: root.sourceItem; live: true; visible: false }
    MultiEffect { anchors.fill: parent; source: snap; blurEnabled: true; blur: 1.0; blurMax: 20 }
    Rectangle { anchors.fill: parent; color: theme.scrim }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onClicked: root.close(); onWheel: (w) => { w.accepted = true } }

    // ---- small pieces ------------------------------------------------------------------------------------------------------
    component Gh: Text { property alias title: g.text; id: g; width: 1; height: 13; verticalAlignment: Text.AlignVCenter
                         color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
    component Lbl: Text { height: 16.8; verticalAlignment: Text.AlignVCenter; color: theme.fg2; font.pixelSize: 12 }
    component Note: Text { width: parent ? parent.width : root.colW; topPadding: 1.5; wrapMode: Text.WordWrap; color: theme.fg3; font.pixelSize: 12; lineHeight: 17.4; lineHeightMode: Text.FixedHeight }
    // one labelled text field (label 16.8, 6, box 34)
    component Field: Column {
        id: fld
        property string label; property string key; property string placeholder; property string suffix
        objectName: "batchField_" + key
        spacing: 6; width: root.colW
        Lbl { text: fld.label }
        FInput { width: parent.width; placeholder: fld.placeholder; suffix: fld.suffix; text: { void root.rev; return String(batch.get(fld.key)) }
                 onEdited: (t) => batch.set(fld.key, t) }
    }
    component Pick: Column {
        id: pk
        property string label; property string key; property bool custom: false
        signal chosen(var value)
        objectName: "batchPick_" + key
        spacing: 6; width: root.colW
        Lbl { text: pk.label }
        SSelect { width: parent.width; name: pk.label; options: { void root.rev; return batch.options(pk.key) }
                  current: { void root.rev; return batch.get(pk.key) }
                  onPicked: (v) => { if (pk.custom) pk.chosen(v); else batch.set(pk.key, v) } }
    }
    component Gap: Item { property real h: 12; width: 1; height: h }
    // a section of the export settings that folds away under its title; which are open is kept while the app runs
    property var secOpen: ({})
    component Sec: Column {
        id: sec
        property string title
        property bool startOpen: true
        default property alias body: secBody.data
        readonly property bool open: { var o = root.secOpen[title]; return o === undefined ? startOpen : o }
        objectName: "batchSec_" + title
        width: root.colW
        bottomPadding: open ? 22 : 10
        Item {
            width: parent.width; height: 24
            Rectangle { x: -8; width: parent.width + 16; height: parent.height; radius: 6; color: Qt.alpha(theme.lift, sh.hovered ? 0.05 : 0) }
            Gh { anchors.verticalCenter: parent.verticalCenter; title: sec.title }
            Icon { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; size: 14; color: sh.hovered ? theme.fg : theme.fg3
                   path: "M6 9l6 6 6-6"; rotation: sec.open ? 0 : -90; Behavior on rotation { NumberAnimation { duration: 140 } } }
            HoverHandler { id: sh; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: { var m = Object.assign({}, root.secOpen); m[sec.title] = !sec.open; root.secOpen = m } }
            Accessible.role: Accessible.Button; Accessible.name: (sec.open ? "Collapse " : "Expand ") + sec.title
        }
        Column { id: secBody; width: parent.width; visible: sec.open; topPadding: 8 }
    }
    component Num: Column {
        id: nm
        property string label; property string key; property int lo: 0; property int hi: 999999
        spacing: 6; width: root.third
        Lbl { text: nm.label }
        SStepper { objectName: "batchNum_" + nm.key; width: parent.width; editable: true; text: { void root.rev; return String(batch.get(nm.key)) }
                   onEdited: (typed) => batch.set(nm.key, typed)          // the model clamps it to the field's range
                   onDecrement: { batch.set(nm.key, Math.max(nm.lo, batch.get(nm.key) - 1)) }
                   onIncrement: { batch.set(nm.key, Math.min(nm.hi, batch.get(nm.key) + 1)) } }
    }
    // a switch row: label, optional description, border below; the group's top border is drawn by Rows
    component SwRow: Item {
        id: sr
        property string label; property string desc; property string key; property bool locked: false
        width: root.colW
        implicitHeight: Math.max(46, 8 + lt.height + (desc !== "" ? 2 + dt.height : 0) + 8 + 1)
        height: implicitHeight
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
        Column {
            x: 0; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 0.5; width: sr.width - 52; spacing: 2
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
                id: nav; x: 0; y: 40; width: root.navW; height: parent.height - 98; color: theme.s2
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }
                Item {
                    id: modeBox; x: 12; y: 12; width: parent.width - 25; height: 32
                    Seg { anchors.fill: parent; fill: true; showExtras: false; labels: ["Rename", "Export"]
                          current: { var i = 0; for (var k = 0; k < root.modes.length; ++k) if (root.modes[k][0] === root.mode) i = k; return i }
                          onActivated: (i) => { UiState.batchMode = root.modes[i][0]; batch.setMode(root.modes[i][0]) } }
                }
                Flickable {
                    id: opts
                    objectName: "batchOptionsList"
                    y: 44; width: parent.width - 1; height: parent.height - 44
                    contentWidth: width; contentHeight: optCol.y + optCol.height + 24
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { id: obar; policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: obar.active || obar.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 300 } } } background: Item {} }
                    Column {
                        id: optCol
                        x: 20; y: 24; width: root.colW

                        // ---- rename
                        Column {
                            visible: root.mode === "rename"; width: parent.width
                            Gh { title: "RENAME RULES" }
                            Item { width: 1; height: 12 }
                            Field { label: "New name"; key: "newName"; placeholder: "Optional base name (without extension)" }
                            Item { width: 1; height: 12 }
                            Row { spacing: 12
                                Field { width: root.half; label: "Prefix"; key: "prefix"; placeholder: "Optional prefix" }
                                Field { width: root.half; label: "Suffix"; key: "suffix"; placeholder: "Optional suffix" } }
                            Item { width: 1; height: 12 }
                            Row { spacing: 12
                                Pick { width: root.half; label: "Case"; key: "case" }
                                Field { width: root.half; label: "Separator"; key: "sep"; placeholder: "_" } }
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
                            Item { width: 1; height: 22 }
                            Gh { title: "EXPORT" }
                            Item { width: 1; height: 6 }
                            Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                            SwRow { objectName: "batchRenameForExport"; label: "Use these names when exporting"; key: "xRename"
                                    desc: "Exported copies get these names. The originals keep theirs." }
                        }

                        // ---- export
                        Column {
                            id: ex
                            objectName: "batchExportOptions"
                            visible: root.mode === "export"; width: parent.width
                            readonly property string sizeMode: { void root.rev; return batch.get("sizeMode") }
                            readonly property string format: { void root.rev; return batch.get("format") }
                            readonly property string dest: { void root.rev; return batch.get("dest") }
                            readonly property string preset: { void root.rev; return batch.get("xPreset") }
                            readonly property string sharpen: { void root.rev; return batch.get("sharpen") }
                            readonly property string profile: { void root.rev; return batch.get("profile") }
                            readonly property string wm: { void root.rev; return batch.get("wm") }
                            Sec { title: "PRESET"; startOpen: true
                                Pick { label: "Preset"; key: "xPreset"; custom: true; onChosen: (v) => { root.presetError = ""; if (v !== "") batch.usePreset(v) } }
                                Gap { h: 8 }
                                Row {
                                    spacing: 8
                                    FInput { id: presetName; objectName: "batchPresetName"; width: root.colW - 8 - 70 - (deleteBtn.visible ? 70 + 8 : 0); placeholder: "Save these settings as…" }
                                    SButton { text: "Save"; variant: "ghost"; h: 34; hpad: 14; radius: 10; width: 70
                                              onClicked: { root.presetError = batch.savePreset(presetName.text); if (root.presetError === "") presetName.text = "" } }
                                    SButton { id: deleteBtn; text: "Delete"; variant: "ghost"; h: 34; hpad: 12; radius: 10; width: 70
                                              visible: ex.preset !== "" && !batch.presetIsBuiltin(ex.preset)
                                              onClicked: batch.deletePreset(ex.preset) }
                            }
                            Note { visible: root.presetError !== ""; color: theme.rej; text: root.presetError }
                            }
                            Sec { title: "IMAGE SIZE"; startOpen: true
                                Row { spacing: 12
                                    Pick { width: root.half; label: "Size"; key: "sizeMode" }
                                    Field { width: root.half; visible: ex.sizeMode === "long"; label: "Long edge"; key: "long"; placeholder: "2048"; suffix: "px" }
                                    Field { width: root.half; visible: ex.sizeMode === "short"; label: "Short edge"; key: "short"; placeholder: "1080"; suffix: "px" }
                                    Field { width: (root.half - 12) / 2; visible: ex.sizeMode === "fit"; label: "Width"; key: "fitW"; placeholder: "1920"; suffix: "px" }
                                    Field { width: (root.half - 12) / 2; visible: ex.sizeMode === "fit"; label: "Height"; key: "fitH"; placeholder: "1080"; suffix: "px" }
                                    Field { width: root.half; visible: ex.sizeMode === "mp"; label: "Megapixels"; key: "mp"; placeholder: "12"; suffix: "MP" }
                                    Field { width: root.half; visible: ex.sizeMode === "pct"; label: "Percentage"; key: "pct"; placeholder: "50"; suffix: "%" }
                            }
                            Gap { h: 8 }
                            Note { text: { void root.rev; return batch.sizeDesc() } }
                            Gap { h: 6; visible: ex.sizeMode !== "original" }
                            Rectangle { visible: ex.sizeMode !== "original"; width: parent.width; height: 1; color: theme.lineSoft }
                            SwRow { visible: ex.sizeMode !== "original"; label: "Don’t enlarge smaller photos"; key: "noEnlarge" }
                            }
                            Sec { title: "FILE"; startOpen: true
                                Row { spacing: 12
                                    Pick { width: root.half; label: "Format"; key: "format" }
                                    Field { width: root.half; visible: ex.format === "jpg" || ex.format === "webp"; label: "Quality (1–100)"; key: "quality"; placeholder: "90" }
                                    Pick { width: root.half; visible: ex.format === "png" || ex.format === "tif"; label: "Bit depth"; key: "depth" }
                            }
                            Gap {}
                            Row { spacing: 12
                                Pick { width: root.half; label: "Colour profile"; key: "profile" }
                                Pick { width: root.half; label: "Metadata"; key: "meta" }
                            }
                            Gap { visible: ex.profile === "file" }
                            Row {
                                visible: ex.profile === "file"; spacing: 8
                                Field { width: root.colW - 8 - 84; label: "Profile file (.icc, .icm)"; key: "profileFile"; placeholder: "Choose a profile" }
                                SButton { anchors.bottom: parent.bottom; text: "Browse…"; variant: "ghost"; h: 34; hpad: 12; radius: 10; width: 84; onClicked: root.pickingProfile = true }
                            }
                            }
                            Sec { title: "WATERMARK"; startOpen: true
                                Row { spacing: 12
                                    Pick { width: root.half; label: "Watermark"; key: "wm" }
                                    Pick { width: root.half; visible: ex.wm !== "none"; label: "Position"; key: "wmPos" }
                            }
                            Gap { visible: ex.wm === "text" }
                            Row { visible: ex.wm === "text"; spacing: 12
                                Field { width: root.half; label: "Text"; key: "wmText"; placeholder: "© Your Name" }
                                Pick { width: root.half; label: "Colour"; key: "wmColor" }
                            }
                            Gap { visible: ex.wm === "image" }
                            Row {
                                visible: ex.wm === "image"; spacing: 8
                                Field { width: root.colW - 8 - 84; label: "Picture (PNG with transparency works best)"; key: "wmImage"; placeholder: "Choose a picture" }
                                SButton { anchors.bottom: parent.bottom; text: "Browse…"; variant: "ghost"; h: 34; hpad: 12; radius: 10; width: 84; onClicked: root.pickingMark = true }
                            }
                            Gap { visible: ex.wm !== "none" }
                            Row { visible: ex.wm !== "none"; spacing: 8
                                Field { width: root.third; label: "Size"; key: "wmSize"; suffix: "%" }
                                Field { width: root.third; label: "Opacity"; key: "wmOpacity"; suffix: "%" }
                                Field { width: root.third; label: "Margin"; key: "wmInset"; suffix: "%" } }
                            }
                            Sec { title: "NAME AND PLACE"; startOpen: true
                                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                                SwRow { objectName: "batchExportRenamed"; label: "Name with the Rename tab's rules"; key: "xRename"
                                        desc: "The exported copies get the names the Rename tab gives them." }
                                Gap {}
                                Row { spacing: 12
                                    Field { width: root.half; label: "Before the name"; key: "xPrefix"; placeholder: "Optional" }
                                    Field { width: root.half; label: "After the name"; key: "xSuffix"; placeholder: "Optional" } }
                                Gap {}
                                Row { spacing: 12
                                    Pick { width: root.half; label: "Export to"; key: "dest" }
                                    Pick { width: root.half; label: "When a file is already there"; key: "exists" }
                            }
                            Gap { visible: ex.dest === "subfolder" || ex.dest === "choose" }
                            Field { visible: ex.dest === "subfolder"; label: "Folder name"; key: "sub"; placeholder: "Export" }
                            Row {
                                visible: ex.dest === "choose"; spacing: 8
                                Field { width: root.colW - 8 - 84; label: "Folder"; key: "folder"; placeholder: "Choose a folder" }
                                SButton { anchors.bottom: parent.bottom; text: "Browse…"; variant: "ghost"; h: 34; hpad: 12; radius: 10; width: 84; onClicked: root.picking = true }
                            }
                            }
                            Sec { title: "OUTPUT SHARPENING"; startOpen: false
                                Row { spacing: 12
                                    Pick { width: root.half; label: "Sharpen for"; key: "sharpen" }
                                    Pick { width: root.half; visible: ex.sharpen !== "none"; label: "Amount"; key: "sharpenAmount" }
                            }
                            Gap { h: 8 }
                            Note { text: ex.sharpen === "none" ? "No sharpening for the output." : "Made for the exported size. Use 100% in the preview to see it." }
                            }
                            Sec { title: "SOFT PROOF"; startOpen: false
                                Pick { width: root.half; label: "Rendering intent"; key: "intent" }
                                Gap { h: 6 }
                                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                                SwRow { label: "Black point compensation"; key: "bpc"; desc: "Keeps shadow detail when the output's black is lighter." }
                                SwRow { label: "Simulate paper and ink"; key: "proofPaper"; desc: "The preview shows the paper's white and the ink's black." }
                                SwRow { label: "Gamut warning"; key: "proofGamut"; desc: "Colours the profile cannot reproduce show in pink." }
                                Gap { h: 8 }
                                Note { text: "The preview is the proof of the export's own profile. Intent and black point compensation apply to ICC profile files." }
                            }
                        }
                    }
                }
            }

            // ---- preview -------------------------------------------------------------------------------------------------
            Item {
                id: main; x: root.navW; y: 40; width: parent.width - root.navW; height: parent.height - 98
                Item {
                    id: mh; width: parent.width; height: 44
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    // which photos: the usual scope, or only the one the viewer or the grid is on
                    Row {
                        objectName: "batchScope"
                        anchors.right: parent.right; anchors.rightMargin: 24; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                        visible: root.currentPath() !== "" || batch.only !== ""
                        SButton { objectName: "batchScopeAll"; h: 26; hpad: 10; radius: 8
                                  text: photos.picked > 0 ? "Picked (" + photos.picked + ")" : "All shown"
                                  variant: batch.only === "" ? "primary" : "ghost"; onClicked: batch.setOnly("") }
                        SButton { objectName: "batchScopeOne"; h: 26; hpad: 10; radius: 8; text: "This photo"
                                  variant: batch.only !== "" ? "primary" : "ghost"; onClicked: batch.setOnly(root.currentPath()) }
                    }
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
                Item {
                    id: pane
                    objectName: "batchPreviewPane"
                    x: 24; y: 44 + 12; width: parent.width - 48; height: Math.round((parent.height - 44) * 0.52)
                    Rectangle { anchors.fill: parent; radius: 10; color: theme.s1; border.color: theme.lineSoft }
                    Image {
                        id: shot
                        objectName: "batchPreviewImage"
                        anchors.fill: parent; anchors.margins: 10; anchors.bottomMargin: 34
                        asynchronous: true; cache: false; smooth: true; mipmap: true
                        source: root.showBefore || batch.mode === "rename" ? batch.previewBefore
                              : root.showDetail && batch.previewDetail !== "" ? batch.previewDetail : batch.previewAfter
                        // 100%: one exported pixel per screen pixel, so sharpening and grain look as they will
                        fillMode: root.showDetail && !root.showBefore ? Image.Pad : Image.PreserveAspectFit
                    }
                    Text {
                        anchors.centerIn: shot; width: shot.width - 40; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                        visible: batch.previewBusy && shot.status !== Image.Ready || batch.previewError !== ""
                        text: batch.previewError !== "" ? batch.previewError : "Preparing the preview…"
                        color: batch.previewError !== "" ? theme.rej : theme.fg3; font.pixelSize: 12
                    }
                    Rectangle {
                        visible: batch.previewBusy && shot.status === Image.Ready
                        anchors.right: shot.right; anchors.top: shot.top; anchors.margins: 6; width: 8; height: 8; radius: 4; color: theme.accent
                        SequentialAnimation on opacity { running: parent.visible; loops: Animation.Infinite; NumberAnimation { to: 0.2; duration: 500 } NumberAnimation { to: 1; duration: 500 } }
                    }
                    Item {
                        x: 10; y: parent.height - 30; width: parent.width - 20; height: 24
                        Row {
                            anchors.verticalCenter: parent.verticalCenter; spacing: 6
                            SButton { objectName: "batchPreviewPrev"; text: "‹"; variant: "ghost"; h: 24; hpad: 9; radius: 8; enabled: batch.previewIndex > 0; onClicked: batch.stepPreview(-1) }
                            SButton { objectName: "batchPreviewNext"; text: "›"; variant: "ghost"; h: 24; hpad: 9; radius: 8; enabled: batch.previewIndex < batch.previewCount - 1; onClicked: batch.stepPreview(1) }
                            Text { anchors.verticalCenter: parent.verticalCenter; text: batch.previewCount > 0 ? (batch.previewIndex + 1) + " of " + batch.previewCount + "  ·  " + batch.previewName : ""
                                   color: theme.fg2; font.pixelSize: 12; elide: Text.ElideMiddle; width: Math.min(implicitWidth, pane.width - 330) }
                        }
                        Row {
                            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                            Text { objectName: "batchPreviewInfo"; anchors.verticalCenter: parent.verticalCenter; text: batch.previewInfo; color: theme.fg3; font.pixelSize: 12 }
                            SButton {
                                objectName: "batchPreviewDetail"; visible: batch.mode === "export"
                                text: "100%"; variant: root.showDetail ? "primary" : "ghost"; h: 24; hpad: 10; radius: 8
                                onClicked: root.showDetail = !root.showDetail
                            }
                            SButton {
                                objectName: "batchPreviewBefore"; visible: batch.mode === "export"
                                text: "Before"; variant: root.showBefore ? "primary" : "ghost"; h: 24; hpad: 10; radius: 8
                                onClicked: root.showBefore = !root.showBefore
                            }
                        }
                    }
                }
                Text {
                    id: scopeText
                    x: 24; y: pane.y + pane.height + 12; width: parent.width - 48; wrapMode: Text.WordWrap; textFormat: Text.StyledText
                    color: theme.fg3; font.pixelSize: 12; lineHeight: 17.4; lineHeightMode: Text.FixedHeight
                    text: "<b><font color='" + theme.fg2 + "'>" + root.scopeParts[0] + "</font></b> " + (root.scopeParts[1] || "")
                }
                ListView {
                    id: table
                    x: 0; y: scopeText.y + scopeText.height + 10; width: parent.width; height: parent.height - y
                    clip: true; boundsBehavior: Flickable.StopAtBounds; model: root.rows
                    headerPositioning: ListView.OverlayHeader
                    ScrollBar.vertical: ScrollBar { id: tbar; policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: tbar.active || tbar.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 300 } } } background: Item {} }
                    readonly property real fr: (width - 48 - 28 - 16 - 104 - 4 * 12) / 2.2
                    header: Rectangle {
                        z: 2; width: table.width; height: 30; color: theme.base
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                        Text { x: 24 + 28 + 12; anchors.verticalCenter: parent.verticalCenter; text: "CURRENT NAME"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                        Text { x: 24 + 28 + 12 + table.fr + 12 + 16 + 12; anchors.verticalCenter: parent.verticalCenter; text: root.mode === "rename" ? "NEW NAME" : "EXPORTED FILE"; color: theme.fg3
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
                        Rectangle { anchors.fill: parent; color: row.index === batch.previewIndex ? Qt.alpha(theme.accent, 0.10) : Qt.alpha(theme.lift, rh.hovered ? 0.03 : 0) }
                        HoverHandler { id: rh }
                        TapHandler { onTapped: { batch.setPreviewIndex(row.index); root.forceActiveFocus() } }
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                        Item {
                            x: 24; y: (parent.height - 1 - 28) / 2; width: 28; height: 28
                            RoundClip { anchors.fill: parent; radius: 6; ThumbImage { anchors.fill: parent; path: row.modelData.thumb; bucket: 160 } }
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
