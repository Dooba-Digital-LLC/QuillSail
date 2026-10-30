/*
 * Docs enhancements: live search, the small-screen menu, code copy buttons and
 * the current section in "On this page". Without JavaScript, search submits to
 * /search and the menu opens as a link target.
 */
(function () {
  "use strict";

  var root = document.documentElement;

  /* ---------------------------------------------------------------- menu */

  var menu = document.getElementById("docs-menu");
  var toggle = document.querySelector("[data-menu-toggle]");
  var close = document.querySelector("[data-menu-close]");

  function setMenu(open) {
    if (!menu || !toggle) return;
    menu.classList.toggle("is-open", open);
    root.classList.toggle("menu-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      var current = menu.querySelector('[aria-current="page"]') || menu.querySelector("a");
      if (current) current.focus();
    } else {
      toggle.focus();
    }
  }

  if (menu && toggle) {
    toggle.setAttribute("role", "button");
    toggle.setAttribute("aria-expanded", "false");
    toggle.addEventListener("click", function (event) {
      event.preventDefault();
      setMenu(!menu.classList.contains("is-open"));
    });
    if (close) {
      close.addEventListener("click", function (event) {
        event.preventDefault();
        setMenu(false);
      });
    }
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.classList.contains("is-open")) setMenu(false);
    });
  }

  /* ---------------------------------------------------------------- code copy buttons */

  document.querySelectorAll(".prose .code-block").forEach(function (block) {
    var code = block.querySelector("pre code");
    if (!code || !window.QuillSail) return;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "code-copy";
    button.innerHTML = '<svg class="icon" aria-hidden="true" focusable="false"><use href="#i-copy"/></svg>' +
      '<span data-copy-label>Copy</span><span class="visually-hidden"> code</span>';
    block.appendChild(button);
    window.QuillSail.copyButton(button, function () { return code.textContent.replace(/\n$/, ""); });
  });

  /* ---------------------------------------------------------------- live search */

  var form = document.querySelector("[data-search]");
  var input = form && form.querySelector("[data-search-input]");
  var panel = form && form.querySelector("[data-search-panel]");
  var list = form && form.querySelector("[data-search-list]");
  var status = form && form.querySelector("[data-search-status]");
  var timer = null;
  var latest = 0;

  function links() {
    return Array.prototype.slice.call(list.querySelectorAll("a"));
  }

  function hide() {
    if (panel) panel.hidden = true;
    if (input) input.setAttribute("aria-expanded", "false");
  }

  function render(query, results) {
    list.textContent = "";
    if (!results.length) {
      var empty = document.createElement("li");
      empty.className = "search-empty";
      empty.textContent = "Nothing matched “" + query + "”.";
      list.appendChild(empty);
    }
    results.forEach(function (result) {
      var item = document.createElement("li");
      var link = document.createElement("a");
      link.href = result.url;
      var kicker = document.createElement("span");
      kicker.className = "kicker";
      kicker.textContent = result.section + " · " + result.title;
      var title = document.createElement("span");
      title.className = "suggestion-title";
      title.textContent = result.heading;
      var snippet = document.createElement("span");
      snippet.className = "suggestion-snippet";
      snippet.innerHTML = result.snippet; // Escaped by the server; only <mark> is added.
      link.appendChild(kicker);
      link.appendChild(title);
      link.appendChild(snippet);
      item.appendChild(link);
      list.appendChild(item);
    });
    status.textContent = results.length ? results.length + (results.length === 1 ? " result." : " results.") : "No results.";
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function search() {
    var query = input.value.trim();
    if (query.length < 2) {
      hide();
      return;
    }
    var request = ++latest;
    fetch(form.getAttribute("action") + ".json?q=" + encodeURIComponent(query), { headers: { Accept: "application/json" } })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(response.status); })
      .then(function (data) {
        if (request === latest && input.value.trim() === query) render(query, data.results);
      })
      .catch(function () {});
  }

  if (form && input && panel && list) {
    input.setAttribute("aria-controls", panel.id || "");
    input.setAttribute("aria-expanded", "false");
    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(search, 120);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" && !panel.hidden && links().length) {
        event.preventDefault();
        links()[0].focus();
      } else if (event.key === "Escape") {
        hide();
      }
    });
    list.addEventListener("keydown", function (event) {
      var items = links();
      var index = items.indexOf(document.activeElement);
      if (event.key === "ArrowDown" && index < items.length - 1) {
        event.preventDefault();
        items[index + 1].focus();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        (index > 0 ? items[index - 1] : input).focus();
      } else if (event.key === "Escape") {
        hide();
        input.focus();
      }
    });
    document.addEventListener("click", function (event) {
      if (!form.contains(event.target)) hide();
    });
    form.addEventListener("focusout", function (event) {
      if (event.relatedTarget && !form.contains(event.relatedTarget)) hide();
    });
    // "/" jumps to search, unless someone is typing in a field.
    document.addEventListener("keydown", function (event) {
      var target = event.target;
      var typing = target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        input.focus();
        input.select();
      }
    });
  }

  /* ---------------------------------------------------------------- current section */

  var toc = document.querySelector("[data-toc]");
  if (toc && "IntersectionObserver" in window) {
    var entries = Array.prototype.slice.call(toc.querySelectorAll("a"));
    var targets = entries.map(function (link) {
      return document.getElementById(decodeURIComponent(link.getAttribute("href").slice(1)));
    });
    var visible = new Set();
    var mark = function () {
      var index = targets.findIndex(function (target) { return target && visible.has(target); });
      if (index < 0) return;
      entries.forEach(function (link, position) { link.classList.toggle("is-active", position === index); });
    };
    var observer = new IntersectionObserver(function (changes) {
      changes.forEach(function (change) {
        if (change.isIntersecting) visible.add(change.target);
        else visible.delete(change.target);
      });
      mark();
    }, { rootMargin: "-72px 0px -60% 0px" });
    targets.forEach(function (target) { if (target) observer.observe(target); });
  }
})();
