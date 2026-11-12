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

  // Filters: agent, benchmark, setup, and search or research. They act on the rows the JSON lists and on any element
  // marked data-f; a group (a board, a finding) hides when nothing inside it matches, and a finding's prose switches
  // to the variant written for the chosen agent, so the words never contradict the rows.
  $("form[data-filters]").forEach(function (form) {
    var json = document.getElementById("filter-data"), facets = {};
    if (json) JSON.parse(json.textContent).forEach(function (r) { facets[r.id] = r; });
    var keys = ["agent", "bench", "provider", "kind"];
    var items = Object.keys(facets).map(function (id) { return document.getElementById(id); }).filter(Boolean)
      .concat($("[data-f]"));
    var groups = $("[data-f-group]").sort(function (a, b) { return depth(b) - depth(a); });
    var empty = document.getElementById("filter-empty");
    function depth(el) { var d = 0; while ((el = el.parentElement)) d++; return d; }
    function facet(el, key) {
      var f = facets[el.id];
      if (f) return f[key];
      if (key === "provider" && el.hasAttribute("data-providers")) return el.getAttribute("data-providers").split(" ");
      return el.getAttribute("data-" + key);
    }
    function match(el, want, skip) {
      return keys.every(function (key) {
        if (!want[key] || key === skip) return true;
        var have = facet(el, key);
        if (have === "-") return false;  // about no agent at all: shown only when no agent is chosen
        if (have == null || have === "") return true;
        return Array.isArray(have) ? have.indexOf(want[key]) >= 0 : have === want[key];
      });
    }
    function value(key) { var f = form.elements[key]; return f ? f.value : ""; }
    function apply() {
      var want = {};
      keys.forEach(function (key) { want[key] = value(key); });
      items.forEach(function (el) {
        // The leader stays beside a chosen setup, so its rank reads in context.
        el.hidden = !(match(el, want) || (want.provider && el.hasAttribute("data-lead") && match(el, want, "provider")));
      });
      groups.forEach(function (gr) {
        var inner = $("[data-f], .row[id]", gr);
        gr.hidden = !match(gr, want) || (inner.length > 0 && !inner.some(function (el) { return !el.hidden; }));
      });
      $("details.allrows").forEach(function (d) {
        d.open = !!want.provider && $(".row[id], [data-f]", d).some(function (el) { return !el.hidden; });
      });
      $("[data-f-group]").forEach(function (gr) {
        var variants = $(":scope > .head > [data-prose]", gr);
        if (!variants.length) return;
        var pick = variants.filter(function (p) { return p.getAttribute("data-prose") === want.agent; })[0] ||
          variants.filter(function (p) { return p.getAttribute("data-prose") === ""; })[0];
        variants.forEach(function (p) { p.hidden = p !== pick; });
      });
      if (empty) empty.hidden = groups.some(function (gr) { return !gr.hidden && !gr.parentElement.closest("[data-f-group]"); });
      try {
        var q = new URLSearchParams();
        keys.forEach(function (key) { if (want[key]) q.set(key, want[key]); });
        history.replaceState(null, "", (q.toString() ? "?" + q : location.pathname) + location.hash);
      } catch (e) { /* file:// or sandboxed: filters still work */ }
    }
    var start = new URLSearchParams(location.search);
    keys.forEach(function (key) {
      $('input[name="' + key + '"]', form).forEach(function (r) { if (start.get(key) === r.value && r.value) r.checked = true; });
    });
    form.addEventListener("change", apply);
    form.addEventListener("submit", function (e) { e.preventDefault(); });
    apply();
  });
})();
