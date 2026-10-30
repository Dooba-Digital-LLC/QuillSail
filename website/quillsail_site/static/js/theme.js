/* Apply the saved appearance before the page draws. Light unless someone chose Dark or Automatic. */
(function () {
  var root = document.documentElement;
  var choice = "light";
  try {
    choice = localStorage.getItem("quillsail-appearance") || "light";
  } catch (error) {}
  if (choice !== "dark" && choice !== "auto") choice = "light";
  root.classList.remove("no-js");
  root.classList.add("js");
  root.setAttribute("data-appearance", choice);
  if (choice === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
})();
