/* The desk's own small enhancements. Every page works without this - forms
   submit, links navigate - this only adds the popup, folding and tabs. */
(function () {
  "use strict";

  function initFilters() {
    var panel = document.querySelector(".d-filter-panel");
    var trigger = document.querySelector(".d-filters-trigger");
    var backdrop = document.querySelector(".d-filter-backdrop");
    if (!panel || !trigger || !backdrop) return;

    var key = "desk-filters:" + location.pathname;
    var remembered = {};
    try { remembered = JSON.parse(sessionStorage.getItem(key) || "{}"); } catch (e) {}

    var activeCount = 0;
    panel.querySelectorAll(".d-filter-group").forEach(function (group) {
      var title = group.dataset.title;
      var picked = group.querySelector("li.selected:not(.is-default)");
      var badge = group.querySelector(".d-filter-picked");
      if (picked && badge) {
        badge.textContent = picked.textContent.trim();
        badge.hidden = false;
        group.classList.add("is-active");
        activeCount++;
      }
      group.open = picked ? true : remembered[title] === true;
      group.addEventListener("toggle", function () {
        remembered[title] = group.open;
        try { sessionStorage.setItem(key, JSON.stringify(remembered)); } catch (e) {}
      });

      var narrow = group.querySelector(".d-filter-narrow input");
      if (narrow) {
        narrow.addEventListener("input", function () {
          var q = narrow.value.trim().toLowerCase();
          group.querySelectorAll(".d-chips li").forEach(function (li) {
            li.hidden = !!q && !li.classList.contains("is-default") &&
              li.textContent.toLowerCase().indexOf(q) === -1;
          });
        });
      }
    });

    if (activeCount) {
      var countBadge = trigger.querySelector(".d-filters-count");
      countBadge.textContent = String(activeCount);
      countBadge.hidden = false;
      trigger.classList.add("is-active");
    }

    var closeBtn = panel.querySelector(".d-filter-close");
    var lastFocus = null;

    function open() {
      lastFocus = document.activeElement;
      panel.hidden = false;
      backdrop.hidden = false;
      // Forces the browser to apply the closed state above before the
      // class below flips, so the slide-in transition actually runs. A
      // requestAnimationFrame-based version of this trick depends on the
      // tab actively compositing frames and does nothing in a
      // backgrounded/inactive one; a synchronous style read does not.
      void panel.offsetHeight;
      panel.classList.add("is-open");
      backdrop.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      document.body.classList.add("d-no-scroll");
      if (closeBtn) closeBtn.focus();
    }

    function close() {
      panel.classList.remove("is-open");
      backdrop.classList.remove("is-open");
      trigger.setAttribute("aria-expanded", "false");
      document.body.classList.remove("d-no-scroll");
      window.setTimeout(function () {
        if (!panel.classList.contains("is-open")) { panel.hidden = true; backdrop.hidden = true; }
      }, 220);
      if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
      else trigger.focus();
    }

    trigger.addEventListener("click", function () {
      if (panel.hidden) open(); else close();
    });
    if (closeBtn) closeBtn.addEventListener("click", close);
    backdrop.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !panel.hidden) close();
    });
  }

  function initSidebar() {
    var side = document.querySelector(".d-side");
    var toggle = side && side.querySelector(".d-menu-toggle");
    if (!toggle) return;
    toggle.addEventListener("click", function () {
      var open = side.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "Close" : "Menu";
    });
  }

  function initTabs() {
    document.querySelectorAll("[data-tabs]").forEach(function (root) {
      var panels = Array.prototype.slice.call(root.querySelectorAll("[data-tab-panel]"));
      if (panels.length < 2) return;
      var tabs = document.createElement("div");
      tabs.className = "d-tabs";
      tabs.setAttribute("role", "tablist");

      var storeKey = "desk-tab:" + (root.dataset.tabs || location.pathname);
      var remembered = -1;
      try { remembered = parseInt(sessionStorage.getItem(storeKey) || "-1", 10); } catch (e) {}
      var erroredIndex = panels.findIndex(function (p) { return p.querySelector(".d-errors"); });
      var initial = erroredIndex > -1 ? erroredIndex
        : (remembered >= 0 && remembered < panels.length ? remembered : 0);

      function select(index) {
        panels.forEach(function (p, i) {
          p.hidden = i !== index;
          tabs.children[i].setAttribute("aria-selected", i === index ? "true" : "false");
        });
        try { sessionStorage.setItem(storeKey, String(index)); } catch (e) {}
      }

      panels.forEach(function (p, i) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.setAttribute("role", "tab");
        btn.textContent = p.dataset.tabPanel;
        btn.addEventListener("click", function () { select(i); });
        tabs.appendChild(btn);
      });
      root.insertBefore(tabs, panels[0]);
      select(initial);
    });
  }

  function initBulkSelect() {
    document.querySelectorAll("form[data-bulk-form]").forEach(function (form) {
      var selectAll = form.querySelector("[data-select-all]");
      var rowChecks = Array.prototype.slice.call(form.querySelectorAll("input[name=selected]"));
      var counter = form.querySelector("[data-selected-count]");
      var actions = Array.prototype.slice.call(form.querySelectorAll("[data-bulk-action]"));

      function refresh() {
        var n = rowChecks.filter(function (c) { return c.checked; }).length;
        if (counter) counter.textContent = n ? n + " selected" : "None selected";
        if (selectAll) selectAll.checked = n > 0 && n === rowChecks.length;
        // Greyed out until something is ticked - pressing an action with an
        // empty selection is never what anyone meant.
        actions.forEach(function (button) { button.disabled = n === 0; });
        rowChecks.forEach(function (c) {
          var tr = c.closest("tr");
          if (tr) tr.classList.toggle("is-selected", c.checked);
        });
      }
      if (selectAll) {
        selectAll.addEventListener("change", function () {
          rowChecks.forEach(function (c) { c.checked = selectAll.checked; });
          refresh();
        });
      }
      rowChecks.forEach(function (c) { c.addEventListener("change", refresh); });
      refresh();
    });
  }

  function initConfirm() {
    // On a form: confirm before it submits. On a button inside a form with
    // several actions: confirm only for that button, since the others are
    // harmless.
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (!window.confirm(form.dataset.confirm)) e.preventDefault();
      });
    });
    document.querySelectorAll("button[data-confirm]").forEach(function (button) {
      button.addEventListener("click", function (e) {
        if (!window.confirm(button.dataset.confirm)) e.preventDefault();
      });
    });
  }

  initFilters();
  initSidebar();
  initTabs();
  initBulkSelect();
  initConfirm();
})();
