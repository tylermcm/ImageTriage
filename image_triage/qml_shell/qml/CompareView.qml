import QtQuick

// Compare: two to four photos side by side in the viewer's stage (compare_model.py, `popout.compare`). The panes share one zoom and one
// place, both relative to the photo (zoom 1 fits each photo to its pane; the place is a fraction of the photo), so every pane shows the
// same part of its photo whatever its size in pixels. The active pane's photo is the viewer's current photo: the viewer's own keys and
// buttons decide and rate it. Viewer.qml hands the keys that mean something else here to key().
Item {
    id: cmp
    objectName: "compareView"
    property var model                                   // popout.compare
    readonly property var panes: { void model.revision; return model.active ? model.panes : [] }
    readonly property int count: panes.length
    readonly property int activeIndex: model.activeIndex
    readonly property real dpr: Screen.devicePixelRatio

    // ---- the shared view ----
    property real zoom: 1                                // 1 = each photo fitted to its pane
    property real cx: 0.5                                // the middle of what is shown, as a fraction of the photo
    property real cy: 0.5
    readonly property bool zoomed: zoom > 1.001
    readonly property real maxZoom: 64
    // the scale of the active pane's photo on screen (1 = one photo pixel per screen pixel), for the percentage beside Fit and 100%
    readonly property real activeFit: { var p = paneRepeater.count > activeIndex ? paneRepeater.itemAt(activeIndex) : null; return p ? p.fit : 1 }
    readonly property real activeScale: activeFit * zoom
    readonly property bool atActual: Math.abs(activeScale - 1) < 0.001
    function fit() { zoom = 1; cx = 0.5; cy = 0.5 }
    function actual() { zoom = Math.max(1, Math.min(maxZoom, 1 / Math.max(0.0001, activeFit))) }      // 100% of the active pane's photo
    function toggleActual() { if (zoomed && atActual) fit(); else actual() }
    function setCentre(x, y) { cx = Math.max(0, Math.min(1, x)); cy = Math.max(0, Math.min(1, y)) }
    // zoom by `factor`, keeping the place (fx, fy) of the photo at the offset (ox, oy) from a pane's middle, as fractions of the drawn photo
    function zoomAbout(factor, fx, fy, ox, oy) {
        var next = Math.max(1, Math.min(maxZoom, zoom * factor))
        if (next === zoom) return
        var ratio = zoom / next
        zoom = next
        if (next <= 1.001) { cx = 0.5; cy = 0.5 } else setCentre(fx - ox * ratio, fy - oy * ratio)
    }
    Connections { target: cmp.model; function onChanged() { if (!cmp.model.active) cmp.fit() } }

    // The keys that mean something else while comparing; true when the key was taken.
    function key(k, modifiers) {
        if (k === Qt.Key_Escape || k === Qt.Key_C) model.close()
        else if (k === Qt.Key_Left || k === Qt.Key_A) model.moveActive(-1)
        else if (k === Qt.Key_Right || k === Qt.Key_D) model.moveActive(1)
        else if (k === Qt.Key_N || k === Qt.Key_Down || k === Qt.Key_PageDown) model.next()
        else if (k === Qt.Key_P || k === Qt.Key_Up || k === Qt.Key_PageUp) model.back()
        else if (k === Qt.Key_Z) toggleActual()
        else if (k === Qt.Key_0) fit()
        else if (k === Qt.Key_Plus || k === Qt.Key_Equal) zoomAbout(1.25, cx, cy, 0, 0)
        else if (k === Qt.Key_Minus) zoomAbout(0.8, cx, cy, 0, 0)
        else if (k === Qt.Key_Home || k === Qt.Key_End || k === Qt.Key_F) return true      // stepping through the folder and focus assist are the single view's
        else return false
        return true
    }

    // ---- the bar above the panes: how many panes, what is left, the keys ----
    Item {
        id: head
        objectName: "compareBar"
        width: parent.width; height: 34
        Row {
            anchors.verticalCenter: parent.verticalCenter; spacing: 10
            Text { anchors.verticalCenter: parent.verticalCenter; text: "Compare"; color: theme.fg; font.pixelSize: 13; font.weight: Font.DemiBold }
            Item {
                anchors.verticalCenter: parent.verticalCenter; width: countRow.implicitWidth; height: 28
                Rectangle { anchors.fill: parent; radius: 8; color: theme.s1; border.color: theme.lineSoft }
                Row { id: countRow; height: parent.height
                    Repeater {
                        model: [2, 3, 4]
                        delegate: Item {
                            id: cb
                            required property int modelData
                            objectName: "comparePanes" + modelData
                            readonly property bool on: cmp.count === modelData
                            width: 30; height: 28
                            Rectangle { anchors.fill: parent; anchors.margins: 1; radius: 7; color: cb.on ? Qt.alpha(theme.accent, 0.22) : cbh.hovered ? theme.s3 : "transparent" }
                            Text { anchors.centerIn: parent; text: cb.modelData; color: cb.on ? theme.accentFg : theme.fg2; font.pixelSize: 12; font.weight: Font.Medium }
                            HoverHandler { id: cbh; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: cmp.model.setCount(cb.modelData) }
                            Accessible.role: Accessible.RadioButton; Accessible.checked: cb.on; Accessible.name: cb.modelData + " photos side by side"
                            Accessible.onPressAction: cmp.model.setCount(cb.modelData)
                        }
                    }
                }
            }
            Item {   // swap the other panes for the next photos
                id: nextBtn
                objectName: "compareNextButton"
                anchors.verticalCenter: parent.verticalCenter; width: nextRow.implicitWidth + 20; height: 28
                opacity: cmp.model.remaining > 0 ? 1 : 0.45
                Rectangle { anchors.fill: parent; radius: 8; color: nbh.hovered ? theme.s3 : theme.s2; border.color: theme.line }
                Row { id: nextRow; x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                    Text { anchors.verticalCenter: parent.verticalCenter; color: theme.fg; font.pixelSize: 12; font.weight: Font.Medium
                        text: cmp.model.remaining > 0 ? "Next · " + cmp.model.remaining + " more" : "No more photos" }
                    Kbd { anchors.verticalCenter: parent.verticalCenter; text: "N" } }
                HoverHandler { id: nbh; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: cmp.model.next() }
                Accessible.role: Accessible.Button; Accessible.name: "Swap the other photos for the next ones. The active photo stays."
                Accessible.onPressAction: cmp.model.next()
            }
        }
        Row {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 10
            visible: head.width >= 760
            Row { spacing: 4; Kbd { text: "←" } Kbd { text: "→" } Text { text: "pane"; color: theme.fg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter; leftPadding: 2 } }
            Row { spacing: 4; Kbd { text: "P" } Text { text: "back"; color: theme.fg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter; leftPadding: 2 } }
            Row { spacing: 4; Kbd { text: "Esc" } Text { text: "leave"; color: theme.fg3; font.pointSize: UiState.pt(11.5); anchors.verticalCenter: parent.verticalCenter; leftPadding: 2 } }
        }
    }

    // ---- the panes: side by side, four as two rows of two ----
    Item {
        id: area
        y: head.height + 6; width: parent.width; height: parent.height - y
        readonly property int columns: cmp.count === 4 ? 2 : Math.max(1, cmp.count)
        readonly property int rows: cmp.count === 4 ? 2 : 1
        readonly property real gap: 8
        readonly property real cellW: (width - gap * (columns - 1)) / columns
        readonly property real cellH: (height - gap * (rows - 1)) / rows
        Repeater {
            id: paneRepeater
            model: cmp.count                             // a count, not the list: a decision or a rating must not rebuild the panes
            delegate: Item {
                id: pane
                required property int index
                objectName: "comparePane" + index
                readonly property var d: cmp.panes[index] || ({})
                readonly property bool isActive: index === cmp.activeIndex
                readonly property color tone: !theme ? "transparent" : d.decision === "win" ? theme.win : d.decision === "keep" ? theme.keep : d.decision === "rej" ? theme.rej : "transparent"
                // the photo in this pane: its size, how much of it fits, and where it is drawn for the shared zoom and place
                readonly property real nw: Math.max(1, d.w || 0) / cmp.dpr
                readonly property real nh: Math.max(1, d.h || 0) / cmp.dpr
                readonly property real fit: Math.min(view.width / nw, view.height / nh)
                readonly property real drawW: Math.max(1, nw * fit * cmp.zoom)
                readonly property real drawH: Math.max(1, nh * fit * cmp.zoom)
                readonly property real imageX: drawW <= view.width ? (view.width - drawW) / 2 : Math.max(view.width - drawW, Math.min(0, view.width / 2 - cmp.cx * drawW))
                readonly property real imageY: drawH <= view.height ? (view.height - drawH) / 2 : Math.max(view.height - drawH, Math.min(0, view.height / 2 - cmp.cy * drawH))
                // the place in the photo at this pane's middle, after the edges stop it (what the tests read)
                readonly property real shownX: (view.width / 2 - imageX) / drawW
                readonly property real shownY: (view.height / 2 - imageY) / drawH
                x: (index % area.columns) * (area.cellW + area.gap); y: Math.floor(index / area.columns) * (area.cellH + area.gap)
                width: area.cellW; height: area.cellH

                Rectangle { anchors.fill: parent; radius: 10; color: theme.base; border.width: pane.isActive ? 2 : 1; border.color: pane.isActive ? theme.accent : theme.lineSoft }
                Item {
                    id: view
                    anchors.fill: parent; anchors.margins: 3; anchors.bottomMargin: foot.height + 3
                    clip: true
                    ThumbImage {   // the grid's thumbnail until the picture is read
                        anchors.fill: parent; path: pane.d.path || ""; bucket: 800; fillMode: Image.PreserveAspectFit; fadeIn: false
                        visible: photo.status !== Image.Ready && !cmp.zoomed
                    }
                    Image {
                        id: photo
                        objectName: "comparePhoto" + pane.index
                        x: pane.imageX; y: pane.imageY; width: pane.drawW; height: pane.drawH
                        source: pane.d.src || ""
                        // fitted, the picture is decoded no larger than twice the pane; zoomed in, in full
                        sourceSize: cmp.zoomed ? undefined : Qt.size(Math.ceil(view.width * cmp.dpr * 2), Math.ceil(view.height * cmp.dpr * 2))
                        asynchronous: true; cache: false; autoTransform: true; retainWhileLoading: true
                        fillMode: Image.PreserveAspectFit; smooth: true; mipmap: pane.fit * cmp.zoom < 1
                    }
                    Text {
                        visible: (pane.d.error || "") !== ""; anchors.centerIn: parent; width: parent.width - 32; horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap; text: pane.d.error || ""; color: theme.fg2; font.pixelSize: 13
                    }
                    Text {
                        visible: pane.d.loading === true; anchors.horizontalCenter: parent.horizontalCenter; y: parent.height - height - 10
                        text: "Loading full resolution…"; color: theme.fg; font.pixelSize: 12; style: Text.Outline; styleColor: Qt.rgba(0, 0, 0, 0.6)
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: cmp.zoomed ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor) : Qt.ArrowCursor
                        property real lastX: 0; property real lastY: 0
                        onPressed: (m) => { lastX = m.x; lastY = m.y; cmp.model.setActive(pane.index) }
                        onPositionChanged: (m) => {
                            if (!pressed || !cmp.zoomed) return
                            // drag the photo; the place is taken from where this pane really is, so an edge does not leave slack to drag back through
                            cmp.setCentre(pane.shownX - (m.x - lastX) / pane.drawW, pane.shownY - (m.y - lastY) / pane.drawH)
                            lastX = m.x; lastY = m.y
                        }
                        onDoubleClicked: cmp.toggleActual()
                        onWheel: (w) => {
                            w.accepted = true
                            var steps = w.pixelDelta.y !== 0 ? w.pixelDelta.y / 60 : w.angleDelta.y / 120
                            if (steps === 0) return
                            var ox = (w.x - view.width / 2) / pane.drawW, oy = (w.y - view.height / 2) / pane.drawH
                            cmp.zoomAbout(Math.pow(1.2, steps), pane.shownX + ox, pane.shownY + oy, ox, oy)
                        }
                    }
                }
                // name, decision and rating
                Item {
                    id: foot
                    x: 3; y: parent.height - height - 3; width: parent.width - 6; height: 34
                    Rectangle { anchors.fill: parent; radius: 8; color: pane.isActive ? theme.s2 : theme.s1 }
                    Text {
                        objectName: "compareName" + pane.index
                        x: 10; anchors.verticalCenter: parent.verticalCenter; width: Math.max(20, decisionPill.x - x - 8); elide: Text.ElideMiddle
                        text: pane.d.name || ""; color: pane.isActive ? theme.fg : theme.fg2; font.pixelSize: 12; font.weight: pane.isActive ? Font.DemiBold : Font.Normal
                    }
                    Item {
                        id: decisionPill
                        objectName: "compareDecision" + pane.index
                        readonly property string text: pane.d.decisionName || ""
                        anchors.verticalCenter: parent.verticalCenter; x: stars.x - width - 8; width: decisionRow.implicitWidth + 16; height: 22
                        Rectangle { anchors.fill: parent; radius: 7; color: (pane.d.decision || "") !== "" ? Qt.tint(theme.base, Qt.alpha(pane.tone, 0.24)) : "transparent"
                            border.color: (pane.d.decision || "") !== "" ? Qt.alpha(pane.tone, 0.7) : theme.lineSoft }
                        Row { id: decisionRow; x: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 5
                            Rectangle { visible: (pane.d.decision || "") !== ""; anchors.verticalCenter: parent.verticalCenter; width: 7; height: 7; radius: 3.5; color: pane.tone }
                            Text { anchors.verticalCenter: parent.verticalCenter; text: decisionPill.text; color: (pane.d.decision || "") !== "" ? theme.fg : theme.fg3; font.pointSize: UiState.pt(11) } }
                    }
                    Row {
                        id: stars
                        objectName: "compareRating" + pane.index
                        readonly property int rating: pane.d.rating || 0
                        anchors.verticalCenter: parent.verticalCenter; x: parent.width - width - 8; spacing: 0
                        visible: foot.width >= 260
                        Repeater {
                            model: 5
                            delegate: Item {
                                id: star
                                required property int index
                                readonly property bool on: stars.rating >= index + 1
                                width: 18; height: 22
                                Icon { anchors.centerIn: parent; path: "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8 6.8 19.6l1-5.8-4.3-4.1 5.9-.8z"; size: 13
                                    color: star.on || starHover.hovered ? theme.star : theme.fg3; filled: star.on }
                                HoverHandler { id: starHover; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: popout.rate(pane.d.row, star.index + 1) }
                                Accessible.role: Accessible.Button; Accessible.name: (star.index + 1) + (star.index === 0 ? " star" : " stars") + " for " + (pane.d.name || "")
                                Accessible.onPressAction: popout.rate(pane.d.row, star.index + 1)
                            }
                        }
                    }
                }
                Accessible.role: Accessible.Button
                Accessible.name: (pane.d.name || "") + ", " + (pane.d.decisionName || "") + ", " + (pane.d.rating || 0) + " of 5 stars" + (pane.isActive ? ", active" : "")
                Accessible.onPressAction: cmp.model.setActive(pane.index)
            }
        }
    }
}
