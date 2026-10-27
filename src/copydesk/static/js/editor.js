/*
 * The writing editor: a Markdown textarea with a toolbar, keyboard shortcuts,
 * an exact server-rendered preview, image uploads, a word count, and a
 * local backup so a closed tab never loses work.
 *
 * Markup expected (see templates/write/edit.html):
 *   <div class="editor" data-editor data-preview-url=... data-upload-url=... data-backup-key=...>
 *     <div class="editor-toolbar">... <button data-md="bold"> ... </div>
 *     <textarea data-editor-input></textarea>
 *     <div data-editor-preview hidden></div>
 *     <div class="editor-status"><span data-word-count></span></div>
 *   </div>
 */
(function () {
  "use strict";

  var editor = document.querySelector("[data-editor]");
  if (!editor) return;

  var textarea = editor.querySelector("[data-editor-input]");
  var preview = editor.querySelector("[data-editor-preview]");
  var wordCount = editor.querySelector("[data-word-count]");
  var form = textarea.form;
  var maxWords = parseInt(editor.getAttribute("data-max-words") || "0", 10);
  var backupKey = editor.getAttribute("data-backup-key");
  var titleInput = form.querySelector('[name="title"]');
  var subtitleInput = form.querySelector('[name="subtitle"]');
  var dirty = false;

  // ------------------------------------------------------------ text helpers

  function selection() {
    return { start: textarea.selectionStart, end: textarea.selectionEnd, text: textarea.value };
  }

  function replaceRange(start, end, insert, selectStart, selectEnd) {
    textarea.focus();
    textarea.setRangeText(insert, start, end, "end");
    if (selectStart !== undefined) {
      textarea.setSelectionRange(start + selectStart, start + (selectEnd !== undefined ? selectEnd : selectStart));
    }
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function wrap(before, after, placeholder) {
    var s = selection();
    var chosen = s.text.slice(s.start, s.end) || placeholder;
    replaceRange(s.start, s.end, before + chosen + after, before.length, before.length + chosen.length);
  }

  function prefixLines(prefix, numbered) {
    var s = selection();
    var lineStart = s.text.lastIndexOf("\n", s.start - 1) + 1;
    var lineEnd = s.text.indexOf("\n", s.end);
    if (lineEnd === -1) lineEnd = s.text.length;
    var lines = s.text.slice(lineStart, lineEnd).split("\n");
    var already = lines.every(function (line, i) {
      return numbered ? /^\d+\.\s/.test(line) : line.indexOf(prefix) === 0;
    });
    var result = lines
      .map(function (line, i) {
        if (already) return numbered ? line.replace(/^\d+\.\s/, "") : line.slice(prefix.length);
        return (numbered ? i + 1 + ". " : prefix) + line;
      })
      .join("\n");
    replaceRange(lineStart, lineEnd, result, 0, result.length);
  }

  var actions = {
    bold: function () {
      wrap("**", "**", "bold text");
    },
    italic: function () {
      wrap("_", "_", "italic text");
    },
    heading: function () {
      prefixLines("## ");
    },
    link: function () {
      var s = selection();
      var label = s.text.slice(s.start, s.end) || "link text";
      var url = window.prompt("Link address (URL):", "https://");
      if (!url) return;
      replaceRange(s.start, s.end, "[" + label + "](" + url + ")", 1, 1 + label.length);
    },
    quote: function () {
      prefixLines("> ");
    },
    code: function () {
      var s = selection();
      var chosen = s.text.slice(s.start, s.end);
      if (chosen.indexOf("\n") !== -1 || !chosen) {
        var block = "```\n" + (chosen || "code") + "\n```";
        replaceRange(s.start, s.end, block, 4, 4 + (chosen || "code").length);
      } else {
        wrap("`", "`", "code");
      }
    },
    ul: function () {
      prefixLines("- ");
    },
    ol: function () {
      prefixLines("", true);
    },
    callout: function () {
      var s = selection();
      var chosen = s.text.slice(s.start, s.end) || "Something worth calling out.";
      var block = "\n::: tip\n" + chosen + "\n:::\n";
      replaceRange(s.start, s.end, block, 9, 9 + chosen.length);
    },
    image: function () {
      fileInput.click();
    },
  };

  editor.querySelectorAll("[data-md]").forEach(function (button) {
    button.addEventListener("click", function () {
      var action = actions[button.getAttribute("data-md")];
      if (action) action();
    });
  });

  textarea.addEventListener("keydown", function (event) {
    var mod = event.metaKey || event.ctrlKey;
    if (!mod) return;
    var key = event.key.toLowerCase();
    if (key === "b") {
      event.preventDefault();
      actions.bold();
    } else if (key === "i") {
      event.preventDefault();
      actions.italic();
    } else if (key === "k") {
      event.preventDefault();
      actions.link();
    }
  });

  document.addEventListener("keydown", function (event) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      var save = form.querySelector('button[value="save"]');
      if (save) save.click();
    }
  });

  // ------------------------------------------------------------ word count

  function countWords(text) {
    var matches = text.match(/[\w'’-]+/g);
    return matches ? matches.length : 0;
  }

  function updateCount() {
    if (!wordCount) return;
    var words = countWords(textarea.value);
    var minutes = Math.max(1, Math.ceil(words / 230));
    var text = words.toLocaleString() + (words === 1 ? " word" : " words") + " · " + minutes + " min read";
    if (maxWords && words > maxWords) {
      text += " · " + (words - maxWords).toLocaleString() + " over the " + maxWords.toLocaleString() + "-word limit";
      wordCount.classList.add("over");
    } else {
      wordCount.classList.remove("over");
    }
    wordCount.textContent = text;
  }

  // ------------------------------------------------------------ preview

  var tabs = editor.querySelectorAll("[data-editor-tab]");
  tabs.forEach(function (tab) {
    tab.hidden = false;
    tab.addEventListener("click", function () {
      var showPreview = tab.getAttribute("data-editor-tab") === "preview";
      tabs.forEach(function (t) {
        t.setAttribute("aria-pressed", String(t === tab));
      });
      textarea.hidden = showPreview;
      preview.hidden = !showPreview;
      editor.querySelectorAll("[data-md]").forEach(function (b) {
        b.disabled = showPreview;
      });
      if (showPreview) renderPreview();
      else textarea.focus();
    });
  });

  function renderPreview() {
    preview.innerHTML = '<p class="muted">Loading preview…</p>';
    window.Copydesk.post(editor.getAttribute("data-preview-url"), { body: textarea.value })
      .then(function (data) {
        preview.innerHTML = '<div class="prose">' + (data.html || '<p class="muted">Nothing to preview yet.</p>') + "</div>";
      })
      .catch(function (error) {
        preview.innerHTML = '<p class="error-text">' + escapeHtml(error.message) + "</p>";
      });
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // ------------------------------------------------------------ image upload

  var fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/jpeg,image/png,image/gif,image/webp";
  fileInput.hidden = true;
  editor.appendChild(fileInput);

  fileInput.addEventListener("change", function () {
    if (fileInput.files.length) upload(fileInput.files[0]);
    fileInput.value = "";
  });

  function upload(file) {
    var s = selection();
    var placeholder = "![Uploading " + file.name.replace(/[[\]]/g, "") + "…]()";
    replaceRange(s.start, s.end, placeholder);
    var body = new FormData();
    body.append("image", file);
    window.Copydesk.post(editor.getAttribute("data-upload-url"), body)
      .then(function (data) {
        var alt = window.prompt("Describe this image for people who can't see it (alt text):", "") || "";
        swapPlaceholder(placeholder, "![" + alt.replace(/[[\]]/g, "") + "](" + data.url + ")");
      })
      .catch(function (error) {
        swapPlaceholder(placeholder, "");
        window.alert(error.message);
      });
  }

  function swapPlaceholder(placeholder, replacement) {
    var index = textarea.value.indexOf(placeholder);
    if (index === -1) return;
    replaceRange(index, index + placeholder.length, replacement);
  }

  textarea.addEventListener("paste", function (event) {
    var items = event.clipboardData && event.clipboardData.files;
    if (items && items.length && /^image\//.test(items[0].type)) {
      event.preventDefault();
      upload(items[0]);
    }
  });

  textarea.addEventListener("dragover", function (event) {
    if (event.dataTransfer && Array.prototype.indexOf.call(event.dataTransfer.types, "Files") !== -1) {
      event.preventDefault();
      editor.classList.add("drop-hint");
    }
  });

  textarea.addEventListener("dragleave", function () {
    editor.classList.remove("drop-hint");
  });

  textarea.addEventListener("drop", function (event) {
    editor.classList.remove("drop-hint");
    var files = event.dataTransfer && event.dataTransfer.files;
    if (files && files.length && /^image\//.test(files[0].type)) {
      event.preventDefault();
      upload(files[0]);
    }
  });

  // ------------------------------------------------------------ local backup

  function snapshot() {
    return {
      title: titleInput ? titleInput.value : "",
      subtitle: subtitleInput ? subtitleInput.value : "",
      body: textarea.value,
      savedAt: new Date().toISOString(),
    };
  }

  function readBackup() {
    try {
      return JSON.parse(localStorage.getItem(backupKey) || "null");
    } catch (e) {
      return null;
    }
  }

  function writeBackup() {
    try {
      localStorage.setItem(backupKey, JSON.stringify(snapshot()));
    } catch (e) {
      /* storage full or blocked; nothing else to do */
    }
  }

  function clearBackup() {
    try {
      localStorage.removeItem(backupKey);
    } catch (e) {
      /* ignore */
    }
  }

  if (backupKey) {
    var backup = readBackup();
    var current = snapshot();
    var differs =
      backup && (backup.body !== current.body || backup.title !== current.title || backup.subtitle !== current.subtitle);
    var notice = document.querySelector("[data-restore-notice]");
    if (differs && notice) {
      notice.hidden = false;
      var when = notice.querySelector("[data-restore-time]");
      if (when) when.textContent = new Date(backup.savedAt).toLocaleString();
      notice.querySelector("[data-restore]").addEventListener("click", function () {
        if (titleInput) titleInput.value = backup.title;
        if (subtitleInput) subtitleInput.value = backup.subtitle;
        textarea.value = backup.body;
        dirty = true;
        updateCount();
        notice.hidden = true;
      });
      notice.querySelector("[data-discard]").addEventListener("click", function () {
        clearBackup();
        notice.hidden = true;
      });
    } else if (backup) {
      clearBackup();
    }
  }

  var backupTimer = null;
  form.addEventListener("input", function () {
    dirty = true;
    updateCount();
    if (backupKey) {
      clearTimeout(backupTimer);
      backupTimer = setTimeout(writeBackup, 800);
    }
  });

  form.addEventListener("submit", function () {
    dirty = false;
    clearTimeout(backupTimer);
    if (backupKey) writeBackup();
  });

  window.addEventListener("beforeunload", function (event) {
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  updateCount();
})();
