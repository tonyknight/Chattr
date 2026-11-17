/* The box's index: a category at a time, sorting people into categories, naming the people nobody
   named and removing people from the box. Without this script every section shows, one after
   another. Changes need Caixa (it saves them in the box); opened any other way the page says so
   and only browses. */
(function () {
  document.documentElement.className += " js";
  var CAIXA = location.pathname === "/api/caixa/box";
  var LABELS = { all: "All people", family: "Family", friends: "Friends", business: "Business",
    acquaintance: "Acquaintances", unrecognized: "Unrecognized", unassigned: "Unassigned",
    groups: "Groups", mail: "Mail" };
  var ASSIGNED = { family: 1, friends: 1, business: 1, acquaintance: 1 };
  var CHOOSING = { unassigned: 1, unrecognized: 1 };  // the views where people are selected
  var NEEDS_CAIXA = "Changing the box works when it is opened in Caixa.";
  function init() {
    var app = document.querySelector(".app");
    if (!app || app.getAttribute("data-ready")) return;
    app.setAttribute("data-ready", "1");
    var tiles = Array.prototype.slice.call(document.querySelectorAll("#people .tile"));
    var title = document.getElementById("view-title");
    var footer = { count: document.getElementById("count"), picked: document.getElementById("picked"),
      actions: document.getElementById("actions"), said: document.getElementById("said") };
    var view = "all", anchor = null;
    function cell(tile) { return tile.parentNode; }
    function belongs(tile, name) {
      var category = tile.getAttribute("data-category");
      if (name === "all") return category !== "unrecognized";
      if (name === "unassigned") return category === "";
      return category === name;
    }
    function shown() { return tiles.filter(function (t) { return belongs(t, view); }); }
    function selected() { return tiles.filter(function (t) { return t.classList.contains("selected"); }); }
    function plural(n, word) { return n.toLocaleString() + " " + word + (n === 1 ? "" : "s"); }
    function people(n) { return plural(n, "person").replace("persons", "people"); }
    function say(text) { footer.said.textContent = text || ""; }
    function rpc(method, params) {
      return fetch("/api/caixa/rpc", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
        body: JSON.stringify({ method: method, params: params }) })
        .then(function (response) { return response.json(); })
        .then(function (reply) {
          if (!reply.ok) throw new Error((reply.error && reply.error.message) || "Caixa refused.");
          return reply.result;
        }, function () { throw new Error("Caixa could not be reached."); });
    }
    function counts() {
      Array.prototype.forEach.call(document.querySelectorAll(".sidebar [data-view]"), function (link) {
        var name = link.getAttribute("data-view"), badge = link.querySelector(".n");
        if (!badge || name === "groups" || name === "mail") return;
        badge.textContent = tiles.filter(function (t) { return belongs(t, name); }).length.toLocaleString();
      });
    }
    function update() {
      if (view === "groups") footer.count.textContent = plural(document.querySelectorAll("#groups .tile").length, "group");
      else if (view === "mail") footer.count.textContent = plural(document.querySelectorAll("#mail li").length, "conversation");
      else footer.count.textContent = people(shown().length);
      var picked = selected().length;
      footer.picked.textContent = CHOOSING[view] && CAIXA ? picked.toLocaleString() + " selected" : "";
      footer.actions.hidden = !(view === "unassigned" && CAIXA && picked);
      app.setAttribute("data-picked", picked ? "1" : "");
      counts();
    }
    function unpick(t) { t.classList.remove("selected"); if (t.hasAttribute("aria-pressed")) t.setAttribute("aria-pressed", "false"); }
    function clear() { selected().forEach(unpick); anchor = null; }
    function choosable(t, name) { return CAIXA && ((name === "unassigned" && t.getAttribute("data-name") !== null) || name === "unrecognized"); }
    function show(name) {
      if (!LABELS[name]) name = "all";
      view = name;
      clear();
      say(CHOOSING[name] && !CAIXA ? NEEDS_CAIXA : "");
      app.setAttribute("data-view", name);
      title.textContent = LABELS[name];
      Array.prototype.forEach.call(document.querySelectorAll(".sidebar [data-view]"), function (link) {
        link.classList.toggle("current", link.getAttribute("data-view") === name);
      });
      tiles.forEach(function (t) {
        var pickable = choosable(t, name);
        t.draggable = pickable && name === "unassigned";
        if (pickable) t.setAttribute("aria-pressed", "false"); else t.removeAttribute("aria-pressed");
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
    function categorize(chosen, category, done) {
      var names = chosen.map(function (t) { return t.getAttribute("data-name"); });
      if (!names.length) return;
      if (!CAIXA) { say(NEEDS_CAIXA); return; }
      say("Saving…");
      rpc("people.categorize", { people: names, category: category }).then(function () {
        chosen.forEach(function (t) { t.setAttribute("data-category", category || ""); unpick(t); t.draggable = false; t.removeAttribute("aria-pressed"); });
        anchor = null;
        say(done(names.length));
        update();
      }, function (error) { say("Not saved: " + error.message); });
    }
    function move(category) {
      categorize(selected(), category, function (n) { return people(n) + " moved to " + LABELS[category] + "."; });
    }

    // --- Removing people from the box ------------------------------------------------------
    var dialog = document.createElement("dialog");
    dialog.className = "confirm";
    dialog.setAttribute("aria-labelledby", "confirm-title");
    dialog.innerHTML = '<h2 id="confirm-title"></h2><p></p><div class="buttons">' +
      '<button type="button" value="cancel">Cancel</button><button type="button" class="danger" value="delete">Delete</button></div>';
    document.body.appendChild(dialog);
    var asking = null;
    dialog.addEventListener("click", function (event) {
      var button = event.target.closest("button");
      if (!button) return;
      dialog.close();
      var then = asking; asking = null;
      if (button.value === "delete" && then) then();
    });
    dialog.addEventListener("cancel", function () { asking = null; });
    function confirmRemoval(chosen) {
      var n = chosen.length;
      dialog.querySelector("h2").textContent = "Delete " + people(n) + " from this box?";
      dialog.querySelector("p").textContent = (n === 1 ? "Their" : "Each one's") +
        " conversations with you and the photos, recordings and files in them are deleted, and later imports leave them out. " +
        "What they wrote in group conversations stays. This can't be undone.";
      asking = function () { remove(chosen); };
      if (dialog.showModal) dialog.showModal(); else if (window.confirm(dialog.querySelector("h2").textContent)) remove(chosen);
      dialog.querySelector("button.danger").focus();
    }
    function remove(chosen) {
      var names = [], handles = [];
      chosen.forEach(function (t) {
        var name = t.getAttribute("data-name");
        if (name !== null) names.push(name);
        else handles = handles.concat(JSON.parse(t.getAttribute("data-handles") || "[]"));
      });
      say("Deleting…");
      rpc("people.delete", { people: names, handles: handles }).then(function () {
        chosen.forEach(function (t) { var c = cell(t); c.parentNode.removeChild(c); });
        tiles = tiles.filter(function (t) { return chosen.indexOf(t) < 0; });
        anchor = null;
        say(people(chosen.length) + " deleted. The pages are being redrawn.");
        update();
      }, function (error) { say("Not deleted: " + error.message); });
    }

    // --- Naming someone nobody named -------------------------------------------------------
    function name(tile, typed) {
      var handles = JSON.parse(tile.getAttribute("data-handles") || "[]");
      if (!handles.length) return;
      say("Saving…");
      var chain = Promise.resolve();
      handles.forEach(function (h) {
        chain = chain.then(function () { return rpc("people.link", { service: h.service, identifier: h.identifier, person: typed }); });
      });
      chain.then(function () {
        unpick(tile);
        tile.removeAttribute("aria-pressed");
        tile.removeAttribute("data-handles");
        tile.setAttribute("data-name", typed);
        tile.setAttribute("data-category", "");
        tile.querySelector(".name").textContent = typed;
        labels(cell(tile), typed);
        var form = cell(tile).querySelector("form.naming");
        if (form) form.parentNode.removeChild(form);
        say(typed + " is kept as a person, in Unassigned.");
        update();
      }, function (error) { say("Not saved: " + error.message); });
    }

    // --- Each tile in a cell, with its tools -----------------------------------------------
    function labels(box, who) {
      var open = box.querySelector(".tool.window"), out = box.querySelector(".tool.unassign");
      if (open) open.setAttribute("aria-label", "Open " + who + " in a new window");
      if (out) out.setAttribute("aria-label", "Move " + who + " back to Unassigned");
    }
    tiles.forEach(function (tile) {
      var box = document.createElement("div");
      box.className = "cell";
      tile.parentNode.insertBefore(box, tile);
      box.appendChild(tile);
      var open = document.createElement("button");
      open.type = "button"; open.className = "tool window"; open.textContent = "↗";
      open.title = "Open in a new window";
      box.appendChild(open);
      if (!CAIXA) { labels(box, tile.querySelector(".name").textContent); return; }
      var out = document.createElement("button");
      out.type = "button"; out.className = "tool unassign"; out.textContent = "×";
      out.title = "Back to Unassigned";
      box.appendChild(out);
      labels(box, tile.querySelector(".name").textContent);
      if (tile.getAttribute("data-name") === null) {
        var form = document.createElement("form");
        form.className = "naming";
        form.innerHTML = '<input type="text" name="name" placeholder="Name" aria-label="Name for this person" autocomplete="off"><button type="button" class="apply">Apply</button>';
        box.appendChild(form);
      }
    });
    var gallery = document.getElementById("people");
    gallery.addEventListener("click", function (event) {
      var tool = event.target.closest(".tool");
      if (tool) {
        event.preventDefault();
        var tile = cell(tool).querySelector(".tile");
        if (tool.classList.contains("window")) window.open(tile.href, "_blank", "noopener");
        else categorize([tile], null, function () { return tile.querySelector(".name").textContent + " moved back to Unassigned."; });
        return;
      }
      var apply = event.target.closest("form.naming .apply");
      if (apply) { applyName(apply.form); return; }
      if (event.target.closest("form.naming")) return;
      var picked = event.target.closest(".tile");
      if (!picked || !CHOOSING[view] || !choosable(picked, view)) return;
      event.preventDefault();
      pick(picked, event.shiftKey);
    });
    // (no form submission: a sandboxed frame, like Caixa's viewer, never sends a submit event)
    function applyName(form) {
      var typed = form.elements.name.value.trim();
      if (!typed) { form.elements.name.focus(); return; }
      name(cell(form).querySelector(".tile"), typed);
    }
    gallery.addEventListener("keydown", function (event) {
      var field = event.target.closest && event.target.closest("form.naming input");
      if (field && event.key === "Enter") { event.preventDefault(); applyName(field.form); }
    });
    // the tools show for the views they belong to
    function tools() {
      Array.prototype.forEach.call(document.querySelectorAll("#people .cell"), function (c) {
        var t = c.querySelector(".tile"), out = c.querySelector(".tool.unassign"), win = c.querySelector(".tool.window"), form = c.querySelector("form.naming");
        if (out) out.hidden = !(ASSIGNED[view] && ASSIGNED[t.getAttribute("data-category")]);
        if (win) win.hidden = !CHOOSING[view];
        if (form) form.hidden = view !== "unrecognized";
      });
    }
    var shownView = show;
    show = function (name) { shownView(name); tools(); };
    var updated = update;
    update = function () { updated(); tools(); };

    document.querySelector(".sidebar").addEventListener("click", function (event) {
      var link = event.target.closest("[data-view]");
      if (!link) return;
      event.preventDefault();
      show(link.getAttribute("data-view"));
      history.replaceState(null, "", "#" + link.getAttribute("data-view"));
    });
    footer.actions.addEventListener("click", function (event) {
      var button = event.target.closest("[data-move]");
      if (button) move(button.getAttribute("data-move"));
    });
    document.addEventListener("keydown", function (event) {
      if (!CHOOSING[view] || (dialog.open)) return;
      if (event.target.closest && event.target.closest("input, textarea, select")) return;
      if (event.key === "Escape") { clear(); update(); }
      else if (event.key === "Delete" || event.key === "Backspace") {
        var chosen = selected();
        if (!chosen.length) return;
        event.preventDefault();
        if (!CAIXA) { say(NEEDS_CAIXA); return; }
        confirmRemoval(chosen);
      }
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
