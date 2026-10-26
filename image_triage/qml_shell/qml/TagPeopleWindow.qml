import QtQuick
import QtQuick.Shapes

// Tag People: every recurring face as a card; name them in place, select several to merge them into one person, or ignore them.
Backdrop {
    id: root
    objectName: "tagPeopleWindow"
    shown: UiState.peopleOpen
    z: 106
    readonly property real winW: Math.min(1000, width - 96)
    readonly property real winH: Math.min(700, height - 60 - 36)
    readonly property int rev: people.revision
    readonly property var cards: { void rev; return people.listPeople(UiState.peopleQuery, UiState.peopleFilter === "unnamed", UiState.peopleSingles) }
    readonly property var stats: { void rev; return people.stats() }
    property var sel: []                       // the selected people's ids
    property var hidden: []                    // what the last Ignore hid (for Undo)
    property string mergeName: ""
    readonly property int selPhotos: { void rev; return people.photoCount(sel) }
    readonly property bool mergeNamed: { void rev; for (var i = 0; i < sel.length; ++i) { var c = people.card(sel[i]); if (c.name) return true } return false }
    readonly property bool showUndo: hidden.length > 0 && sel.length === 0
    onShownChanged: {
        if (!shown) return
        sel = UiState.peopleSel !== "" ? UiState.peopleSel.split(",") : []
        hidden = []
        forceActiveFocus()
    }
    Connections { target: UiState; function onPeopleSelChanged() { if (root.shown) root.sel = UiState.peopleSel !== "" ? UiState.peopleSel.split(",") : [] } }
    function close() { UiState.peopleOpen = false; sel = []; hidden = []; mergePrompt.open = false }
    function toggle(id) { var i = sel.indexOf(id); var a = sel.slice(); if (i >= 0) a.splice(i, 1); else a.push(id); sel = a; hidden = [] }
    function merge() {
        if (sel.length < 2) return
        if (mergeNamed) {
            var n = ""
            for (var i = 0; i < sel.length; ++i) { var c = people.card(sel[i]); if (c.name) { n = c.name; break } }
            people.nameAll(sel, n); sel = []
        } else { mergeName = ""; mergePrompt.open = true }
    }
    onDismissed: { if (mergePrompt.open) mergePrompt.open = false; else close() }

    Item {
        id: win
        width: root.winW; height: root.winH
        anchors.centerIn: parent
        scale: root.shown ? 1 : 0.96
        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.4 } }
        Shadow { anchors.fill: parent; radius: 14; offsetA: 48; insetA: 24; blurA: 64; multA: 3; alphaA: 0.85; offsetB: 12; insetB: 12; blurB: 24; alphaB: 0.5 }
        Rectangle { anchors.fill: parent; radius: 14; color: theme.base }
        MouseArea { anchors.fill: parent }
        RoundClip {
            anchors.fill: parent; radius: 14
            // title bar
            Rectangle {
                id: tb; width: parent.width; height: 40; color: theme.s1
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                Row { x: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                    Icon { name: "people"; size: 16; strokeWidth: 1.5; color: theme.fg2; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: "Tag People"; color: theme.fg2; font.pixelSize: 13; font.weight: Font.Medium; anchors.verticalCenter: parent.verticalCenter } }
                Row { anchors.right: parent.right
                    Item { width: 44; height: 40; Icon { anchors.centerIn: parent; name: "winMin"; size: 14; color: theme.fg3 } }
                    Item { width: 44; height: 40
                        Rectangle { anchors.fill: parent; color: ch.hovered ? theme.rej : "transparent" }
                        Icon { anchors.centerIn: parent; name: "winClose"; size: 14; color: ch.hovered ? "white" : theme.fg3 }
                        HoverHandler { id: ch; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.close() }
                        Accessible.role: Accessible.Button; Accessible.name: "Close" } }
            }
            // tool row
            Item {
                id: tool; y: 40; width: parent.width; height: 56
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.lineSoft }
                Text { x: 20; anchors.verticalCenter: parent.verticalCenter; textFormat: Text.StyledText; color: theme.fg2; font.pixelSize: 13
                       text: "<b><font color='" + theme.fg + "'>" + root.stats.named + "</font></b> Named / <b><font color='" + theme.fg + "'>" + root.stats.unnamed + "</font></b> Unnamed / <b><font color='" + theme.fg + "'>" + root.stats.photos + "</font></b> Total" }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 14
                    Rectangle {
                        width: 200; height: 32; radius: 10; color: theme.base; border.color: sin.activeFocus ? theme.accent : theme.lineSoft; anchors.verticalCenter: parent.verticalCenter
                        Row { x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                            Icon { name: "search"; size: 16; strokeWidth: 1.7; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
                            TextInput { id: sin; width: 152; anchors.verticalCenter: parent.verticalCenter; color: theme.fg; font.pixelSize: 13; clip: true; selectByMouse: true
                                text: UiState.peopleQuery; onTextEdited: UiState.peopleQuery = text
                                Text { visible: !parent.text; text: "Search"; color: theme.fg3; font: parent.font }
                                Accessible.name: "Search people" } }
                    }
                    Seg { anchors.verticalCenter: parent.verticalCenter; height: 32; labels: ["All", "Unnamed"]; showExtras: false; padH: 14; current: UiState.peopleFilter === "unnamed" ? 1 : 0
                          onActivated: (i) => UiState.peopleFilter = i === 1 ? "unnamed" : "all" }
                    Row { spacing: 8; anchors.verticalCenter: parent.verticalCenter
                        SSwitch { checked: UiState.peopleSingles; onToggled: (v) => UiState.peopleSingles = v; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Include single-photo faces"; color: theme.fg2; font.pointSize: UiState.pt(12.5); anchors.verticalCenter: parent.verticalCenter } }
                    SButton { text: "Rescan Faces"; h: 28; hpad: 11; radius: 9; anchors.verticalCenter: parent.verticalCenter }
                }
            }
            // the cards
            Flickable {
                id: sc
                y: 96; width: parent.width; height: parent.height - 96 - 58 - (acts.visible ? acts.height : 0)
                contentHeight: grid.height + 36; clip: true; boundsBehavior: Flickable.StopAtBounds
                readonly property int cols: Math.max(1, Math.floor((width - 40 + 12) / 162))
                readonly property real cw: (width - 40 - (cols - 1) * 12) / cols
                Grid {
                    id: grid
                    x: 20; y: 18; columns: sc.cols; columnSpacing: 12; rowSpacing: 12
                    Repeater {
                        model: root.cards
                        delegate: Item {
                            id: pc
                            required property var modelData
                            readonly property bool on: root.sel.indexOf(modelData.id) >= 0
                            readonly property bool named: modelData.name !== ""
                            width: sc.cw; height: 180
                            Rectangle { anchors.fill: parent; radius: 14; color: pc.on ? Qt.tint(theme.s1, Qt.alpha(theme.accent, 0.09)) : (ph.hovered ? theme.s2 : theme.s1) }
                            Rectangle { anchors.fill: parent; radius: 14; color: "transparent"; border.width: pc.on ? 2 : 1; border.color: pc.on ? theme.accent : theme.lineSoft }
                            FaceDisc { x: (parent.width - 88) / 2; y: 14; width: 88; height: 88; hue: pc.modelData.hue; initial: pc.modelData.init; fontSize: 28 }
                            Item {   // the name, or a field to give one
                                x: 12; y: 111; width: parent.width - 24; height: 30
                                Row { visible: pc.named; anchors.centerIn: parent; spacing: 5
                                    Text { text: pc.modelData.name; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; width: Math.min(implicitWidth, pc.width - 24 - 20); elide: Text.ElideRight; anchors.verticalCenter: parent.verticalCenter }
                                    Item { width: 15; height: 15; anchors.verticalCenter: parent.verticalCenter
                                        Rectangle { anchors.fill: parent; radius: 8; color: theme.accent }
                                        Icon { anchors.centerIn: parent; name: "check"; size: 10; strokeWidth: 3; color: theme.onAccent } } }
                                Rectangle { visible: !pc.named; anchors.fill: parent; radius: 8; color: theme.base; border.width: nin.activeFocus ? 1.5 : 1; border.color: nin.activeFocus ? theme.accent : theme.lineSoft
                                    TextInput { id: nin; anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; verticalAlignment: TextInput.AlignVCenter; horizontalAlignment: TextInput.AlignHCenter
                                        color: theme.fg; font.pointSize: UiState.pt(12.5); clip: true; selectByMouse: true
                                        onAccepted: { if (text.trim() !== "") people.name(pc.modelData.id, text) }
                                        Text { visible: !parent.text; anchors.fill: parent; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; text: "Name this person.."; color: theme.fg3; font: parent.font }
                                        Accessible.name: "Name this person" } }
                            }
                            Row { y: 150; anchors.horizontalCenter: parent.horizontalCenter; spacing: 6; height: 15
                                Icon { name: "bars"; size: 14; strokeWidth: 1.8; color: theme.fg3; anchors.verticalCenter: parent.verticalCenter }
                                Text { text: pc.modelData.count + (pc.modelData.count === 1 ? " photo" : " photos"); color: theme.fg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter } }
                            Item { visible: pc.on; x: 8; y: 8; width: 20; height: 20
                                Rectangle { anchors.fill: parent; radius: 10; color: theme.accent }
                                Icon { anchors.centerIn: parent; name: "check"; size: 12; strokeWidth: 3; color: theme.onAccent } }
                            HoverHandler { id: ph; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: root.toggle(pc.modelData.id) }
                            Accessible.role: Accessible.CheckBox; Accessible.checked: pc.on
                            Accessible.name: (pc.named ? pc.modelData.name : "Unnamed person") + ", " + pc.modelData.count + " photos"
                        }
                    }
                }
            }
            Text { visible: root.cards.length === 0; y: 96; width: parent.width; height: sc.height; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; color: theme.fg3; font.pixelSize: 13
                   text: UiState.peopleQuery !== "" ? "No one matches that search." : UiState.peopleFilter === "unnamed" ? "Everyone here has a name." : "No recurring faces found yet." }
            // what to do with the selection (or the undo for the last ignore)
            Rectangle {
                id: acts
                visible: root.sel.length > 0 || root.showUndo
                y: parent.height - 58 - height; width: parent.width; height: 48; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                Text { x: 20; anchors.verticalCenter: parent.verticalCenter; color: theme.fg2; font.pointSize: UiState.pt(12.5)
                       text: root.sel.length > 0 ? root.sel.length + " selected · " + root.selPhotos + " photos" : root.hidden.length + (root.hidden.length === 1 ? " person ignored" : " people ignored") }
                Row { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { visible: root.sel.length > 0; text: "Merge Selected"; h: 28; hpad: 11; radius: 9; enabled: root.sel.length >= 2; onClicked: root.merge() }
                    SButton { visible: root.sel.length > 0; text: "Ignore Selected"; h: 28; hpad: 11; radius: 9; onClicked: { root.hidden = people.ignore(root.sel); root.sel = [] } }
                    SButton { visible: root.sel.length > 0; text: "Clear Selected"; h: 28; hpad: 11; radius: 9; onClicked: { people.clearNames(root.sel); root.sel = [] } }
                    SButton { visible: root.showUndo; text: "Undo"; h: 28; hpad: 11; radius: 9; onClicked: { people.restore(root.hidden); root.hidden = [] } } }
            }
            // footer
            Rectangle {
                y: parent.height - 58; width: parent.width; height: 58; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                SButton { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: "DONE"; variant: "primary"; h: 34; hpad: 24; radius: 12; onClicked: root.close() }
            }
        }
        Rectangle { anchors.fill: parent; anchors.margins: -1; radius: 15; color: "transparent"; border.color: theme.line; z: 10 }
    }

    // "Merge as Same Person": one name for all the selected faces when none of them has one yet
    Item {
        id: mergePrompt
        property bool open: false
        anchors.fill: parent; visible: open; z: 5
        Rectangle { anchors.fill: parent; color: Qt.rgba(0, 0, 0, 0.25) }
        MouseArea { anchors.fill: parent; onClicked: mergePrompt.open = false }
        DialogCard {
            shown: mergePrompt.open
            width: 400; height: mc.implicitHeight + 46
            x: (parent.width - width) / 2; y: 180
            Column { id: mc; x: 23; y: 23; width: parent.width - 46
                Text { height: 20.25; verticalAlignment: Text.AlignVCenter; text: "Merge as Same Person"; color: theme.fg; font.pixelSize: 15; font.weight: Font.DemiBold; font.letterSpacing: -0.15 }
                Item { width: 1; height: 6 }
                Text { text: "Name for this person (" + root.selPhotos + " photos):"; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.75; lineHeightMode: Text.FixedHeight }
                Item { width: 1; height: 12 }
                FInput { id: mname; width: parent.width; placeholder: "Name"; text: root.mergeName; onEdited: (t) => root.mergeName = t }
                Item { width: 1; height: 20 }
                Row { anchors.right: parent.right; spacing: 8
                    SButton { text: "Cancel"; variant: "ghost"; h: 34; hpad: 16; radius: 12; onClicked: mergePrompt.open = false }
                    SButton { text: "Merge"; variant: "primary"; h: 34; hpad: 16; radius: 12; enabled: root.mergeName.trim() !== ""; onClicked: { people.nameAll(root.sel, root.mergeName); root.sel = []; mergePrompt.open = false } } }
            }
        }
        onOpenChanged: if (open) mname.input.forceActiveFocus()
    }
}
