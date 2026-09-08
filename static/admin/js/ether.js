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
    var activeCount = 0;

    nav.querySelectorAll("details.e-filter").forEach(function (group) {
      var title = group.dataset.filterTitle;
      var picked = group.querySelector("li.selected:not([data-all])");
      var badge = group.querySelector(".e-filter-picked");

      if (picked) {
        badge.textContent = picked.textContent.trim().replace(/\s*\(\d+\)$/, "");
        badge.hidden = false;
        group.classList.add("is-active");
        activeCount++;
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

    // ---- Turn the panel into a popup ---------------------------------
    // Without this the panel is just a normal block on the page - the
    // markup and CSS both work either way.
    var trigger = document.querySelector(".e-filters-trigger");
    var backdrop = document.querySelector(".e-filter-backdrop");
    if (trigger && backdrop) {
      var countBadge = trigger.querySelector(".e-filters-count");
      if (activeCount) {
        countBadge.textContent = String(activeCount);
        countBadge.hidden = false;
        trigger.classList.add("is-active");
      }

      nav.classList.add("e-filter-popup-active");
      nav.hidden = true;

      var closeBtn = nav.querySelector(".e-filter-close");
      var lastFocus = null;

      function openPanel() {
        lastFocus = document.activeElement;
        nav.hidden = false;
        backdrop.hidden = false;
        // Forces the browser to apply the hidden->visible state before the
        // transition-triggering class below, so the slide-in actually
        // animates. rAF-based versions of this trick depend on the tab
        // actively compositing frames and silently do nothing in a
        // background/inactive tab; a synchronous style read does not.
        void nav.offsetHeight;
        nav.classList.add("is-open");
        backdrop.classList.add("is-open");
        trigger.setAttribute("aria-expanded", "true");
        document.body.classList.add("e-no-scroll");
        if (closeBtn) closeBtn.focus();
      }

      function closePanel() {
        nav.classList.remove("is-open");
        backdrop.classList.remove("is-open");
        trigger.setAttribute("aria-expanded", "false");
        document.body.classList.remove("e-no-scroll");
        window.setTimeout(function () {
          if (!nav.classList.contains("is-open")) { nav.hidden = true; backdrop.hidden = true; }
        }, 200);
        if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
        else trigger.focus();
      }

      trigger.addEventListener("click", function () {
        if (nav.hidden) openPanel(); else closePanel();
      });
      if (closeBtn) closeBtn.addEventListener("click", closePanel);
      backdrop.addEventListener("click", closePanel);
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !nav.hidden) closePanel();
      });
    }

    var header = document.getElementById("changelist-filter-header");
    if (header) header.textContent = "Filters";
  }

  // ---- Small screens: the sidebar folds behind a Menu button --------------
  var sidebar = document.getElementById("nav-sidebar");
  var toggle = sidebar && sidebar.querySelector(".e-menu-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var open = sidebar.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "Close" : "Menu";
    });
  }
})();
