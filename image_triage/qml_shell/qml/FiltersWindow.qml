import QtQuick
import QtQuick.Controls
import QtQuick.Effects
import QtQuick.Shapes

// Advanced filters and saved searches, as a window over the dimmed and blurred application (the same overlay as the settings window).
// Layout, sizes and colours follow the HTML mockup; the query, presets and wording come from `advFilters` (filters_model.py).
FocusScope {
    id: root
    objectName: "filtersWindow"
    property Item sourceItem
    readonly property bool shown: UiState.filtersOpen
    anchors.fill: parent
    visible: opacity > 0.01
    opacity: shown ? 1 : 0
    z: 105
    Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

    readonly property real winW: Math.min(960, width - 96)
    readonly property real winH: Math.min(640, height - 68 - 64)
    readonly property int rev: advFilters.revision
    readonly property var chips: { void rev; return advFilters.chips() }
    readonly property var presets: { void rev; return advFilters.presets() }
    readonly property string sel: { void rev; return advFilters.selected }
    property bool saving: false
    property string saveName: ""
    property string delAsk: ""
    readonly property bool clash: { void rev; return advFilters.clash(saveName) }
    readonly property int unsupported: { void rev; return advFilters.unsupported }
    readonly property bool dirty: { void rev; return advFilters.dirty }

    function open() { UiState.filtersOpen = true }
    function close() { UiState.filtersOpen = false; saving = false; delAsk = "" }
    function apply() { advFilters.apply(); close() }
    onShownChanged: if (shown) { advFilters.begin(); saving = false; delAsk = ""; forceActiveFocus(); Qt.callLater(function() { scroller.contentY = UiState.filtersScroll }) }
    Keys.onEscapePressed: root.close()

    ShaderEffectSource { id: snap; anchors.fill: parent; sourceItem: root.sourceItem; live: true; visible: false }
    MultiEffect { anchors.fill: parent; source: snap; blurEnabled: true; blur: 1.0; blurMax: 15 }
    Rectangle { anchors.fill: parent; color: theme.light ? Qt.rgba(20 / 255, 22 / 255, 28 / 255, 0.34) : Qt.rgba(6 / 255, 7 / 255, 9 / 255, 0.52) }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onClicked: root.close(); onWheel: (w) => { w.accepted = true } }

    Item {
        id: win
        x: (root.width - width) / 2; y: 42 + (root.height - 68 - height) / 2
        width: root.winW; height: root.winH
        scale: root.shown ? 1 : 0.96
        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }
        MouseArea { anchors.fill: parent; onClicked: win.forceActiveFocus() }          // clicks inside do not close it

        Shadow { anchors.fill: parent; radius: 14; offsetA: 48; insetA: 24; blurA: 64; multA: 3; alphaA: 0.85; offsetB: 12; insetB: 12; blurB: 24; alphaB: 0.5 }
        Rectangle { anchors.fill: parent; radius: 14; color: theme.base }
        Rectangle { anchors.fill: parent; radius: 14; color: "transparent"; border.color: theme.line; border.width: 1; z: 10 }

        RoundClip {
            anchors.fill: parent; radius: 14

            // ---- title bar ---------------------------------------------------------------------------------------------
            Rectangle {
                x: 0; y: 0; width: parent.width; height: 40; color: theme.s1
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                Icon { x: 14; anchors.verticalCenter: parent.verticalCenter; size: 16; color: theme.fg2; path: "M4 5h16l-6 8v5l-4 2v-7z" }
                Text { x: 40; anchors.verticalCenter: parent.verticalCenter; text: "Advanced filters"; color: theme.fg2; font.pixelSize: 13; font.weight: Font.Medium }
                Row {
                    anchors.right: parent.right; height: parent.height
                    Item { width: 44; height: 40; Icon { anchors.centerIn: parent; size: 12; color: theme.fg3; path: "M5 12h14" } }
                    Item {
                        width: 44; height: 40
                        Rectangle { anchors.fill: parent; color: theme.rej; opacity: xh.hovered ? 1 : 0 }
                        Icon { anchors.centerIn: parent; size: 12; color: xh.hovered ? "#ffffff" : theme.fg3; path: "M6 6l12 12M18 6L6 18" }
                        HoverHandler { id: xh; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.close() }
                        Accessible.role: Accessible.Button; Accessible.name: "Close filters"
                    }
                }
            }

            // ---- saved searches ------------------------------------------------------------------------------------------
            Rectangle {
                id: nav; x: 0; y: 40; width: 248; height: parent.height - 98; color: theme.s2
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSoft }
                Item {
                    id: ph; width: parent.width; height: 44
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Text { x: 16; anchors.verticalCenter: parent.verticalCenter; text: "SAVED SEARCHES"; color: theme.fg2; font.pointSize: UiState.pt(11); font.weight: Font.DemiBold; font.letterSpacing: 0.88 }
                }

                readonly property real bottomH: root.saving ? saveBox.height + 2 + 12 : 2 + 34 + 12
                Flickable {
                    id: presetList
                    y: 44; width: parent.width - 1; height: parent.height - 44 - nav.bottomH
                    contentWidth: width; contentHeight: presetCol.height + 6 + 10
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { id: pbar; policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: pbar.active || pbar.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 300 } } } background: Item {} }
                    Column {
                        id: presetCol
                        x: 12; y: 6; width: 223
                        Item { width: 1; height: 31
                            Text { x: 12; y: 12; height: 13; verticalAlignment: Text.AlignVCenter; text: "BUILT IN"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 } }
                        Repeater {
                            model: root.presets.builtin
                            delegate: Item {
                                id: bp
                                required property var modelData
                                readonly property bool on: root.sel === modelData.id
                                width: 223; height: 36
                                Rectangle { anchors.fill: parent; radius: 12
                                    color: bp.on ? Qt.alpha(theme.accent, 0.15) : Qt.alpha(theme.lift, bh.hovered ? 0.05 : 0)
                                    border.width: bp.on ? 1 : 0; border.color: Qt.alpha(theme.accent, 0.30) }
                                Text { x: 12; width: parent.width - 24; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight; text: modelData.name; font.pixelSize: 13
                                       color: bp.on || bh.hovered ? theme.fg : theme.fg2 }
                                HoverHandler { id: bh; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: advFilters.pick(modelData.id) }
                                Accessible.role: Accessible.Button; Accessible.name: modelData.name
                            }
                        }
                        Item { width: 1; height: 31
                            Text { x: 12; y: 12; height: 13; verticalAlignment: Text.AlignVCenter; text: "YOURS"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 } }
                        Repeater {
                            model: root.presets.mine
                            delegate: Item {
                                id: mp
                                required property var modelData
                                readonly property bool on: root.sel === modelData.id
                                readonly property bool asking: root.delAsk === modelData.name
                                width: 223; height: 36
                                Rectangle { anchors.fill: parent; radius: 12
                                    color: mp.asking ? Qt.alpha(theme.rej, 0.12) : (mp.on ? Qt.alpha(theme.accent, 0.15) : Qt.alpha(theme.lift, mh.hovered ? 0.05 : 0))
                                    border.width: mp.asking || mp.on ? 1 : 0; border.color: mp.asking ? Qt.alpha(theme.rej, 0.30) : Qt.alpha(theme.accent, 0.30) }
                                Text { visible: !mp.asking; x: 12; width: parent.width - 12 - 32; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight; text: modelData.name; font.pixelSize: 13
                                       color: mp.on || mh.hovered ? theme.fg : theme.fg2 }
                                Rectangle {
                                    visible: !mp.asking && mh.hovered
                                    x: parent.width - 6 - 26; anchors.verticalCenter: parent.verticalCenter; width: 26; height: 26; radius: 8
                                    color: Qt.alpha(theme.rej, xh2.hovered ? 0.22 : 0)
                                    Icon { anchors.centerIn: parent; size: 14; color: xh2.hovered ? theme.rej : theme.fg3; path: "M6 6l12 12M18 6L6 18" }
                                    HoverHandler { id: xh2; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: root.delAsk = modelData.name }
                                    Accessible.role: Accessible.Button; Accessible.name: "Delete " + modelData.name
                                }
                                Text { visible: mp.asking; x: 12; anchors.verticalCenter: parent.verticalCenter; text: "Delete “" + modelData.name + "”?"; font.pixelSize: 13; color: theme.fg
                                       width: askRow.x - 12 - 6; elide: Text.ElideRight }
                                Row {
                                    id: askRow; visible: mp.asking; anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter; spacing: 4
                                    Rectangle { width: kt.implicitWidth + 18; height: 26; radius: 8; color: theme.s3
                                        Text { id: kt; anchors.centerIn: parent; text: "Keep"; font.pixelSize: 12; color: theme.fg }
                                        TapHandler { onTapped: root.delAsk = "" }
                                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                                        Accessible.role: Accessible.Button; Accessible.name: "Keep" }
                                    Rectangle { width: dt.implicitWidth + 18; height: 26; radius: 8; color: theme.rej
                                        Text { id: dt; anchors.centerIn: parent; text: "Delete"; font.pixelSize: 12; color: "#ffffff" }
                                        TapHandler { onTapped: { advFilters.remove(modelData.name); root.delAsk = "" } }
                                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                                        Accessible.role: Accessible.Button; Accessible.name: "Delete" }
                                }
                                HoverHandler { id: mh; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: if (!mp.asking) advFilters.pick(modelData.id) }
                            }
                        }
                        Item { visible: root.presets.mine.length === 0; width: 1; height: visible ? 31 : 0
                            Text { x: 12; y: 12; height: 13; verticalAlignment: Text.AlignVCenter; text: "Nothing saved yet."; color: theme.fg3; font.pixelSize: 12 } }
                    }
                }

                // name a new saved search, in place of the Widgets app's name prompt
                Rectangle {
                    id: saveBox
                    visible: root.saving
                    x: 12; y: parent.height - 12 - height; width: 223; radius: 12; color: theme.base
                    height: 10 + 15.4 + 8 + 30 + (root.clash ? 8 + warn.implicitHeight : 0) + 8 + 32 + 10
                    border.width: 1; border.color: theme.lineSoft
                    Text { x: 10; y: 10; height: 15.4; verticalAlignment: Text.AlignVCenter; text: "Name this search"; color: theme.fg3; font.pixelSize: 11 }
                    Rectangle {
                        x: 10; y: 10 + 15.4 + 8; width: parent.width - 20; height: 30; radius: 9; color: theme.s2
                        border.width: 1; border.color: nameInput.activeFocus ? theme.accent : theme.lineSoft
                        TextInput {
                            id: nameInput; x: 10; width: parent.width - 20; anchors.verticalCenter: parent.verticalCenter
                            color: theme.fg; font.pixelSize: 13; clip: true; selectByMouse: true; text: root.saveName
                            onTextEdited: root.saveName = text
                            Keys.onReturnPressed: saveGo.clicked()
                            Keys.onEscapePressed: root.saving = false
                            Text { visible: !parent.text; text: "Search name"; color: theme.fg3; font: parent.font }
                            Accessible.name: "Name this search"
                        }
                    }
                    Text { id: warn; visible: root.clash; x: 10; y: 10 + 15.4 + 8 + 30 + 8; width: parent.width - 20; wrapMode: Text.WordWrap
                           text: "A saved search with this name exists. Saving replaces it."; color: theme.rev; font.pointSize: UiState.pt(11.5); lineHeight: 16.1; lineHeightMode: Text.FixedHeight }
                    Row {
                        anchors.right: parent.right; anchors.rightMargin: 10; anchors.bottom: parent.bottom; anchors.bottomMargin: 11; spacing: 6
                        SButton { text: "Cancel"; variant: "ghost"; onClicked: root.saving = false }
                        SButton { id: saveGo; text: root.clash ? "Replace" : "Save"; variant: "primary"
                                  onClicked: { if (advFilters.saveAs(root.saveName)) root.saving = false } }
                    }
                }
                Item {
                    id: addBtn
                    visible: !root.saving
                    x: 12; y: parent.height - 12 - 34; width: 223; height: 34
                    Shape {
                        anchors.fill: parent; preferredRendererType: Shape.GeometryRenderer
                        ShapePath {
                            strokeColor: ah.hovered ? theme.accent : theme.line; strokeWidth: 1; fillColor: "transparent"
                            strokeStyle: ShapePath.DashLine; dashPattern: [3, 3]
                            PathSvg { path: "M12.5 0.5H210.5A12 12 0 0 1 222.5 12.5V21.5A12 12 0 0 1 210.5 33.5H12.5A12 12 0 0 1 0.5 21.5V12.5A12 12 0 0 1 12.5 0.5Z" }
                        }
                    }
                    Row { anchors.centerIn: parent; spacing: 8
                        Icon { size: 16; color: ah.hovered ? theme.accentFg : theme.fg2; path: "M12 5v14M5 12h14"; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Save these filters"; font.pixelSize: 13; color: ah.hovered ? theme.accentFg : theme.fg2; anchors.verticalCenter: parent.verticalCenter } }
                    opacity: advFilters.canSave ? 1 : 0.4
                    HoverHandler { id: ah; cursorShape: Qt.PointingHandCursor }
                    TapHandler { enabled: advFilters.canSave; onTapped: { root.saveName = root.sel.indexOf("m:") === 0 ? root.sel.slice(2) : ""; root.saving = true; nameInput.forceActiveFocus() } }
                    Accessible.role: Accessible.Button; Accessible.name: "Save these filters"
                }
            }

            // ---- page -----------------------------------------------------------------------------------------------------
            Item {
                id: pageArea; x: 248; y: 40; width: parent.width - 248; height: parent.height - 98
                Item {
                    id: mh2; width: parent.width; height: 44
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                    Row {
                        x: 32; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                        Text { height: 24; verticalAlignment: Text.AlignVCenter; text: "Filters"; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold; font.letterSpacing: -0.14 }
                        Text { height: 24; verticalAlignment: Text.AlignVCenter; text: advFilters.filterCount === 0 ? "None set" : advFilters.filterCount + (advFilters.filterCount === 1 ? " filter set" : " filters set"); color: theme.fg3; font.pixelSize: 13 }
                        Rectangle {
                            height: 24; width: pt.implicitWidth + 31; radius: 12; color: theme.s2; border.color: theme.lineSoft; border.width: 1
                            anchors.verticalCenter: parent.verticalCenter
                            Row { anchors.centerIn: parent; spacing: 6
                                Rectangle { width: 7; height: 7; radius: 4; color: advFilters.filterCount > 0 ? theme.accent : theme.win; anchors.verticalCenter: parent.verticalCenter }
                                Text { id: pt; text: advFilters.matchCount + " of " + photos.total + " photos match"; color: theme.fg2; font.pointSize: UiState.pt(11.5) } }
                        }
                    }
                }
                Flickable {
                    id: scroller
                    y: 44; width: parent.width; height: parent.height - 44
                    contentWidth: width; contentHeight: colItem.height + 4 + 28
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { id: sbar; policy: ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Qt.alpha(theme.fg, 0.22); opacity: sbar.active || sbar.hovered ? 1 : 0; Behavior on opacity { NumberAnimation { duration: 300 } } } background: Item {} }
                    Column {
                        id: colItem
                        x: 32; y: 4; width: parent.width - 64

                        // active filters, each removable
                        Item {
                            width: parent.width; height: Math.max(38, 14 + chipFlow.height)
                            Flow {
                                id: chipFlow; x: 4; y: 14; width: parent.width - 8; spacing: 6
                                Repeater { model: root.chips; delegate: FChip { required property var modelData; text: modelData.label; onRemoved: advFilters.clear(modelData.keys.join(",")) } }
                            }
                            Text { visible: root.chips.length === 0; x: 4; y: 14; height: 24; verticalAlignment: Text.AlignVCenter; text: "No filters set. Every photo in the folder shows."; color: theme.fg3; font.pixelSize: 12 }
                        }

                        // ---- file and review
                        Item { width: 1; height: 18 }
                        Item { width: parent.width; height: 16.8 + 6
                            Row { x: 4; spacing: 12
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "FILE AND REVIEW"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "What kind of file, and where it stands in your review."; color: theme.fg3; font.pixelSize: 12 } } }
                        Item { width: 1; height: 14 }
                        Column {
                            x: 4; width: parent.width - 8; spacing: 14
                            Row { spacing: 16
                                Repeater { model: [["quick", "Quick filter"], ["ftype", "File type"]]
                                    delegate: Column { required property var modelData; spacing: 6; width: 312
                                        Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: modelData[1]; color: theme.fg2; font.pixelSize: 12 }
                                        SSelect { width: 312; menuMax: 268; options: { void root.rev; return advFilters.options(modelData[0]) }
                                                  current: { void root.rev; return advFilters.get(modelData[0]) } onPicked: (v) => advFilters.set(modelData[0], v) } } } }
                            Row { spacing: 16
                                Repeater { model: [["review", "Review state"], ["ai", "AI state"]]
                                    delegate: Column { required property var modelData; spacing: 6; width: 312
                                        Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: modelData[1]; color: theme.fg2; font.pixelSize: 12 }
                                        SSelect { width: 312; menuMax: 268; options: { void root.rev; return advFilters.options(modelData[0]) }
                                                  current: { void root.rev; return advFilters.get(modelData[0]) } onPicked: (v) => advFilters.set(modelData[0], v) } } } }
                            Column { spacing: 6; width: parent.width
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "Orientation"; color: theme.fg2; font.pixelSize: 12 }
                                Seg {
                                    width: parent.width; height: 32; fill: true; showExtras: false
                                    labels: ["Any", "Landscape", "Portrait", "Square"]
                                    readonly property var values: ["Any Orientation", "Landscape", "Portrait", "Square"]
                                    current: { void root.rev; return Math.max(0, values.indexOf(advFilters.get("orient"))) }
                                    onActivated: (i) => advFilters.set("orient", values[i])
                                } }
                            Column { spacing: 6; width: 312
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "Minimum rating"; color: theme.fg2; font.pixelSize: 12 }
                                SStepper {
                                    width: 312
                                    text: { void root.rev; var r = advFilters.get("rating"); return r > 0 ? r + " stars or more" : "Any" }
                                    onDecrement: advFilters.set("rating", advFilters.get("rating") - 1)
                                    onIncrement: advFilters.set("rating", advFilters.get("rating") + 1)
                                } }
                        }
                        Item { width: 1; height: 6 }

                        // ---- capture details
                        Item { width: 1; height: 22 }
                        Item { width: parent.width; height: 16.8 + 6
                            Row { x: 4; spacing: 12
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "CAPTURE DETAILS"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "Matches part of the text, ignoring case. Fills in as the metadata index finishes loading."; color: theme.fg3; font.pixelSize: 12 } } }
                        Item { width: 1; height: 14 }
                        Grid {
                            x: 4; width: parent.width - 8; columns: 2; columnSpacing: 16; rowSpacing: 14
                            Repeater {
                                model: [["camera", "Camera", "Sony, Canon, Fujifilm, A7R..."], ["lens", "Lens", "24-70, 50mm, GM, RF..."],
                                        ["tags", "Tags", "portrait, product, wedding..."], ["folder", "Folder", "restaurant, beach, set-b..."]]
                                delegate: Column { required property var modelData; spacing: 6; width: 312
                                    Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: modelData[1]; color: theme.fg2; font.pixelSize: 12 }
                                    FInput { width: 312; placeholder: modelData[2]; text: { void root.rev; return advFilters.get(modelData[0]) }
                                             onEdited: (t) => advFilters.set(modelData[0], t) } }
                            }
                        }
                        Item { width: 1; height: 6 }

                        // ---- date and exposure
                        Item { width: 1; height: 22 }
                        Item { width: parent.width; height: 16.8 + 6
                            Row { x: 4; spacing: 12
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "DATE AND EXPOSURE"; color: theme.fg3; font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84 }
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "Leave a field empty for any value."; color: theme.fg3; font.pixelSize: 12 } } }
                        Item { width: 1; height: 14 }
                        Grid {
                            x: 4; width: parent.width - 8; columns: 2; columnSpacing: 16; rowSpacing: 14
                            Repeater {
                                model: [["Captured", "after", "before", "", true], ["ISO", "isoMin", "isoMax", "", false], ["Focal length", "focMin", "focMax", "mm", false]]
                                delegate: Column { required property var modelData; spacing: 6; width: 312
                                    Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: modelData[0]; color: theme.fg2; font.pixelSize: 12 }
                                    Row { spacing: 10
                                        readonly property real cell: (312 - 20 - toText.implicitWidth) / 2
                                        FInput { width: parent.cell; placeholder: "Any"; suffix: modelData[3]
                                                 text: { void root.rev; return advFilters.get(modelData[1]) }
                                                 error: { void root.rev; return modelData[4] && advFilters.dateBad(modelData[1]) ? "YYYY-MM-DD" : "" }
                                                 onEdited: (t) => advFilters.set(modelData[1], t) }
                                        Text { id: toText; height: 34; verticalAlignment: Text.AlignVCenter; text: "to"; color: theme.fg3; font.pixelSize: 13 }
                                        FInput { width: parent.cell; placeholder: "Any"; suffix: modelData[3]
                                                 text: { void root.rev; return advFilters.get(modelData[2]) }
                                                 error: { void root.rev; return modelData[4] && advFilters.dateBad(modelData[2]) ? "YYYY-MM-DD" : "" }
                                                 onEdited: (t) => advFilters.set(modelData[2], t) } } }
                            }
                            Column { spacing: 6; width: 312
                                Text { height: 16.8; verticalAlignment: Text.AlignVCenter; text: "Minimum search confidence"; color: theme.fg2; font.pixelSize: 12 }
                                Item { width: 312; height: 29.5
                                    SSlider { y: 5; trackW: 312 - 14 - 52; from: 0; to: 1; step: 0.05
                                              value: { void root.rev; return advFilters.get("conf") }
                                              text: { void root.rev; var c = advFilters.get("conf"); return c > 0 ? c.toFixed(2) : "Auto" }
                                              onMoved: (v) => advFilters.set("conf", v) } } }
                        }
                        Item { width: 1; height: 6 }
                    }
                }
            }

            // ---- footer ---------------------------------------------------------------------------------------------------
            Rectangle {
                x: 0; y: parent.height - 58; width: parent.width; height: 58; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                Row {
                    x: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                    Item {
                        width: 8; height: 8; anchors.verticalCenter: parent.verticalCenter
                        Rectangle { anchors.centerIn: parent; width: 16; height: 16; radius: 8; color: Qt.alpha(theme.accent, 0.25); visible: root.dirty }
                        Rectangle { anchors.fill: parent; radius: 4; color: root.dirty ? theme.accent : theme.fg3; opacity: root.dirty ? 1 : 0.5 }
                    }
                    Text {
                        font.pixelSize: 12; width: 650; elide: Text.ElideRight
                        text: (root.dirty ? "Changes are not applied yet." : "These are the filters the grid is using.")
                              + (root.unsupported > 0 ? "  " + root.unsupported + (root.unsupported === 1 ? " filter is" : " filters are") + " saved but not applied by this window yet." : "")
                        color: root.dirty ? theme.fg : theme.fg3
                    }
                }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { text: "Reset"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: advFilters.reset() }
                    SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: root.close() }
                    SButton { text: "Apply"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: root.apply() }
                }
            }
        }
    }
}
