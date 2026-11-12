/* Searchlight site: small, dependency-free enhancements. Every page reads in full without this script. */
(function () {
  "use strict";
  var $ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  // Menu: Escape closes it.
  $("details.menu").forEach(function (menu) {
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); }
    });
  });

  // Concept tiles: one explanation at a time.
  $(".tiles[role=tablist]").forEach(function (list) {
    var tiles = $("[data-concept]", list);
    function pick(tile, focus) {
      tiles.forEach(function (t) {
        var on = t === tile;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute("data-concept"));
        if (panel) panel.hidden = !on;
      });
      if (focus) tile.focus();
    }
    tiles.forEach(function (t, i) {
      t.addEventListener("click", function (e) { e.preventDefault(); pick(t); });
      t.addEventListener("keydown", function (e) {
        var d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (d) { e.preventDefault(); pick(tiles[(i + d + tiles.length) % tiles.length], true); }
      });
    });
    if (tiles.length) pick(tiles[0]);
  });

  // Step-by-step walkthrough.
  $("[data-stepper]").forEach(function (list) {
    var steps = $(".step", list), ctl = list.nextElementSibling, at = 0;
    if (!ctl || !steps.length) return;
    var pos = ctl.querySelector(".pos"), back = ctl.querySelector('[data-step="-1"]'), next = ctl.querySelector('[data-step="1"]');
    function show(i, focus) {
      at = (i + steps.length) % steps.length;
      steps.forEach(function (s, j) { s.hidden = j !== at; });
      pos.textContent = "Step " + (at + 1) + " of " + steps.length;
      back.disabled = at === 0;
      next.textContent = at === steps.length - 1 ? "Start again" : "Next";
      if (focus) { var h = steps[at].querySelector("h3"); h.setAttribute("tabindex", "-1"); h.focus(); }
    }
    back.addEventListener("click", function () { show(at - 1, true); });
    next.addEventListener("click", function () { show(at + 1, true); });
    var hash = /^#step-(\d+)$/.exec(location.hash);
    show(hash ? +hash[1] - 1 : 0);
  });

  // Honest range explorer: Wilson's 95% interval for k passes in n runs.
  function wilson(k, n) {
    if (!n) return [0, 0];
    var z = 1.959964, p = k / n, d = 1 + z * z / n, c = (p + z * z / (2 * n)) / d,
      h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
    return [Math.max(0, c - h) * 100, Math.min(1, c + h) * 100];
  }
  $("[data-explorer]").forEach(function (box) {
    var k = +box.getAttribute("data-k"), n = +box.getAttribute("data-n");
    var v = function (name) { return box.querySelector('[data-v="' + name + '"]'); };
    var ci = box.querySelector(".track .ci"), dot = box.querySelector(".track .dot"), tr = box.querySelector(".track");
    function draw() {
      var r = wilson(k, n), pct = n ? 100 * k / n : 0;
      v("k").textContent = k; v("n").textContent = n; v("pct").textContent = Math.round(pct) + "%";
      v("range").textContent = Math.round(r[0]) + "% to " + Math.round(r[1]) + "%";
      v("say").textContent = r[1] - r[0] > 30 ? "That is wide: with so few runs, the score could move a lot."
        : r[1] - r[0] > 15 ? "Run the same test again and the score could land anywhere in that range."
        : "That is tight: enough runs to separate setups a few points apart.";
      ci.style.left = r[0].toFixed(1) + "%"; ci.style.width = Math.max(0.6, r[1] - r[0]).toFixed(1) + "%";
      dot.style.left = pct.toFixed(1) + "%";
      tr.setAttribute("aria-label", "95% range " + Math.round(r[0]) + " to " + Math.round(r[1]) + "%");
    }
    box.addEventListener("click", function (e) {
      var b = e.target.closest("[data-d]"); if (!b) return;
      var d = b.getAttribute("data-d").split(","), step = +d[1];
      if (d[0] === "k") k = Math.min(n, Math.max(0, k + step));
      else { n = Math.min(500, Math.max(1, n + step)); k = Math.min(k, n); }
      draw();
    });
    draw();
  });

  // Filters: agent, benchmark, provider, search or research, and order. They act on the rows the JSON lists and on
  // any element marked data-f; a group (a board, a finding) hides when nothing inside it matches.
  $("form[data-filters]").forEach(function (form) {
    var json = document.getElementById("filter-data"), facets = {};
    if (json) JSON.parse(json.textContent).forEach(function (r) { facets[r.id] = r; });
    var keys = ["agent", "bench", "provider", "kind"];
    var items = Object.keys(facets).map(function (id) { return document.getElementById(id); }).filter(Boolean)
      .concat($("[data-f]"));
    var groups = $("[data-f-group]").sort(function (a, b) { return depth(b) - depth(a); });
    var empty = document.getElementById("filter-empty"), out = form.querySelector(".count");
    function depth(el) { var d = 0; while ((el = el.parentElement)) d++; return d; }
    function facet(el, key) {
      var f = facets[el.id];
      if (f) return f[key];
      if (key === "provider" && el.hasAttribute("data-providers")) return el.getAttribute("data-providers").split(" ");
      return el.getAttribute("data-" + key);
    }
    function match(el, want) {
      return keys.every(function (key) {
        if (!want[key]) return true;
        var have = facet(el, key);
        if (have == null || have === "") return true;
        return Array.isArray(have) ? have.indexOf(want[key]) >= 0 : have === want[key];
      });
    }
    function apply() {
      var want = {}, shown = 0;
      keys.concat(["sort"]).forEach(function (key) { var s = form.elements[key]; want[key] = s ? s.value : ""; });
      items.forEach(function (el) { var on = match(el, want); el.hidden = !on; if (on) shown++; });
      $("ol[data-limit]").forEach(function (ol) {
        var limit = +ol.getAttribute("data-limit"), seen = 0;
        $("[data-f]", ol).forEach(function (li) { if (!li.hidden && ++seen > limit) li.hidden = true; });
      });
      groups.forEach(function (gr) {
        var inner = $("[data-f], .row[id]", gr);
        gr.hidden = !match(gr, want) || (inner.length > 0 && !inner.some(function (el) { return !el.hidden; }));
      });
      $("ol.rows").forEach(function (ol) {
        var lis = $(":scope > li", ol), by = want.sort === "label" ? "data-sort-label" : "data-rank";
        if (!lis.length || !lis[0].hasAttribute(by)) return;
        lis.sort(function (a, b) {
          var x = a.getAttribute(by), y = b.getAttribute(by);
          return by === "data-rank" ? x - y : x.localeCompare(y);
        }).forEach(function (li) { ol.appendChild(li); });
      });
      shown = items.filter(function (el) { return !el.hidden; }).length;
      if (out) out.textContent = "Showing " + shown + " of " + items.length;
      if (empty) empty.hidden = groups.some(function (gr) { return !gr.hidden && !gr.parentElement.closest("[data-f-group]"); });
      try {
        var q = new URLSearchParams();
        keys.concat(["sort"]).forEach(function (key) { if (want[key] && !(key === "sort" && want[key] === "rank")) q.set(key, want[key]); });
        history.replaceState(null, "", (q.toString() ? "?" + q : location.pathname) + location.hash);
      } catch (e) { /* file:// or sandboxed: filters still work */ }
    }
    var start = new URLSearchParams(location.search);
    keys.concat(["sort"]).forEach(function (key) {
      var s = form.elements[key];
      if (s && start.get(key) && $("option", s).some(function (o) { return o.value === start.get(key); })) s.value = start.get(key);
    });
    form.addEventListener("change", apply);
    form.addEventListener("submit", function (e) { e.preventDefault(); });
    apply();
  });
})();
