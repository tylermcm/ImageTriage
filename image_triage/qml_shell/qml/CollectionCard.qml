import QtQuick

Item {
    id: card
    required property var collection
    property bool selected: false
    property bool dropActive: false
    readonly property var previews: collection.previewThumbs || []
    readonly property real deckHeight: width * 0.76
    implicitHeight: info.y + info.implicitHeight + 9
    signal activated()
    signal menuRequested()

    Rectangle {
        anchors.fill: parent; radius: 12
        color: card.selected || card.dropActive ? Qt.alpha(theme.accent, 0.10) : hover.hovered ? theme.s3 : Qt.alpha(theme.base, 0.48)
        border.color: card.selected || card.dropActive ? Qt.alpha(theme.accent, 0.65) : theme.lineSoft
        Behavior on color { ColorAnimation { duration: 140 } }
    }
    Repeater {
        model: 3
        delegate: Rectangle {
            id: printCard
            required property int index
            readonly property int photoIndex: 2 - index
            readonly property string photoPath: card.previews.length > photoIndex ? card.previews[photoIndex] : ""
            width: card.width - 34; height: width * 0.75
            x: (card.width - width) / 2 + (index === 0 ? -7 : index === 1 ? 7 : 0)
            y: index === 2 ? 27 : 15
            rotation: index === 0 ? -10 : index === 1 ? 9 : 0
            radius: 6; color: theme.s3
            border.color: index === 2 ? Qt.alpha(theme.fg, 0.18) : theme.line
            opacity: index === 0 ? 0.65 : index === 1 ? 0.85 : 1
            RoundClip {
                anchors.fill: parent; anchors.margins: 3; radius: 4
                ThumbImage { anchors.fill: parent; path: printCard.photoPath; bucket: 160 }
            }
            Icon {
                visible: printCard.index === 2 && printCard.photoPath === ""
                anchors.centerIn: parent; name: "imagePlaceholder"; size: 40; color: theme.fg3
            }
        }
    }
    Column {
        id: info
        x: 10; y: card.deckHeight + 4; width: parent.width - 20; spacing: 4
        Text {
            objectName: "collectionCardName"
            width: parent.width
            text: card.collection.name; color: theme.fg; font.pixelSize: 14; font.weight: Font.DemiBold
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
        }
        Row {
            spacing: 5
            CollMark { color: card.collection.color; iconPath: card.collection.iconPath; ring: 10; iconSize: 11; anchors.verticalCenter: parent.verticalCenter }
            Text { text: card.collection.count + (card.collection.count === 1 ? " photo" : " photos"); color: theme.fg2; font.pixelSize: 12 }
        }
        Rectangle {
            visible: card.collection.active || card.dropActive
            width: badge.implicitWidth + 12; height: 19; radius: 5
            color: Qt.alpha(theme.accent, 0.13)
            Text { id: badge; anchors.centerIn: parent; text: card.dropActive ? "Drop to add" : "New winners"; color: theme.accentFg; font.pixelSize: 11 }
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: card.activated() }
    TapHandler { acceptedButtons: Qt.RightButton; onTapped: card.menuRequested() }
    Item {
        objectName: "collectionCardMenu"
        x: parent.width - width - 5; y: 5; width: 24; height: 24
        Rectangle { anchors.fill: parent; radius: 7; color: moreHover.hovered ? theme.s3 : Qt.alpha(theme.base, 0.9); border.color: theme.lineSoft }
        Icon { anchors.centerIn: parent; name: "more"; size: 16; color: moreHover.hovered ? theme.fg : theme.fg2 }
        HoverHandler { id: moreHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { gesturePolicy: TapHandler.WithinBounds; onTapped: card.menuRequested() }
        Accessible.role: Accessible.Button; Accessible.name: "More for " + card.collection.name
    }
    Accessible.role: Accessible.Button
    Accessible.name: collection.name + ", " + collection.count + " photos"
}
