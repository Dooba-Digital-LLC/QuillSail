/*
 * Issue composer: drag items to reorder them.
 * Without JavaScript, the up/down buttons on each item do the same thing.
 */
(function () {
  "use strict";

  var list = document.querySelector("[data-composer]");
  if (!list) return;
  var url = list.getAttribute("data-reorder-url");
  var status = document.querySelector("[data-composer-status]");
  var dragged = null;

  function items() {
    return Array.prototype.slice.call(list.querySelectorAll("[data-item-id]"));
  }

  function announce(message) {
    if (status) status.textContent = message;
  }

  function save() {
    var order = items().map(function (el) {
      return parseInt(el.getAttribute("data-item-id"), 10);
    });
    announce("Saving order…");
    window.Copydesk.post(url, { order: order })
      .then(function () {
        announce("Order saved.");
      })
      .catch(function (error) {
        announce(error.message + " Reload the page and try again.");
      });
  }

  items().forEach(function (item) {
    var handle = item.querySelector(".drag-handle");
    if (!handle) return;

    handle.addEventListener("mousedown", function () {
      item.setAttribute("draggable", "true");
    });
    handle.addEventListener("touchstart", function () {
      item.setAttribute("draggable", "true");
    }, { passive: true });

    // Keyboard: focus the handle and press the up and down arrows.
    handle.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault();
      var sibling = event.key === "ArrowUp" ? item.previousElementSibling : item.nextElementSibling;
      if (!sibling) return;
      if (event.key === "ArrowUp") list.insertBefore(item, sibling);
      else list.insertBefore(sibling, item);
      handle.focus();
      save();
    });

    item.addEventListener("dragstart", function (event) {
      dragged = item;
      item.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", item.getAttribute("data-item-id"));
    });

    item.addEventListener("dragend", function () {
      item.classList.remove("dragging");
      item.removeAttribute("draggable");
      items().forEach(function (el) {
        el.classList.remove("drop-target");
      });
      dragged = null;
    });

    item.addEventListener("dragover", function (event) {
      if (!dragged || dragged === item) return;
      event.preventDefault();
      item.classList.add("drop-target");
    });

    item.addEventListener("dragleave", function () {
      item.classList.remove("drop-target");
    });

    item.addEventListener("drop", function (event) {
      event.preventDefault();
      item.classList.remove("drop-target");
      if (!dragged || dragged === item) return;
      var rect = item.getBoundingClientRect();
      var after = event.clientY > rect.top + rect.height / 2;
      list.insertBefore(dragged, after ? item.nextSibling : item);
      save();
    });
  });
})();
