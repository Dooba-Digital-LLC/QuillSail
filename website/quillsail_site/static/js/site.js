/*
 * Progressive enhancement for quillsail.blog and the docs. Every page works
 * without it: commands stay visible and selectable, and downloads are links.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var STORAGE_KEY = "quillsail-appearance";

  /* ---------------------------------------------------------------- appearance */

  function effectiveTheme(choice) {
    if (choice !== "auto") return choice;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyAppearance(choice) {
    root.setAttribute("data-appearance", choice);
    if (choice === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", effectiveTheme(choice) === "dark" ? "#000000" : "#ffffff");
    document.querySelectorAll("[data-appearance-choice]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.getAttribute("data-appearance-choice") === choice));
    });
  }

  document.querySelectorAll("[data-appearance-control]").forEach(function (control) {
    control.hidden = false;
    control.addEventListener("click", function (event) {
      var button = event.target.closest("[data-appearance-choice]");
      if (!button) return;
      var choice = button.getAttribute("data-appearance-choice");
      try {
        localStorage.setItem(STORAGE_KEY, choice);
      } catch (error) {}
      applyAppearance(choice);
    });
  });
  applyAppearance(root.getAttribute("data-appearance") || "light");
  if (window.matchMedia) {
    var query = window.matchMedia("(prefers-color-scheme: dark)");
    var refresh = function () {
      if (root.getAttribute("data-appearance") === "auto") applyAppearance("auto");
    };
    if (query.addEventListener) query.addEventListener("change", refresh);
  }

  /* ---------------------------------------------------------------- copy */

  function announce(element, message) {
    var status = element.closest("[data-tabs]");
    status = status ? status.querySelector("[data-copy-status]") : document.querySelector("[data-copy-status]");
    if (status) {
      status.textContent = "";
      window.setTimeout(function () { status.textContent = message; }, 50);
    }
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.className = "visually-hidden";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy") ? resolve() : reject(new Error("copy failed"));
      } catch (error) {
        reject(error);
      } finally {
        document.body.removeChild(area);
      }
    });
  }

  window.QuillSail = window.QuillSail || {};
  window.QuillSail.copyButton = function (button, getText) {
    var label = button.querySelector("[data-copy-label]");
    var icon = button.querySelector("use");
    var timer = null;
    button.hidden = false;
    button.addEventListener("click", function () {
      copyText(getText()).then(function () {
        if (label) label.textContent = "Copied";
        if (icon) icon.setAttribute("href", "#i-check");
        announce(button, "Copied to the clipboard.");
        window.clearTimeout(timer);
        timer = window.setTimeout(function () {
          if (label) label.textContent = "Copy";
          if (icon) icon.setAttribute("href", "#i-copy");
        }, 2000);
      }, function () {
        if (label) label.textContent = "Select and copy";
      });
    });
  };

  document.querySelectorAll("[data-copy]").forEach(function (button) {
    window.QuillSail.copyButton(button, function () { return button.getAttribute("data-copy"); });
  });

  /* ---------------------------------------------------------------- install tabs */

  document.querySelectorAll("[data-tabs]").forEach(function (container) {
    var list = container.querySelector('[role="tablist"]');
    if (!list) return;
    var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
    var panels = tabs.map(function (tab) { return document.getElementById(tab.getAttribute("aria-controls")); });

    function select(index, focus) {
      tabs.forEach(function (tab, position) {
        var selected = position === index;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
        panels[position].hidden = !selected;
      });
      if (focus) tabs[index].focus();
    }

    list.hidden = false;
    container.classList.add("has-tabs");
    var initial = tabs.findIndex(function (tab) { return tab.getAttribute("aria-selected") === "true"; });
    select(initial < 0 ? 0 : initial, false);
    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { select(index, false); });
      tab.addEventListener("keydown", function (event) {
        var next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        select((next + tabs.length) % tabs.length, true);
      });
    });
  });

  /* ---------------------------------------------------------------- download choice */

  // iPads ask for desktop sites, so their browser looks like a Mac's. QuillSail runs on computers.
  var isTablet = /Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1;

  document.querySelectorAll("[data-mac-switch]").forEach(function (cta) {
    var link = cta.querySelector("[data-download-link]");
    var meta = cta.querySelector("[data-download-meta]");
    var alternative = cta.querySelector("[data-download-alt]");
    if (isTablet) {
      if (link) {
        link.setAttribute("href", cta.getAttribute("data-downloads-href"));
        link.querySelector("span").textContent = "See downloads for your computer";
      }
      if (meta) meta.textContent = "QuillSail runs on Mac, Windows and Linux computers.";
      if (alternative) {
        var separator = alternative.nextElementSibling;
        if (separator && separator.classList.contains("dot")) separator.remove();
        alternative.remove();
      }
      return;
    }
    var data = navigator.userAgentData;
    if (!data || !data.getHighEntropyValues) return;
    data.getHighEntropyValues(["architecture"]).then(function (values) {
      if (values.architecture !== "x86") return;
      if (link) link.setAttribute("href", cta.getAttribute("data-intel-href"));
      if (meta) meta.textContent = cta.getAttribute("data-intel-meta");
      if (alternative) {
        alternative.setAttribute("href", cta.getAttribute("data-arm-href"));
        alternative.textContent = "Download for Apple silicon";
      }
    }, function () {});
  });
})();
