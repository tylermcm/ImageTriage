import QtQuick

// Small caps heading of a panel block: 10.5px, 600, 0.08em, 13px line; 14px above (collapsed margin) and 6px below.
Item {
    id: sl
    property string caption
    width: parent ? parent.width : 280
    height: 14 + 13 + 6
    Text {
        x: 16; y: 14; height: 13
        verticalAlignment: Text.AlignVCenter
        text: sl.caption.toUpperCase()
        color: theme.fg3
        font.pointSize: UiState.pt(10.5); font.weight: Font.DemiBold; font.letterSpacing: 0.84
    }
}
