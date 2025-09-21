// ドラッグ可能なウィンドウの機能を追加
dragElement(document.getElementById("draggable-window"));

function dragElement(element) {
    var pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;

    // ウィンドウのヘッダーをドラッグ可能にする
    var header = document.getElementById("window-header");
    if (header) {
        header.onmousedown = dragMouseDown;
    } else {
        element.onmousedown = dragMouseDown;
    }

    function dragMouseDown(e) {
        e = e || window.event;
        e.preventDefault();
        pos3 = e.clientX;
        pos4 = e.clientY;
        document.onmouseup = closeDragElement;
        document.onmousemove = elementDrag;
    }

    function elementDrag(e) {
        e = e || window.event;
        e.preventDefault();
        pos1 = pos3 - e.clientX;
        pos2 = pos4 - e.clientY;
        pos3 = e.clientX;
        pos4 = e.clientY;
        element.style.top = (element.offsetTop - pos2) + "px";
        element.style.left = (element.offsetLeft - pos1) + "px";
    }

    function closeDragElement() {
        document.onmouseup = null;
        document.onmousemove = null;
        // 現在の位置をlocalStorageに保存
        localStorage.setItem("chatWindowPos", JSON.stringify({
            top: element.style.top,
            left: element.style.left
        }));
    }
}

function closeWindow() {
    var windowElement = document.getElementById('draggable-window');
    windowElement.style.display = 'none';
}

// ページ読み込み時に保存されたチャットウィンドウの位置を復元
window.addEventListener("load", function() {
    var savedPos = localStorage.getItem("chatWindowPos");
    if (savedPos) {
        savedPos = JSON.parse(savedPos);
        var chatWindow = document.getElementById("draggable-window");
        chatWindow.style.top = savedPos.top;
        chatWindow.style.left = savedPos.left;
    }

    var toggleBtn = document.getElementById("history-toggle");
    var historyArea = document.getElementById("operation-history");
    if (toggleBtn && historyArea) {
        toggleBtn.addEventListener("click", function() {
            if (historyArea.style.display === "none" || historyArea.style.display === "") {
                historyArea.style.display = "block";
            } else {
                historyArea.style.display = "none";
            }
        });
    }
});
