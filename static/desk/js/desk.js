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


  // ---- Searchable dropdowns -----------------------------------------------
  // Every dropdown becomes the same component, so a form never mixes two
  // kinds of control. The filter box inside it only appears once a list is
  // long enough to be worth searching - a search field over three options
  // is just friction.
  var FILTER_FROM = 8;

  function initSelects() {
    document.querySelectorAll("select:not([multiple])").forEach(function (select) {
      // Even a one-option select is converted: it looks inert either way,
      // and leaving it native is what makes a form look half-finished.
      if (!select.options.length) return;
      if ("noSearch" in select.dataset) return;
      makeSearchable(select);
    });
  }

  function makeSearchable(select) {
    var wrap = document.createElement("div");
    wrap.className = "d-combo";
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    // The select stays in the DOM and keeps holding the value, so the form
    // submits exactly as it would without any of this.
    select.classList.add("d-combo-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    var button = document.createElement("button");
    button.type = "button";
    button.className = "d-combo-button";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    if (select.id) button.setAttribute("aria-labelledby", "");

    var panel = document.createElement("div");
    panel.className = "d-combo-panel";
    panel.hidden = true;

    var search = document.createElement("input");
    search.type = "search";
    search.className = "d-combo-search";
    search.placeholder = "Type to narrow…";
    search.setAttribute("aria-label", "Filter options");

    var list = document.createElement("ul");
    list.className = "d-combo-list";
    list.setAttribute("role", "listbox");

    if (select.options.length >= FILTER_FROM) panel.appendChild(search);
    panel.appendChild(list);
    wrap.appendChild(button);
    wrap.appendChild(panel);

    var items = [];
    Array.prototype.forEach.call(select.options, function (option, i) {
      var li = document.createElement("li");
      li.className = "d-combo-option";
      li.setAttribute("role", "option");
      li.id = (select.id || "combo") + "-opt-" + i;
      li.textContent = option.text;
      li.dataset.value = option.value;
      li.addEventListener("click", function () { choose(option.value); });
      list.appendChild(li);
      items.push(li);
    });

    var active = -1;

    function label() {
      var picked = select.options[select.selectedIndex];
      return picked ? picked.text : "";
    }

    function paint() {
      button.textContent = label();
      button.classList.toggle("is-placeholder", select.value === "");
      items.forEach(function (li) {
        li.setAttribute("aria-selected", li.dataset.value === select.value ? "true" : "false");
      });
    }

    function visible() {
      return items.filter(function (li) { return !li.hidden; });
    }

    function highlight(index) {
      var shown = visible();
      items.forEach(function (li) { li.classList.remove("is-active"); });
      if (!shown.length) { active = -1; button.removeAttribute("aria-activedescendant"); return; }
      active = Math.max(0, Math.min(index, shown.length - 1));
      shown[active].classList.add("is-active");
      shown[active].scrollIntoView({ block: "nearest" });
      button.setAttribute("aria-activedescendant", shown[active].id);
    }

    function open() {
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
      search.value = "";
      items.forEach(function (li) { li.hidden = false; });
      var current = items.findIndex(function (li) { return li.dataset.value === select.value; });
      highlight(current > -1 ? current : 0);
      if (search.isConnected) search.focus();
    }

    function close() {
      panel.hidden = true;
      button.setAttribute("aria-expanded", "false");
    }

    function choose(value) {
      select.value = value;
      // Anything already listening to the select - a filter form that
      // submits on change, say - must still hear about it.
      select.dispatchEvent(new Event("change", { bubbles: true }));
      paint();
      close();
      button.focus();
    }

    button.addEventListener("click", function () {
      if (panel.hidden) open(); else close();
    });

    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      items.forEach(function (li) {
        li.hidden = !!q && li.textContent.toLowerCase().indexOf(q) === -1;
      });
      highlight(0);
    });

    search.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); highlight(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        var shown = visible();
        if (shown[active]) choose(shown[active].dataset.value);
      } else if (e.key === "Escape") { e.preventDefault(); close(); button.focus(); }
    });

    button.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (panel.hidden) open();
        else if (visible()[active]) choose(visible()[active].dataset.value);
        return;
      }
      if (panel.hidden) return;
      if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });

    document.addEventListener("click", function (e) {
      if (!wrap.contains(e.target)) close();
    });

    // Something else may set the value programmatically.
    select.addEventListener("change", paint);
    paint();
  }


  // ---- Sticky page header --------------------------------------------------
  // Purely cosmetic: the header is sticky in CSS regardless, this only adds
  // the border and shadow once it is actually holding position, so the edge
  // doesn't show while the page is scrolled to the top.
  function initStickyHeader() {
    var top = document.querySelector(".d-page-top");
    if (!top || !("IntersectionObserver" in window)) return;
    var sentinel = document.createElement("div");
    sentinel.setAttribute("aria-hidden", "true");
    top.parentNode.insertBefore(sentinel, top);
    new IntersectionObserver(function (entries) {
      top.classList.toggle("is-stuck", !entries[0].isIntersecting);
    }, { rootMargin: "-" + (parseInt(getComputedStyle(document.documentElement)
        .getPropertyValue("--d-topbar-h"), 10) + 1) + "px 0px 0px 0px" }).observe(sentinel);
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
  initSelects();
  initStickyHeader();
  initConfirm();
})();
