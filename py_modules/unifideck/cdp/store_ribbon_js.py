"""The "already owned elsewhere" ribbon, as JS evaluated inside a Steam Store page.

The Gaming Mode store is a separate BrowserView composited *above* the Big
Picture window, so nothing Unifideck renders in Steam's own React tree can
reach it. ProtonDB Badges and DeckySales hit the same wall and inject DOM
into the store document over CDP; this does the same.

Two placements, both verified on-device (2026-10-02):

* an **overlay strip inside the capsule-art container** (the parent of
  ``#gamepad_carousel img[src*="/header"]``). It is visible on first paint
  and *layout-neutral*. An in-flow banner above the media carousel left the
  carousel's own gamepad focus ring stranded at its old coordinates (they
  are computed at focus time, and neither a refocus nor a resize event
  recomputes them), so nothing above the carousel may change height;
* an **in-flow note just before ``#FeatureTarget_purchase-options``**. It
  sits below the initially focused carousel, so it shifts nothing the ring
  depends on, and it is at the point of purchase.

Every node is built with ``createElement``/``textContent``; store logos
arrive as SVG shape data (allowlisted by ``rpc/mixins/_store_ribbon_icons``)
and are rebuilt with ``createElementNS``/``setAttribute``. The payload is
data only, embedded by :func:`build_ribbon_script` as a JSON literal. The
script is idempotent (a window-scoped handle replaces or keeps a previous
instance) and self-verifying (it renders nothing unless ``location.pathname``
is still ``/app/<appid>``, so an evaluation that lost a race with the next
navigation does nothing). Hashed store class names change with every store
deploy; only ``id`` anchors are used.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_RIBBON_FN_JS = r"""(function (DATA) {
  'use strict';
  var HANDLE = '__unifideckOwnershipRibbon';
  var STYLE_ID = 'unifideck-owned-style';
  var OVERLAY_ID = 'unifideck-owned-overlay';
  var NOTE_ID = 'unifideck-owned-note';
  var OVERLAY_ANCHOR = '#gamepad_carousel img[src*="/header"]';
  var NOTE_ANCHOR = '#FeatureTarget_purchase-options';
  var GUTTER_REF = '#FeatureTarget_summary-bar-top';
  var GIVE_UP_MS = 15000;
  var SVG_NS = 'http://www.w3.org/2000/svg';
  // Defence in depth behind the backend allowlist (rpc/mixins/_store_ribbon_icons.py,
  // ICON_TAGS; a test pins this regex to that set).
  var ICON_TAG_RE = /^(?:path|circle|ellipse|rect|polygon|polyline|line|g)$/;
  var ICON_ATTR_RE = /^[a-z][a-z-]*$/;
  var CSS = [
    '.ud-own-note{box-sizing:border-box;margin:0 34px 16px;padding:12px 24px;display:flex;',
    'flex-direction:column;gap:8px;background:rgba(14,20,27,.8);border-inline-start:3px solid #a1cd44;',
    'font-family:"Motiva Sans",Arial,sans-serif;color:#c6d4df;font-size:15px;line-height:1.3;pointer-events:none}',
    '.ud-own-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px}',
    '.ud-own-tag{background:#a1cd44;color:#111113;font-size:11px;padding:1px 6px;letter-spacing:.04em;',
    'text-transform:uppercase;white-space:nowrap}',
    '.ud-own-msg{color:#a1cd44}',
    '.ud-own-chips{display:flex;gap:6px;flex-wrap:wrap}',
    '.ud-own-chip{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.1);',
    'color:#dcdedf;font-size:13px;padding:2px 8px;border-radius:2px}',
    '.ud-own-dot{width:7px;height:7px;border-radius:50%;background:#67707b;flex:none}',
    '.ud-own-chip.ud-inst .ud-own-dot{background:#a1cd44}',
    '.ud-own-logo{width:16px;height:16px;flex:none}',
    '.ud-own-chip.ud-inst .ud-own-logo{color:#a1cd44}',
    '.ud-own-detail{color:#8f98a0}',
    '.ud-own-via{margin-inline-start:auto;font-size:12px;color:#8f98a0}',
    '.ud-own-row.ud-cloud .ud-own-tag,.ud-own-ovl.ud-cloud .ud-own-tag{background:#67707b;color:#fff}',
    '.ud-own-row.ud-cloud .ud-own-msg{color:#c6d4df}',
    '.ud-own-ovl{position:absolute;left:0;right:0;top:0;z-index:2;box-sizing:border-box;padding:6px 10px;',
    'display:flex;align-items:center;gap:8px;white-space:nowrap;pointer-events:none;',
    'background:linear-gradient(90deg,rgba(14,20,27,.92),rgba(14,20,27,.55));',
    'border-inline-start:3px solid #a1cd44;font-family:"Motiva Sans",Arial,sans-serif;font-size:12px;color:#a1cd44}',
    '.ud-own-ovl[dir=rtl]{background:linear-gradient(270deg,rgba(14,20,27,.92),rgba(14,20,27,.55))}',
    '.ud-own-ovl.ud-cloud{border-inline-start-color:#67707b;color:#c6d4df}',
    '.ud-own-ovl .ud-own-tag{font-size:10px;padding:1px 5px}',
    '.ud-own-ovl-text{overflow:hidden;text-overflow:ellipsis;min-width:0}'
  ].join('');

  try {
    var pathRe = new RegExp('^/app/' + Number(DATA.appid) + '(?:/|$)');
    var pathOk = function () { return pathRe.test(window.location.pathname); };
    // Before touching any previous instance: an evaluation that lost the
    // race with the next navigation must not tear down a valid ribbon.
    if (!pathOk()) return 'path-mismatch';
    var key = JSON.stringify(DATA);
    var prev = window[HANDLE];
    if (prev && prev.key === key && prev.alive) return 'unchanged';
    if (prev && typeof prev.teardown === 'function') {
      try { prev.teardown(); } catch (e) { /* a broken predecessor must not block this one */ }
    }

    var el = function (tag, cls, text) {
      var node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    };

    var buildOverlay = function () {
      var o = DATA.overlay;
      var root = el('div', 'ud-own-ovl' + (o.cloud ? ' ud-cloud' : ''));
      root.id = OVERLAY_ID;
      root.dir = DATA.dir;
      root.tabIndex = -1;
      root.appendChild(el('span', 'ud-own-tag', o.tag));
      root.appendChild(el('span', 'ud-own-ovl-text', o.text));
      return root;
    };

    // Store logos arrive as allowlisted SVG data and are rebuilt node by node
    // with createElementNS/setAttribute. Nothing is parsed from markup.
    var appendShapes = function (parent, nodes, depth) {
      if (depth > 3 || !nodes || !nodes.length) return;
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (!n || !ICON_TAG_RE.test(String(n.tag))) continue;
        var shape = document.createElementNS(SVG_NS, n.tag);
        var attrs = n.attrs || {};
        for (var name in attrs) {
          if (!Object.prototype.hasOwnProperty.call(attrs, name)) continue;
          if (!ICON_ATTR_RE.test(name) || name.indexOf('on') === 0 || name.indexOf('href') !== -1) continue;
          shape.setAttribute(name, String(attrs[name]));
        }
        appendShapes(shape, n.children, depth + 1);
        parent.appendChild(shape);
      }
    };

    var buildLogo = function (icon) {
      if (!icon || typeof icon.viewBox !== 'string' || !icon.nodes || !icon.nodes.length) return null;
      var svg = document.createElementNS(SVG_NS, 'svg');
      svg.setAttribute('viewBox', icon.viewBox);
      svg.setAttribute('class', 'ud-own-logo');
      svg.setAttribute('fill', 'currentColor');
      svg.setAttribute('aria-hidden', 'true');
      appendShapes(svg, icon.nodes, 0);
      return svg;
    };

    var buildChip = function (c) {
      var chip = el('span', 'ud-own-chip' + (c.installed ? ' ud-inst' : ''));
      var logo = buildLogo(c.icon);
      if (logo) chip.appendChild(logo);
      else if (c.label) chip.appendChild(el('span', 'ud-own-dot'));
      if (c.label) chip.appendChild(el('span', null, c.label));
      if (c.detail) chip.appendChild(el('span', 'ud-own-detail', c.detail));
      if (c.installed && DATA.installed) chip.appendChild(el('span', 'ud-own-detail', DATA.installed));
      return chip;
    };

    // Match the page's own side gutters (the summary bar's offset inside the
    // purchase block's parent); the CSS default is the 1280-wide layout's.
    var applyGutter = function (note, anchor) {
      var ref = document.querySelector(GUTTER_REF);
      var parent = anchor.parentElement;
      if (!ref || !parent) return;
      var r = ref.getBoundingClientRect();
      var p = parent.getBoundingClientRect();
      if (r.width <= 0 || p.width <= 0) return;
      note.style.marginLeft = Math.max(0, Math.round(r.left - p.left)) + 'px';
      note.style.marginRight = Math.max(0, Math.round(p.right - r.right)) + 'px';
    };

    var buildNote = function (anchor) {
      var root = el('div', 'ud-own-note');
      root.id = NOTE_ID;
      root.dir = DATA.dir;
      root.tabIndex = -1;
      var sections = DATA.sections || [];
      for (var i = 0; i < sections.length; i++) {
        var s = sections[i];
        var row = el('div', 'ud-own-row' + (s.kind === 'cloud' ? ' ud-cloud' : ''));
        row.appendChild(el('span', 'ud-own-tag', s.tag));
        row.appendChild(el('span', 'ud-own-msg', s.message));
        if (s.chips && s.chips.length) {
          var chips = el('span', 'ud-own-chips');
          for (var j = 0; j < s.chips.length; j++) chips.appendChild(buildChip(s.chips[j]));
          row.appendChild(chips);
        }
        if (i === 0 && DATA.via) row.appendChild(el('span', 'ud-own-via', DATA.via));
        root.appendChild(row);
      }
      applyGutter(root, anchor);
      return root;
    };

    var state = { key: key, alive: true, teardown: null };
    var observer = null;
    var giveUp = 0;
    var queued = false;
    var found = false;

    var teardown = function () {
      if (!state.alive) return;
      state.alive = false;
      if (observer) observer.disconnect();
      window.clearTimeout(giveUp);
      [OVERLAY_ID, NOTE_ID, STYLE_ID].forEach(function (id) {
        var node = document.getElementById(id);
        if (node) node.remove();
      });
      if (window[HANDLE] === state) window[HANDLE] = undefined;
    };
    state.teardown = teardown;

    var ensure = function () {
      if (!pathOk()) { teardown(); return; }
      if (!document.getElementById(STYLE_ID)) {
        var style = el('style', null, CSS);
        style.id = STYLE_ID;
        (document.head || document.documentElement).appendChild(style);
      }
      var art = document.querySelector(OVERLAY_ANCHOR);
      var capsule = art && art.parentElement;
      if (capsule && DATA.overlay && !document.getElementById(OVERLAY_ID)) {
        capsule.appendChild(buildOverlay());
      }
      var anchor = document.querySelector(NOTE_ANCHOR);
      if (anchor && anchor.parentElement && !document.getElementById(NOTE_ID)) {
        anchor.parentElement.insertBefore(buildNote(anchor), anchor);
      }
      if (capsule || anchor) found = true;
    };

    window[HANDLE] = state;
    // The store page is React: anchors appear late and re-renders drop our
    // nodes. Re-check once per frame while the DOM is changing.
    observer = new MutationObserver(function () {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(function () {
        queued = false;
        if (state.alive) ensure();
      });
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    giveUp = window.setTimeout(function () {
      if (found || !state.alive) return;
      console.info('[Unifideck] ownership ribbon not shown: neither ' + OVERLAY_ANCHOR +
        ' nor ' + NOTE_ANCHOR + ' exists on this store page layout');
      teardown();
    }, GIVE_UP_MS);
    ensure();
    return 'installed';
  } catch (e) {
    return 'error: ' + (e && e.message ? e.message : String(e));
  }
})"""


def build_ribbon_script(payload: Mapping[str, Any]) -> str:
    """The ribbon function applied to *payload*, ready for ``Runtime.evaluate``.

    ``json.dumps`` output is a valid JS expression; ``ensure_ascii`` also
    escapes U+2028/U+2029, the two characters JSON allows raw but older JS
    parsers reject inside string literals. No other templating touches the
    function body.
    """
    data = json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":"))
    return f"{_RIBBON_FN_JS}({data});"
