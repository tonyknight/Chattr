/* The box's index: a category at a time, sorting people into categories, naming the people nobody
   named and removing people from the box. Without this script every section shows, one after
   another. Changes need Caixa (it saves them in the box); opened any other way the page says so
   and only browses. */
(function () {
  document.documentElement.className += " js";
  // opened in Caixa, which serves the box under /box/
  var CAIXA = /^https?:$/.test(location.protocol) &&
    (location.pathname === "/box/" || location.pathname === "/box/index.html");
  var LABELS = { all: "All people", family: "Family", friends: "Friends", business: "Business",
    acquaintance: "Acquaintances", unrecognized: "Unrecognized", unassigned: "Unassigned",
    groups: "Groups", mail: "Mail" };
  var ASSIGNED = { family: 1, friends: 1, business: 1, acquaintance: 1 };
  var CHOOSING = { unassigned: 1, unrecognized: 1 };  // the views where people are selected
  var NEEDS_CAIXA = "Changing the box works when it is opened in Caixa.";
  // The script brings the styles for what it adds, so a page drawn by an earlier version (Caixa
  // serves every page with the current script) looks right too.
  var STYLE = [
    "[hidden]{display:none !important}",
    ".cell{position:relative;display:flex;flex-direction:column}",
    ".cell .tile{flex:1}",
    ".cell .tool{position:absolute;top:.3rem;right:.4rem;z-index:3;width:1.6rem;height:1.6rem;padding:0;border-radius:50%;border:1px solid var(--line);background:var(--card);color:var(--fg);font:inherit;font-size:.9rem;line-height:1;cursor:pointer;display:grid;place-items:center;box-shadow:0 1px 3px var(--shadow);opacity:0;pointer-events:none;transition:opacity .12s}",
    ".cell:hover .tool,.cell:focus-within .tool{opacity:1;pointer-events:auto}",
    ".cell .tool:hover,.cell .tool:focus-visible{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}",
    ".cell .tool.unassign:hover,.cell .tool.unassign:focus-visible{background:#bf616a;border-color:#bf616a;color:#eceff4}",
    ".tile.selected::after{right:auto !important;left:.6rem}",
    ".cell .naming{position:relative;display:flex;gap:.3rem;align-items:center;margin:.35rem .35rem 0;visibility:hidden}",
    ".cell:hover .naming,.cell:focus-within .naming,.cell .naming.filled{visibility:visible}",
    ".cell .naming input{flex:1;min-width:0;font:inherit;font-size:.8rem;padding:.3rem .5rem;border:1px solid var(--line);border-radius:.45rem;background:var(--card);color:var(--fg)}",
    ".cell .naming input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 30%,transparent)}",
    ".cell .naming .apply{font:inherit;font-size:.78rem;padding:.3rem .6rem;border-radius:.45rem;border:1px solid var(--accent);background:var(--accent);color:var(--on-accent);cursor:pointer}",
    ".cell .naming .suggest{position:absolute;left:0;right:0;top:calc(100% + .2rem);z-index:6;margin:0;padding:.25rem;list-style:none;background:var(--card);border:1px solid var(--line);border-radius:.5rem;box-shadow:0 6px 18px var(--shadow);text-align:left;max-height:14rem;overflow-y:auto}",
    ".cell .naming .suggest li{padding:.3rem .5rem;border-radius:.35rem;font-size:.8rem;cursor:pointer;overflow-wrap:anywhere}",
    ".cell .naming .suggest li[aria-selected=true],.cell .naming .suggest li:hover{background:var(--accent);color:var(--on-accent)}",
    ".cell .naming .suggest li small{display:block;opacity:.75;font-size:.7rem}",
    ".app[data-view=unrecognized] .tile .tip{top:auto;bottom:calc(100% - .3rem)}",
    ".cell:focus-within .tile .tip{display:none !important}",
    "dialog.confirm{border:1px solid var(--line);border-radius:.8rem;background:var(--card);color:var(--fg);padding:1.2rem 1.4rem;max-width:26rem;box-shadow:0 10px 30px var(--shadow)}",
    "dialog.confirm::backdrop{background:rgba(46,52,64,.45)}",
    "dialog.confirm h2{font-size:1.05rem;margin:0 0 .5rem}dialog.confirm p{font-size:.9rem;color:var(--muted);margin:0 0 1rem}",
    "dialog.confirm .buttons{display:flex;justify-content:flex-end;gap:.5rem}",
    "dialog.confirm button{font:inherit;font-size:.88rem;padding:.35rem .9rem;border-radius:.5rem;border:1px solid var(--line);background:var(--panel);color:var(--fg);cursor:pointer}",
    "dialog.confirm button.danger{background:#bf616a;border-color:#bf616a;color:#eceff4}"
  ].join("\n");
  function init() {
    var app = document.querySelector(".app");
    if (!app || app.getAttribute("data-ready")) return;
    app.setAttribute("data-ready", "1");
    var style = document.createElement("style");
    style.textContent = STYLE;
    document.head.appendChild(style);
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
      tiles.forEach(function (t) { cell(t).hidden = !belongs(t, view); });
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
    // The people already in the box, offered as a name is typed (so a number can join someone
    // known); any other name makes a new person.
    var known = [];
    function learnKnown(list) {
      var seen = {};
      known = list.filter(function (p) {
        var key = p.name.toLowerCase();
        if (!p.name || seen[key]) return false;
        seen[key] = 1;
        return true;
      }).sort(function (a, b) { return a.name.localeCompare(b.name); });
    }
    learnKnown(tiles.filter(function (t) { return t.getAttribute("data-name") !== null; })
      .map(function (t) { return { name: t.getAttribute("data-name"), detail: "" }; }));
    if (CAIXA) {
      rpc("people.list", {}).then(function (result) {
        learnKnown(result.people.filter(function (p) { return !p.is_owner; }).map(function (p) {
          return { name: p.name, detail: p.items ? plural(p.items, "item") : "" };
        }));
      }, function () { /* the names on the page will do */ });
    }
    function existing(typed) {
      var key = typed.toLowerCase();
      for (var i = 0; i < known.length; i++) if (known[i].name.toLowerCase() === key) return known[i].name;
      return null;
    }
    function matches(typed) {
      var key = typed.trim().toLowerCase();
      if (!key) return [];
      var first = [], then = [];
      known.forEach(function (p) {
        var name = p.name.toLowerCase();
        if (name.indexOf(key) === 0) first.push(p);
        else if (name.split(/\s+/).some(function (word) { return word.indexOf(key) === 0; }) || name.indexOf(key) > 0) then.push(p);
      });
      return first.concat(then).slice(0, 8);
    }
    function name(tile, typed) {
      var handles = JSON.parse(tile.getAttribute("data-handles") || "[]");
      if (!handles.length) return;
      var joins = existing(typed);
      if (joins) typed = joins;
      say("Saving…");
      var chain = Promise.resolve();
      handles.forEach(function (h) {
        chain = chain.then(function () { return rpc("people.link", { service: h.service, identifier: h.identifier, person: typed }); });
      });
      chain.then(function () {
        if (joins) {  // someone already in the box: the tile joins theirs when the pages are redrawn
          var gone = cell(tile);
          gone.parentNode.removeChild(gone);
          tiles = tiles.filter(function (t) { return t !== tile; });
          say("Linked to " + joins + ". Their page is being redrawn with everything from this number.");
          update();
          return;
        }
        unpick(tile);
        tile.removeAttribute("aria-pressed");
        tile.removeAttribute("data-handles");
        tile.setAttribute("data-name", typed);
        tile.setAttribute("data-category", "");
        tile.querySelector(".name").textContent = typed;
        labels(cell(tile), typed);
        var form = cell(tile).querySelector(".naming");
        if (form) form.parentNode.removeChild(form);
        known.push({ name: typed, detail: "" });
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
        var form = document.createElement("div");
        form.className = "naming";
        form.innerHTML = '<input type="text" name="name" placeholder="Name" aria-label="Name for this person" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false">' +
          '<button type="button" class="apply" hidden>Apply</button><ul class="suggest" role="listbox" aria-label="People in this box" hidden></ul>';
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
      var apply = event.target.closest(".naming .apply");
      if (apply) { applyName(apply.parentNode); return; }
      var option = event.target.closest(".naming .suggest li");
      if (option) { choose(option.parentNode.parentNode, option.getAttribute("data-name")); return; }
      if (event.target.closest(".naming")) return;
      var picked = event.target.closest(".tile");
      if (!picked || !CHOOSING[view] || !choosable(picked, view)) return;
      event.preventDefault();
      pick(picked, event.shiftKey);
    });
    // --- The name field: Apply once something is typed, and the people it could be ---------
    function applyName(form) {
      var field = form.querySelector("input"), typed = field.value.trim();
      if (!typed) { field.focus(); return; }
      suggest(form, false);
      name(cell(form).querySelector(".tile"), typed);
    }
    function choose(form, chosen) {
      var field = form.querySelector("input");
      field.value = chosen;
      typing(form);
      suggest(form, false);
      field.focus();
    }
    function suggest(form, open) {
      var list = form.querySelector(".suggest"), field = form.querySelector("input");
      var found = open ? matches(field.value) : [];
      list.innerHTML = "";
      found.forEach(function (p, i) {
        var item = document.createElement("li");
        item.setAttribute("role", "option");
        item.setAttribute("data-name", p.name);
        item.setAttribute("aria-selected", i === 0 ? "true" : "false");
        item.textContent = p.name;
        if (p.detail) { var small = document.createElement("small"); small.textContent = p.detail; item.appendChild(small); }
        list.appendChild(item);
      });
      list.hidden = !found.length;
      field.setAttribute("aria-expanded", found.length ? "true" : "false");
    }
    function typing(form) {
      var filled = form.querySelector("input").value.trim() !== "";
      form.classList.toggle("filled", filled);
      form.querySelector(".apply").hidden = !filled;
    }
    gallery.addEventListener("input", function (event) {
      var form = event.target.closest && event.target.closest(".naming");
      if (!form) return;
      typing(form);
      suggest(form, true);
    });
    gallery.addEventListener("keydown", function (event) {
      var field = event.target.closest && event.target.closest(".naming input");
      if (!field) return;
      var form = field.parentNode, list = form.querySelector(".suggest");
      var items = Array.prototype.slice.call(list.querySelectorAll("li"));
      var at = items.findIndex(function (li) { return li.getAttribute("aria-selected") === "true"; });
      if ((event.key === "ArrowDown" || event.key === "ArrowUp") && items.length) {
        event.preventDefault();
        var next = event.key === "ArrowDown" ? Math.min(items.length - 1, at + 1) : Math.max(0, at - 1);
        items.forEach(function (li, i) { li.setAttribute("aria-selected", i === next ? "true" : "false"); });
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (!list.hidden && at >= 0) choose(form, items[at].getAttribute("data-name"));
        else applyName(form);
      } else if (event.key === "Escape") {
        suggest(form, false);
      }
    });
    gallery.addEventListener("focusout", function (event) {
      var form = event.target.closest && event.target.closest(".naming");
      if (form && !form.contains(event.relatedTarget)) setTimeout(function () { if (!form.contains(document.activeElement)) suggest(form, false); }, 150);
    });
    // the tools show for the views they belong to
    function tools() {
      Array.prototype.forEach.call(document.querySelectorAll("#people .cell"), function (c) {
        var t = c.querySelector(".tile"), out = c.querySelector(".tool.unassign"), win = c.querySelector(".tool.window"), form = c.querySelector(".naming");
        // one button in the corner: the close box in a category, the open-a-page button where a
        // click selects (Unassigned, Unrecognized)
        if (out) out.hidden = !(CAIXA && ASSIGNED[view] && ASSIGNED[t.getAttribute("data-category")]);
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
