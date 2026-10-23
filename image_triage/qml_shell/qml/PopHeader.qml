import QtQuick

Text {
    property string caption
    text: caption.toUpperCase()
    color: theme.fg3
    font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.8
    leftPadding: 10; topPadding: 6; bottomPadding: 4
}
