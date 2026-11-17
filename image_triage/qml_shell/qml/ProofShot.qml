import QtQuick
Rectangle { width: 1000; height: 640; color: theme.base
  Image { x: 10; y: 10; width: 600; height: 400; fillMode: Image.PreserveAspectFit; source: rawModel.preview }
  RawEditorPanel { id: p; x: 630; width: 360; height: 640; model: rawModel }
  Component.onCompleted: p.findMenu()
}