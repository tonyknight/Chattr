/* Pictures, full size: click one to see it over the page, then the arrows (or the arrow keys) for
   the pictures before and after it, and Escape, a click beside it or the cross to close. The page
   works without this: every picture is also a link to its file. */
(function () {
  var box = null, picture = null, shown = -1, last = null;
  function pictures() { return document.querySelectorAll(".attachment a > img"); }
  function open() { return box !== null && box.classList.contains("open"); }
  function make() {
    box = document.createElement("div");
    box.className = "lightbox";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Picture");
    box.innerHTML = '<button type="button" class="lb-prev" aria-label="Previous picture">&lsaquo;</button>' +
      '<img alt=""><button type="button" class="lb-next" aria-label="Next picture">&rsaquo;</button>' +
      '<button type="button" class="lb-close" aria-label="Close">&times;</button>';
    picture = box.querySelector("img");
    document.body.appendChild(box);
  }
  function show(index) {
    var all = pictures();
    if (index < 0 || index >= all.length) return;
    shown = index;
    last = all[index];
    picture.src = last.parentNode.href;
    picture.alt = last.alt;
    box.classList.add("open");
    box.querySelector(".lb-prev").disabled = index === 0;
    box.querySelector(".lb-next").disabled = index === all.length - 1;
  }
  function close() {
    box.classList.remove("open");
    picture.removeAttribute("src");
    if (last) last.scrollIntoView({ block: "center" });
    shown = -1;
  }
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (open()) {
      event.preventDefault();
      if (target.closest(".lb-prev")) show(shown - 1);
      else if (target.closest(".lb-next")) show(shown + 1);
      else if (target !== picture) close();
      return;
    }
    var image = target.closest ? target.closest(".attachment a > img") : null;
    if (!image || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (box === null) make();
    show(Array.prototype.indexOf.call(pictures(), image));
  });
  document.addEventListener("keydown", function (event) {
    if (!open()) return;
    if (event.key === "Escape") close();
    else if (event.key === "ArrowLeft") show(shown - 1);
    else if (event.key === "ArrowRight") show(shown + 1);
    else return;
    event.preventDefault();
  });
})();
