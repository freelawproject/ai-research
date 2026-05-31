// Shared top nav for the citator outreach package.
//
// Why this exists: the same nav structure was previously duplicated across
// 7 HTML pages, so a nav-item rename or reorder required touching every page
// and risked drift. This file is the single source of truth.
//
// Usage in each page:
//   <head>
//     <link rel="stylesheet" href="shared.css">
//     <script src="nav.js" defer></script>
//   </head>
//   <body>
//     ...
//     <nav class="topnav"
//          data-active="experiments.html"
//          data-active-sub="sonnet.html"></nav>
//
// data-active: the top-level tab to mark active (must match an anchor href).
// data-active-sub (optional): a dropdown item to mark active (must match a
//   dropdown anchor href). Used by sonnet.html and haiku_kimi.html so the
//   Experiments parent shows active AND the matching dropdown item is
//   highlighted.
//
// Accessibility: the Experiments dropdown is exposed via a real <button>
// caret with aria-expanded, so it opens on tap (touch devices have no hover)
// and via keyboard. Hover still works for mouse users (CSS). A pure-CSS
// <noscript> fallback nav is present in each page for the JS-disabled case.
(function() {
  const NAV_HTML = `
    <a href="index.html">Overview</a>
    <a href="taxonomy.html">Taxonomy</a>
    <a href="benchmark.html">The Benchmark</a>
    <span class="topnav-dd">
      <a href="experiments.html">Experiments</a><button type="button" class="caret" aria-haspopup="true" aria-expanded="false" aria-label="Toggle Experiments submenu">&#x25BE;</button>
      <span class="dropdown-menu" role="menu">
        <a href="haiku_kimi.html" role="menuitem">Haiku + Kimi (2-stage)</a>
        <a href="sonnet.html" role="menuitem">Sonnet (1-stage)</a>
      </span>
    </span>
    <a href="whats_next.html">What’s next</a>
  `;

  document.querySelectorAll('nav.topnav').forEach(function(nav) {
    nav.innerHTML = NAV_HTML;

    const active = nav.dataset.active;
    if (active) {
      const link = nav.querySelector(
        ':scope > a[href="' + active + '"], :scope > .topnav-dd > a[href="' + active + '"]'
      );
      if (link) {
        link.classList.add('active');
        link.setAttribute('aria-current', 'page');
      }
    }

    const activeSub = nav.dataset.activeSub;
    if (activeSub) {
      const subLink = nav.querySelector('.dropdown-menu a[href="' + activeSub + '"]');
      if (subLink) {
        subLink.classList.add('active');
        subLink.setAttribute('aria-current', 'page');
      }
    }

    // Tap/keyboard toggle for the dropdown (hover is handled in CSS).
    const dd = nav.querySelector('.topnav-dd');
    const caret = dd && dd.querySelector('.caret');
    if (dd && caret) {
      const close = function() {
        dd.classList.remove('open');
        caret.setAttribute('aria-expanded', 'false');
      };
      caret.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        const open = dd.classList.toggle('open');
        caret.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      // Click outside closes the menu.
      document.addEventListener('click', function(e) {
        if (!dd.contains(e.target)) close();
      });
      // Escape closes and returns focus to the caret.
      dd.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') { close(); caret.focus(); }
      });
    }
  });
})();
