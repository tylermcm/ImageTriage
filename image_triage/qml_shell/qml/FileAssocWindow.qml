import QtQuick
import QtQuick.Controls
import QtQuick.Shapes

// File associations: which photo types open in Image Triage when double-clicked in File Explorer. The model is reference_model.py
// (`reference`, backed by file_associations.py on Windows); this file draws its `view.fa` (mockup: scripts/qml_parity/help.dc.html).
Backdrop {
    id: root
    objectName: "fileAssocWindow"
    readonly property var v: reference.view
    readonly property var f: v.fa || ({ supported: true, groups: [], pct: 0, none: false, title: "", sub: "", canRemove: false })
    shown: v.window === "fa"
    onDismissed: reference.close()
    z: 106

    readonly property real winW: Math.min(780, width - 64)
    readonly property real winH: Math.min(660, height - 72)
    function stateColor(s) { return s === "default" ? theme.win : s === "available" ? theme.accentFg : s === "other" ? theme.rev : theme.fg3 }

    DialogCard {
        id: win
        shown: root.shown
        width: root.winW; height: root.winH
        x: (parent.width - width) / 2; y: 44
        RoundClip {
            anchors.fill: parent; anchors.margins: 1; radius: 15
            Rectangle { anchors.fill: parent; color: Qt.alpha(theme.base, 0.97) }
            RHeader {
                id: hd
                width: parent.width
                title: "File associations"; sub: "Choose which photo types open in Image Triage when you double-click them in File Explorer."
                iconPath: "M6 3h8l4 4v14H6zM14 3v4h4M9 13h6M9 17h6"
                onCloseRequested: reference.close()
            }
            Flickable {
                id: body
                y: hd.height; width: parent.width; height: parent.height - hd.height - 60
                contentWidth: width; contentHeight: (root.f.supported ? sup.height : unsup.height) + 8 + 18; clip: true; boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 6; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: theme.line } background: null }
                Column {
                    id: sup
                    visible: root.f.supported
                    x: 26; y: 8; width: body.width - 52
                    Rectangle {
                        width: parent.width; height: 14 * 2 + 46 > sumText.height + 28 ? 14 * 2 + 46 : sumText.height + 28; radius: 14; color: theme.s1; border.color: theme.lineSoft
                        Item { x: 16; anchors.verticalCenter: parent.verticalCenter; width: 46; height: 46
                            Shape { anchors.fill: parent; preferredRendererType: Shape.CurveRenderer
                                ShapePath { strokeColor: theme.s3; strokeWidth: 5; fillColor: "transparent"; PathAngleArc { centerX: 23; centerY: 23; radiusX: 20.5; radiusY: 20.5; startAngle: 0; sweepAngle: 360 } }
                                ShapePath { strokeColor: theme.win; strokeWidth: 5; fillColor: "transparent"; capStyle: ShapePath.FlatCap
                                            PathAngleArc { centerX: 23; centerY: 23; radiusX: 20.5; radiusY: 20.5; startAngle: -90; sweepAngle: 3.6 * root.f.pct } } }
                            Text { anchors.centerIn: parent; text: root.f.pct + "%"; color: theme.fg; font.pixelSize: 12; font.weight: Font.DemiBold } }
                        Column { id: sumText; x: 16 + 46 + 14; anchors.verticalCenter: parent.verticalCenter; width: parent.width - x - 14 - sumBtn.width - 14
                            Text { width: parent.width; text: root.f.title; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium; lineHeight: 20.15; lineHeightMode: Text.FixedHeight; wrapMode: Text.WordWrap }
                            Text { width: parent.width; topPadding: 2; text: root.f.sub; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 18.1; lineHeightMode: Text.FixedHeight } }
                        SButton { id: sumBtn; anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter; text: "Make available for all types"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: reference.faRegister() }
                    }
                    Repeater {
                        model: root.f.groups
                        delegate: Column {
                            id: grp
                            required property var modelData
                            width: sup.width
                            Item { width: 1; height: 14 }
                            Rectangle {
                                width: parent.width; height: 42 + (grp.modelData.open ? rowsCol.height : 0); radius: 14; color: theme.s1; border.color: theme.lineSoft
                                clip: true
                                Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                                Item { width: parent.width; height: 42
                                    Rectangle { anchors.fill: parent; color: Qt.alpha(theme.lift, gh.hovered ? 0.04 : 0) }
                                    Icon { x: 14; anchors.verticalCenter: parent.verticalCenter; name: "chevR"; size: 15; strokeWidth: 1.9; color: theme.fg3; rotation: grp.modelData.open ? 90 : 0; Behavior on rotation { NumberAnimation { duration: 200 } } }
                                    Text { x: 39; anchors.verticalCenter: parent.verticalCenter; text: grp.modelData.name; color: theme.fg; font.pixelSize: 13; font.weight: Font.Medium }
                                    Text { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; text: grp.modelData.sum; color: theme.fg3; font.pointSize: UiState.pt(12) }
                                    HoverHandler { id: gh; cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: reference.toggleGroup(grp.modelData.key) }
                                    Accessible.role: Accessible.Button; Accessible.name: grp.modelData.name
                                }
                                Column {
                                    id: rowsCol
                                    y: 42; width: parent.width
                                    Repeater {
                                        model: grp.modelData.open ? grp.modelData.rows : []
                                        delegate: Item {
                                            id: rw
                                            required property var modelData
                                            width: rowsCol.width; height: 38
                                            Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                                            Text { x: 14; anchors.verticalCenter: parent.verticalCenter; width: 58; text: rw.modelData.ext; color: theme.fg; font.family: "Consolas"; font.pointSize: UiState.pt(12); font.weight: Font.Medium }
                                            Text { x: 14 + 58 + 12; anchors.verticalCenter: parent.verticalCenter; width: parent.width - x - stRow.width - 12 - 14 - (mk.visible ? mk.width + 12 : 0); elide: Text.ElideRight; text: rw.modelData.name; color: theme.fg2; font.pixelSize: 13 }
                                            Item { id: mk; visible: rw.modelData.canMake; anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; width: mkt.implicitWidth + 20; height: 26
                                                Rectangle { anchors.fill: parent; radius: 8; color: Qt.alpha(theme.lift, mh.hovered ? 0.14 : 0.08) }
                                                Text { id: mkt; anchors.centerIn: parent; text: "Make default"; color: theme.fg; font.pointSize: UiState.pt(12) }
                                                HoverHandler { id: mh; cursorShape: Qt.PointingHandCursor }
                                                TapHandler { onTapped: reference.faMakeDefault(rw.modelData.ext) }
                                                Accessible.role: Accessible.Button; Accessible.name: "Make " + rw.modelData.ext + " default" }
                                            Row { id: stRow; anchors.right: mk.visible ? mk.left : parent.right; anchors.rightMargin: mk.visible ? 12 : 14; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                                                Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 7; height: 7; radius: 4; color: root.stateColor(rw.modelData.state) }
                                                Text { text: rw.modelData.text; color: theme.fg2; font.pointSize: UiState.pt(11.5); font.weight: Font.Medium } }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Item { width: 1; height: 14 }
                    Rectangle { width: parent.width; height: noteT.implicitHeight + 24; radius: 12; color: theme.s1; border.color: theme.lineSoft
                        Text { id: noteT; x: 14; y: 12; width: parent.width - 28; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(12.5); lineHeight: 19.4; lineHeightMode: Text.FixedHeight
                               text: "Windows decides the default app, so we can only ask: “Make default” opens the Windows chooser for that type. “Open with” always lists Image Triage once a type is available." } }
                }
                Column {
                    id: unsup
                    visible: !root.f.supported; x: 26; y: 8; width: body.width - 52; topPadding: 48
                    Rectangle { x: (parent.width - 56) / 2; width: 56; height: 56; radius: 28; color: theme.s2; border.color: theme.lineSoft
                        Icon { anchors.centerIn: parent; path: "M6 3h8l4 4v14H6zM14 3v4h4"; size: 26; strokeWidth: 1.6; color: theme.fg3 } }
                    Text { width: parent.width; topPadding: 12; horizontalAlignment: Text.AlignHCenter; text: "Managed by your system"; color: theme.fg; font.pixelSize: 15; font.weight: Font.Medium }
                    Text { x: (parent.width - 380) / 2; width: 380; topPadding: 4; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: theme.fg3; font.pointSize: UiState.pt(13); lineHeight: 20.15; lineHeightMode: Text.FixedHeight
                           text: "On this computer, file associations are set in your system’s settings, not here. Use “Open with” on a photo to pick Image Triage." }
                }
            }
            Rectangle {
                y: parent.height - 60; width: parent.width; height: 60; color: theme.s1
                Rectangle { width: parent.width; height: 1; color: theme.lineSoft }
                Row { x: 26; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                    SButton { visible: root.f.supported; text: "Windows Default Apps…"; h: 34; hpad: 16; radius: 12; onClicked: reference.faDefaultApps() }
                    SButton { visible: root.f.supported; text: "Remove all"; h: 34; hpad: 16; radius: 12; enabled: root.f.canRemove; onClicked: reference.faRemove() } }
                SButton { anchors.right: parent.right; anchors.rightMargin: 20; anchors.verticalCenter: parent.verticalCenter; text: "Done"; variant: "primary"; h: 34; hpad: 16; radius: 12; onClicked: reference.close() }
            }
        }
    }
}
