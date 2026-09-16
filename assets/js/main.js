(function () {
  "use strict";

  var root = document.documentElement;
  root.classList.add("js");
  var storageKey = "jiejie-blog-theme";

  function getPreferredTheme() {
    try {
      var saved = localStorage.getItem(storageKey);
      if (saved === "light" || saved === "dark") return saved;
    } catch (e) {
      /* ignore */
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    var isDark = theme === "dark";
    btn.setAttribute("aria-label", isDark ? "切换到浅色模式" : "切换到深色模式");
    btn.setAttribute("aria-pressed", isDark ? "true" : "false");
    var sun = btn.querySelector("[data-icon='sun']");
    var moon = btn.querySelector("[data-icon='moon']");
    if (sun && moon) {
      sun.hidden = !isDark;
      moon.hidden = isDark;
    }
  }

  applyTheme(getPreferredTheme());

  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var next =
          root.getAttribute("data-theme") === "dark" ? "light" : "dark";
        try {
          localStorage.setItem(storageKey, next);
        } catch (e) {
          /* ignore */
        }
        applyTheme(next);
      });
    }

    var navToggle = document.getElementById("nav-toggle");
    var nav = document.getElementById("site-nav");
    if (navToggle && nav) {
      navToggle.addEventListener("click", function () {
        var open = nav.classList.toggle("is-open");
        navToggle.setAttribute("aria-expanded", open ? "true" : "false");
      });

      nav.querySelectorAll("a").forEach(function (link) {
        link.addEventListener("click", function () {
          nav.classList.remove("is-open");
          navToggle.setAttribute("aria-expanded", "false");
        });
      });
    }

    var progress = document.getElementById("progress");
    var header = document.getElementById("site-header");
    if (progress || header) {
      var updateProgress = function () {
        var doc = document.documentElement;
        var scrollTop = doc.scrollTop || document.body.scrollTop;
        if (progress) {
          var height = doc.scrollHeight - doc.clientHeight;
          var ratio = height > 0 ? scrollTop / height : 0;
          progress.style.width = Math.min(100, Math.max(0, ratio * 100)) + "%";
        }
        if (header) {
          header.classList.toggle("is-scrolled", scrollTop > 12);
        }
      };
      updateProgress();
      window.addEventListener("scroll", updateProgress, { passive: true });
      window.addEventListener("resize", updateProgress);
    }

    var reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;
    var reveals = document.querySelectorAll(".reveal");
    if (reveals.length) {
      if (reduceMotion || !("IntersectionObserver" in window)) {
        reveals.forEach(function (el) {
          el.classList.add("is-visible");
        });
      } else {
        var io = new IntersectionObserver(
          function (entries) {
            entries.forEach(function (entry) {
              if (entry.isIntersecting) {
                entry.target.classList.add("is-visible");
                io.unobserve(entry.target);
              }
            });
          },
          { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
        );
        reveals.forEach(function (el) {
          io.observe(el);
        });
      }
    }

    var tocLinks = document.querySelectorAll(".toc-list a[href^='#']");
    var headings = [];
    tocLinks.forEach(function (link) {
      var id = link.getAttribute("href").slice(1);
      var heading = document.getElementById(id);
      if (heading) headings.push({ id: id, el: heading, link: link });
    });

    if (headings.length && "IntersectionObserver" in window) {
      var tocObserver = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            headings.forEach(function (item) {
              item.link.classList.toggle(
                "active",
                item.el === entry.target
              );
            });
          });
        },
        { rootMargin: "-20% 0px -70% 0px", threshold: 0 }
      );
      headings.forEach(function (item) {
        tocObserver.observe(item.el);
      });
    }

    var filterBar = document.getElementById("filter-bar");
    if (filterBar) {
      var items = document.querySelectorAll("[data-tags]");
      var empty = document.getElementById("empty-state");
      var params = new URLSearchParams(location.search);
      var activeGroup = params.get("group") || "";

      function applyFilter(tag, group) {
        var visible = 0;
        items.forEach(function (item) {
          var tags = (item.getAttribute("data-tags") || "").split(/\s+/);
          var g = item.getAttribute("data-group") || "";
          var okTag = !tag || tag === "all" || tags.indexOf(tag) !== -1;
          var okGroup = !group || g === group;
          var show = okTag && okGroup;
          item.hidden = !show;
          if (show) visible += 1;
        });
        if (empty) empty.hidden = visible !== 0;
        var note = document.getElementById("group-filter-note");
        if (note) {
          if (group) {
            note.hidden = false;
            note.textContent = "正在筛选分组：" + group;
          } else {
            note.hidden = true;
          }
        }
      }

      var tagSet = [];
      var groupSet = [];
      items.forEach(function (item) {
        var raw = item.getAttribute("data-tags") || "";
        raw.split(/\s+/).forEach(function (t) {
          if (t && tagSet.indexOf(t) === -1) tagSet.push(t);
        });
        var g = item.getAttribute("data-group") || "";
        if (g && groupSet.indexOf(g) === -1) groupSet.push(g);
      });
      filterBar.innerHTML = "";
      var allBtn = document.createElement("button");
      allBtn.type = "button";
      allBtn.className = "filter-btn";
      allBtn.setAttribute("data-filter", "all");
      allBtn.setAttribute("aria-pressed", activeGroup ? "false" : "true");
      allBtn.textContent = "全部";
      filterBar.appendChild(allBtn);
      tagSet.forEach(function (t) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "filter-btn";
        btn.setAttribute("data-filter", t);
        btn.setAttribute("aria-pressed", "false");
        btn.textContent = t;
        filterBar.appendChild(btn);
      });
      groupSet.forEach(function (g) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "filter-btn filter-btn-group";
        btn.setAttribute("data-group-filter", g);
        btn.setAttribute("aria-pressed", activeGroup === g ? "true" : "false");
        btn.textContent = "组 · " + g;
        filterBar.appendChild(btn);
      });

      filterBar.addEventListener("click", function (event) {
        var btn = event.target.closest(".filter-btn");
        if (!btn) return;
        filterBar.querySelectorAll(".filter-btn").forEach(function (b) {
          b.setAttribute("aria-pressed", b === btn ? "true" : "false");
        });
        if (btn.hasAttribute("data-group-filter")) {
          activeGroup = btn.getAttribute("data-group-filter");
          applyFilter("all", activeGroup);
        } else {
          var tag = btn.getAttribute("data-filter");
          if (tag === "all") activeGroup = "";
          applyFilter(tag, activeGroup);
        }
      });

      if (activeGroup) applyFilter("all", activeGroup);
    }
  });
})();
