/* The box's index: a category at a time, and sorting people into categories. Without this script
   every section shows, one after another. Sorting needs Caixa (it saves the choice in the box);
   opened any other way the page says so and only browses. */
(function () {
  document.documentElement.className += " js";
  var CAIXA = location.pathname === "/api/caixa/box";
  var LABELS = { all: "All people", family: "Family", friends: "Friends", business: "Business",
    acquaintance: "Acquaintances", unrecognized: "Unrecognized", unassigned: "Unassigned",
    groups: "Groups", mail: "Mail" };
  function init() {
    var app = document.querySelector(".app");
    if (!app || app.getAttribute("data-ready")) return;
    app.setAttribute("data-ready", "1");
    var tiles = Array.prototype.slice.call(document.querySelectorAll("#people .tile"));
    var title = document.getElementById("view-title");
    var footer = { count: document.getElementById("count"), picked: document.getElementById("picked"),
      actions: document.getElementById("actions"), said: document.getElementById("said") };
    var view = "all", anchor = null;
    function belongs(tile, name) {
      var category = tile.getAttribute("data-category");
      if (name === "all") return category !== "unrecognized";
      if (name === "unassigned") return category === "";
      return category === name;
    }
    function shown() { return tiles.filter(function (t) { return belongs(t, view); }); }
    function selected() { return tiles.filter(function (t) { return t.classList.contains("selected"); }); }
    function plural(n, word) { return n.toLocaleString() + " " + word + (n === 1 ? "" : "s"); }
    function say(text) { footer.said.textContent = text || ""; }
    function counts() {
      Array.prototype.forEach.call(document.querySelectorAll(".sidebar [data-view]"), function (link) {
        var name = link.getAttribute("data-view"), badge = link.querySelector(".n");
        if (!badge || name === "groups" || name === "mail") return;
        badge.textContent = tiles.filter(function (t) { return belongs(t, name); }).length.toLocaleString();
      });
    }
    function update() {
      var sorting = view === "unassigned";
      if (view === "groups") footer.count.textContent = plural(document.querySelectorAll("#groups .tile").length, "group");
      else if (view === "mail") footer.count.textContent = plural(document.querySelectorAll("#mail li").length, "conversation");
      else footer.count.textContent = plural(shown().length, "person").replace("persons", "people");
      var picked = selected().length;
      footer.picked.textContent = sorting ? picked.toLocaleString() + " selected" : "";
      footer.actions.hidden = !(sorting && CAIXA && picked);
      app.setAttribute("data-picked", picked ? "1" : "");
      counts();
    }
    function clear() { selected().forEach(function (t) { t.classList.remove("selected"); t.setAttribute("aria-pressed", "false"); }); anchor = null; }
    function show(name) {
      if (!LABELS[name]) name = "all";
      view = name;
      clear();
      say(name === "unassigned" && !CAIXA ? "Sorting people into categories works when this box is opened in Caixa." : "");
      app.setAttribute("data-view", name);
      title.textContent = LABELS[name];
      Array.prototype.forEach.call(document.querySelectorAll(".sidebar [data-view]"), function (link) {
        link.classList.toggle("current", link.getAttribute("data-view") === name);
      });
      tiles.forEach(function (t) {
        var sortable = name === "unassigned" && CAIXA && t.getAttribute("data-name") !== null;
        t.draggable = sortable;
        if (sortable) t.setAttribute("aria-pressed", "false"); else t.removeAttribute("aria-pressed");
      });
      update();
    }
    function pick(tile, range) {
      if (range && anchor) {
        var list = shown(), from = list.indexOf(anchor), to = list.indexOf(tile);
        if (from > to) { var swap = from; from = to; to = swap; }
        list.slice(from, to + 1).forEach(function (t) { t.classList.add("selected"); t.setAttribute("aria-pressed", "true"); });
      } else {
        var on = !tile.classList.contains("selected");
        tile.classList.toggle("selected", on);
        tile.setAttribute("aria-pressed", on ? "true" : "false");
        anchor = tile;
      }
      update();
    }
    function move(category) {
      var chosen = selected(), names = chosen.map(function (t) { return t.getAttribute("data-name"); });
      if (!names.length) return;
      if (!CAIXA) { say("Sorting people into categories works when this box is opened in Caixa."); return; }
      say("Saving…");
      fetch("/api/caixa/rpc", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
        body: JSON.stringify({ method: "people.categorize", params: { people: names, category: category } }) })
        .then(function (response) { return response.json(); })
        .then(function (reply) {
          if (!reply.ok) { say("Not saved: " + ((reply.error && reply.error.message) || "Caixa refused.")); return; }
          chosen.forEach(function (t) { t.setAttribute("data-category", category); t.classList.remove("selected"); t.draggable = false; t.removeAttribute("aria-pressed"); });
          anchor = null;
          say(plural(names.length, "person").replace("persons", "people") + " moved to " + LABELS[category] + ".");
          update();
        })
        .catch(function () { say("Not saved: Caixa could not be reached."); });
    }
    document.querySelector(".sidebar").addEventListener("click", function (event) {
      var link = event.target.closest("[data-view]");
      if (!link) return;
      event.preventDefault();
      show(link.getAttribute("data-view"));
      history.replaceState(null, "", "#" + link.getAttribute("data-view"));
    });
    document.getElementById("people").addEventListener("click", function (event) {
      var tile = event.target.closest(".tile");
      if (!tile || view !== "unassigned" || !CAIXA || tile.getAttribute("data-name") === null) return;
      event.preventDefault();
      pick(tile, event.shiftKey);
    });
    footer.actions.addEventListener("click", function (event) {
      var button = event.target.closest("[data-move]");
      if (button) move(button.getAttribute("data-move"));
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && view === "unassigned") { clear(); update(); }
    });
    document.addEventListener("dragstart", function (event) {
      var tile = event.target.closest && event.target.closest(".tile");
      if (!tile || !tile.draggable) return;
      if (!tile.classList.contains("selected")) { clear(); pick(tile, false); }
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", selected().length + " people");
      app.classList.add("dragging");
    });
    document.addEventListener("dragend", function () {
      app.classList.remove("dragging");
      Array.prototype.forEach.call(document.querySelectorAll(".drop"), function (el) { el.classList.remove("drop"); });
    });
    Array.prototype.forEach.call(document.querySelectorAll(".sidebar [data-category]"), function (target) {
      target.addEventListener("dragover", function (event) {
        if (!app.classList.contains("dragging")) return;
        event.preventDefault();
        target.classList.add("drop");
      });
      target.addEventListener("dragleave", function () { target.classList.remove("drop"); });
      target.addEventListener("drop", function (event) {
        event.preventDefault();
        target.classList.remove("drop");
        move(target.getAttribute("data-category"));
      });
    });
    show((location.hash || "#all").slice(1));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
