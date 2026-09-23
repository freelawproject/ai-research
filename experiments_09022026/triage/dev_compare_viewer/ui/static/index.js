/* Index: make whole table rows clickable without swallowing the link itself. */
(function () {
  "use strict";
  document.querySelectorAll("tr.rowlink").forEach(function (tr) {
    tr.addEventListener("click", function (ev) {
      if (ev.target.tagName === "A") return;
      window.location.href = tr.dataset.href;
    });
  });
})();
