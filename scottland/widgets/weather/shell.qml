import QtQuick
import Quickshell
import Quickshell.Io

FloatingWindow {
    id: root
    title: "Scottland widget: Weather"
    color: "transparent"

    property var reading: ({})
    property var palette: ({})
    readonly property color background: palette.background || "#2e3440"
    readonly property color foreground: palette.foreground || "#d8dee9"
    readonly property color muted: palette.muted || "#97a3ab"
    readonly property string fontFamily: palette.font_family || Qt.application.font.family
    readonly property real textScale: Math.max(0.5, Math.min(3, Number(palette.text_scale) || 1))
    readonly property real padding: 18
    readonly property real rowGap: 6
    readonly property real iconSize: 48 * textScale
    readonly property real fontSize: 20 * textScale
    readonly property real updatedSize: 13 * textScale

    readonly property string status: typeof reading.status === "string" ? reading.status : "unavailable"
    readonly property string iconName: typeof reading.icon === "string" ? reading.icon : ""
    readonly property string temperature: typeof reading.temperature === "string" ? reading.temperature : ""
    readonly property string updated: typeof reading.updated === "string" ? reading.updated : ""
    readonly property bool showingReading: (status === "current" || status === "stale")
        && iconName.length > 0 && temperature.length > 0
        && (status !== "stale" || updated.length > 0)
    readonly property bool stale: showingReading && status === "stale"
    readonly property string message: status === "no-location" ? "No location" : "Weather unavailable"
    readonly property string iconSource: iconName ? Quickshell.iconPath(iconName, true) : ""
    readonly property string updatedTime: {
        const date = new Date(updated)
        return isNaN(date.getTime()) ? updated : Qt.formatTime(date, "h:mm AP")
    }

    TextMetrics {
        id: temperatureMetrics
        text: root.temperature
        font.family: root.fontFamily
        font.pixelSize: root.fontSize
        font.weight: Font.DemiBold
    }
    TextMetrics {
        id: updatedMetrics
        text: "Updated " + root.updatedTime
        font.family: root.fontFamily
        font.pixelSize: root.updatedSize
    }
    TextMetrics {
        id: messageMetrics
        text: root.message
        font.family: root.fontFamily
        font.pixelSize: root.fontSize
        font.weight: Font.DemiBold
    }

    implicitWidth: Math.max(188, Math.ceil(2 * padding + Math.max(
        showingReading ? iconSize + rowGap + temperatureMetrics.advanceWidth : messageMetrics.advanceWidth,
        stale ? updatedMetrics.advanceWidth : 0)))
    implicitHeight: Math.max(82, Math.ceil(2 * padding + (showingReading ? iconSize : fontSize)
        + (stale ? rowGap + updatedSize : 0)))
    minimumSize: Qt.size(implicitWidth, implicitHeight)
    maximumSize: Qt.size(implicitWidth, implicitHeight)

    FileView {
        path: Quickshell.env("SCOTTLAND_PALETTE") || ""
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: {
            try { root.palette = JSON.parse(text()) }
            catch (_error) { root.palette = ({}) }
        }
    }

    Process {
        id: dataReader
        command: ["python3", Quickshell.shellRoot + "/data.py"]
        running: true
        stdout: SplitParser {
            onRead: data => {
                try {
                    const value = JSON.parse(data)
                    root.reading = value && typeof value === "object" && !Array.isArray(value) ? value : ({})
                } catch (_error) {
                    root.reading = ({})
                }
            }
        }
    }

    Process {
        id: openWindow
        command: ["busctl", "--user", "call", "org.scottland.Widgets",
            "/org/scottland/widget/" + (Quickshell.env("SCOTTLAND_WIDGET_ID") || ""),
            "org.scottland.Widget", "Open"]
    }

    Rectangle {
        anchors.fill: parent
        radius: 16
        color: root.background

        Column {
            anchors.centerIn: parent
            spacing: root.rowGap

            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: root.rowGap
                visible: root.showingReading

                Image {
                    width: root.iconSize
                    height: root.iconSize
                    source: root.iconSource
                    sourceSize: Qt.size(width, height)
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.temperature
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: root.fontSize
                    font.weight: Font.DemiBold
                }
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: root.stale
                text: "Updated " + root.updatedTime
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: root.updatedSize
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: !root.showingReading
                text: root.message
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: root.fontSize
                font.weight: Font.DemiBold
            }
        }

        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: openWindow.running = true
        }
    }

    Connections {
        target: Quickshell
        function onLastWindowClosed() {
            dataReader.running = false
            Qt.quit()
        }
    }
}
