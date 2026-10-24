import QtQuick
import QtQuick.Shapes

// New / Edit Collection: a name, a mark (a colour ring or a library icon with a tint), "New winners go here" and the hints table.
Backdrop {
    id: root
    objectName: "collectionDialog"
    shown: UiState.collDlg !== ""
    readonly property bool editing: UiState.collDlg === "edit"
    property string name: ""
    property string color: ""
    property string icon: ""
    property bool winners: false
    property int tab: 0                                  // 0 colour, 1 icon
    readonly property var colors: collections.colors()
    readonly property var icons: collections.icons()
    readonly property string iconPath: icon !== "" ? collections.iconPathOf(icon) : ""
    readonly property string problem: name.trim() !== "" ? collections.nameProblem(name, UiState.collDlgId) : ""
    readonly property bool valid: name.trim() !== "" && problem === ""
    readonly property string previewText: {
        var cname = "No color"
        for (var i = 0; i < colors.length; ++i) if (colors[i].value === color) cname = colors[i].label
        if (icon === "") return cname
        var lbl = ""
        for (var j = 0; j < icons.length; ++j) if (icons[j].key === icon) lbl = icons[j].label
        return lbl + (color !== "" ? " · " + cname : "")
    }

    function load() {
        if (editing) {
            var c = collections.get(UiState.collDlgId)
            name = c.name || ""; color = c.color || ""; icon = c.icon || ""; winners = c.id === collections.activeId
        } else {
            name = UiState.collName; color = ""; icon = ""; winners = true
        }
        tab = UiState.collDlgTab === "icon" || icon !== "" ? 1 : 0
        if (tab === 1 && icon === "") icon = "mountain"
        nameField.input.cursorPosition = name.length
    }
    onShownChanged: { if (shown) { load(); forceActiveFocus() } }
    Connections { target: UiState; function onCollDlgIdChanged() { if (root.shown) root.load() }
                                   function onCollNameChanged() { if (root.shown && !root.editing) root.load() } }
    function close() { UiState.collDlg = ""; UiState.collName = "" }
    function commit() {
        if (!valid) return
        var id = collections.save(editing ? UiState.collDlgId : "", name, color, icon, winners)
        var pickAfter = UiState.collPicker
        close()
        if (pickAfter) { UiState.collPicker = false; collections.addVisible(id) }
    }
    onDismissed: close()
    Keys.onReturnPressed: commit()

    component Lbl: Text { height: 16.8; verticalAlignment: Text.AlignVCenter; color: theme.fg2; font.pixelSize: 12 }
    component Swatch: Item {
        id: sw
        property string value; property real ring: 18; property real cell: 30; property bool on: false
        signal picked()
        width: cell; height: cell
        Rectangle { anchors.fill: parent; radius: 9; color: sw.on ? theme.s3 : Qt.alpha(theme.lift, sh.hovered ? 0.08 : 0); border.width: sw.on ? 1 : 0; border.color: theme.line }
        CollMark { anchors.centerIn: parent; color: sw.value; ring: sw.ring }
        HoverHandler { id: sh; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: sw.picked() }
        Accessible.role: Accessible.Button; Accessible.checked: sw.on
    }

    DialogCard {
        id: card
        shown: root.shown
        width: 440; height: col.implicitHeight + 46
        x: (parent.width - width) / 2; y: 56
        Column {
            id: col
            x: 23; y: 23; width: parent.width - 46
            Text { width: parent.width; height: 20.25; verticalAlignment: Text.AlignVCenter; text: root.editing ? "Edit Collection" : "New Collection"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15 }
            Item { width: 1; height: 6 }
            Text { width: parent.width; wrapMode: Text.WordWrap; text: "Collections reference images where they already live; no files are moved or duplicated."; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
            Item { width: 1; height: 16 }
            Lbl { text: "Name" }
            Item { width: 1; height: 6 }
            FInput { id: nameField; width: parent.width; placeholder: "e.g. Utah Portfolio"; text: root.name; border.color: root.problem !== "" ? theme.rej : (input.activeFocus ? theme.accent : theme.lineSoft)
                     onEdited: (t) => root.name = t }
            Text { visible: root.problem !== ""; width: parent.width; topPadding: 6; text: root.problem; color: theme.rej; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
            Item { width: 1; height: 16 }
            Item {
                width: parent.width; height: 24
                Lbl { anchors.verticalCenter: parent.verticalCenter; text: "Mark" }
                Rectangle {
                    anchors.right: parent.right; height: 24; radius: 12; width: pv.implicitWidth + 18; color: theme.base; border.color: theme.lineSoft
                    Row { id: pv; x: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                        CollMark { anchors.verticalCenter: parent.verticalCenter; color: root.color; iconPath: root.iconPath; ring: 14; iconSize: 14 }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: root.previewText; color: theme.fg2; font.pixelSize: 12 } }
                }
            }
            Item { width: 1; height: 8 }
            Seg { width: parent.width; height: 32; labels: ["Color", "Icon"]; fill: true; current: root.tab; showExtras: false
                  onActivated: (i) => { root.tab = i; if (i === 0) root.icon = ""; else if (root.icon === "") root.icon = "mountain" } }
            // colour tab
            Item { visible: root.tab === 0; width: 1; height: 8 }
            Row { visible: root.tab === 0; spacing: 6
                Repeater { model: root.colors; delegate: Swatch { required property var modelData; value: modelData.value; on: root.color === modelData.value
                                                                  onPicked: root.color = modelData.value
                                                                  Accessible.name: modelData.label } }
                SButton { text: "More…"; h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter }
            }
            // icon tab
            Item { visible: root.tab === 1; width: 1; height: 8 }
            Grid { visible: root.tab === 1; columns: 8; rowSpacing: 4; columnSpacing: 4; width: parent.width
                Repeater { model: root.icons
                    delegate: Item {
                        id: tile
                        required property var modelData
                        readonly property bool on: root.icon === modelData.key
                        width: (col.width - 28) / 8; height: 40
                        Rectangle { anchors.fill: parent; radius: 10; color: tile.on ? theme.s3 : Qt.alpha(theme.lift, th.hovered ? 0.08 : 0); border.width: tile.on ? 1.5 : 0; border.color: theme.accent }
                        Icon { anchors.centerIn: parent; size: 20; strokeWidth: 1.7; path: tile.modelData.path; color: tile.on || th.hovered ? theme.fg : theme.fg2 }
                        HoverHandler { id: th; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.icon = tile.modelData.key }
                        Accessible.role: Accessible.Button; Accessible.name: tile.modelData.label; Accessible.checked: tile.on
                    }
                }
                Item {   // "+": bring your own icon (not connected yet)
                    id: addTile
                    width: (col.width - 28) / 8; height: 40
                    Rectangle { anchors.fill: parent; radius: 10; color: Qt.alpha(theme.lift, ah.hovered ? 0.08 : 0) }
                    Shape { anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
                        ShapePath { strokeColor: theme.line; strokeWidth: 1; fillColor: "transparent"; strokeStyle: ShapePath.DashLine; dashPattern: [3, 3]; PathSvg { path: Icons.rect(0.5, 0.5, addTile.width - 1, 39, 10) } } }
                    Icon { anchors.centerIn: parent; name: "plus"; size: 20; strokeWidth: 1.7; color: theme.fg3 }
                    HoverHandler { id: ah; cursorShape: Qt.PointingHandCursor }
                    Accessible.role: Accessible.Button; Accessible.name: "Add your own icon"
                }
            }
            Item { visible: root.tab === 1; width: 1; height: 9 }
            Row { visible: root.tab === 1; spacing: 8; height: 26
                Lbl { anchors.verticalCenter: parent.verticalCenter; text: "Tint" }
                Repeater { model: root.colors; delegate: Swatch { required property var modelData; value: modelData.value; ring: 15; cell: 26; on: root.color === modelData.value
                                                                  onPicked: root.color = modelData.value
                                                                  Accessible.name: modelData.label } }
            }
            Item { width: 1; height: 16 }
            Item {
                width: parent.width; height: 36
                Column { width: parent.width - 60; spacing: 1
                    Text { text: "New winners go here"; color: theme.fg; font.pixelSize: 13; height: 18 }
                    Text { text: "Every winner you mark with W also goes in."; color: theme.fg3; font.pixelSize: 12; height: 17 } }
                SSwitch { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; checked: root.winners; onToggled: (v) => root.winners = v }
            }
            Item { width: 1; height: 16 }
            Rectangle {
                id: hints
                width: parent.width; height: hcol.implicitHeight + 20; radius: 10; color: theme.base; border.color: theme.lineSoft
                Column { id: hcol; x: 12; y: 10; width: parent.width - 24; spacing: 5
                    Repeater {
                        model: [["W", root.winners ? "Mark a winner and it goes in" : "Marks a winner only, unless New winners go here is on"],
                                ["B", "Add the photo you’re on without marking it a winner"],
                                ["Add All bar", "Filter or select photos, then Add All to Collection above the grid"],
                                ["Drag", "Drop photos onto it in the sidebar"], ["Right-click", "On a photo: Collections, then tick it"]]
                        delegate: Row {
                            required property var modelData
                            spacing: 12
                            Text { width: 76; text: modelData[0]; color: theme.fg2; font.pixelSize: 12; font.weight: Font.DemiBold; lineHeight: 16.8; lineHeightMode: Text.FixedHeight }
                            Text { width: hcol.width - 88; text: modelData[1]; color: theme.fg3; font.pixelSize: 12; wrapMode: Text.WordWrap; lineHeight: 16.8; lineHeightMode: Text.FixedHeight }
                        }
                    }
                }
            }
            Item { width: 1; height: 20 }
            Row { anchors.right: parent.right; spacing: 8
                SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: root.close() }
                SButton { text: root.editing ? "Save" : "Create"; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: root.valid; onClicked: root.commit() }
            }
        }
    }
}
