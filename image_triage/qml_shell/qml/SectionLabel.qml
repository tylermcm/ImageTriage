import QtQuick

Text {
    property string caption
    text: caption.toUpperCase()
    color: theme.fg3
    font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 0.8
    leftPadding: 16; topPadding: 14; bottomPadding: 6
}
