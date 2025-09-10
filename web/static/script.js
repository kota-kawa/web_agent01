// ページ読み込み時の処理
window.addEventListener("load", function() {
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
