/* Small enhancements for the admin. Everything here is optional: the pages
   work without it, they are just less tidy. */
(function () {
  "use strict";

  // ---- Changelist filters ------------------------------------------------
  // Fold groups with nothing picked, remember what the editor opened, show
  // the picked value on a folded group, and let long groups be narrowed.
  var nav = document.getElementById("changelist-filter");
  if (nav) {
    var key = "e-filters:" + location.pathname;
    var remembered = {};
    try { remembered = JSON.parse(sessionStorage.getItem(key) || "{}"); } catch (e) {}

    nav.querySelectorAll("details.e-filter").forEach(function (group) {
      var title = group.dataset.filterTitle;
      var picked = group.querySelector("li.selected:not([data-all])");
      var badge = group.querySelector(".e-filter-picked");

      if (picked) {
        badge.textContent = picked.textContent.trim().replace(/\s*\(\d+\)$/, "");
        badge.hidden = false;
        group.classList.add("is-active");
      }
      group.open = picked ? true : remembered[title] === true;

      group.addEventListener("toggle", function () {
        remembered[title] = group.open;
        try { sessionStorage.setItem(key, JSON.stringify(remembered)); } catch (e) {}
      });

      var narrow = group.querySelector(".e-filter-narrow input");
      if (narrow) {
        narrow.addEventListener("input", function () {
          var q = narrow.value.trim().toLowerCase();
          group.querySelectorAll(".e-chips li").forEach(function (li) {
            li.hidden = !!q && !li.hasAttribute("data-all") &&
              li.textContent.toLowerCase().indexOf(q) === -1;
          });
        });
      }
    });

    var header = document.getElementById("changelist-filter-header");
    if (header) header.textContent = "Filters";
  }
})();
