(function () {
  "use strict";
  var csrf = "";
  var posts = [];

  var els = {
    title: document.getElementById("title"),
    date: document.getElementById("date"),
    tag: document.getElementById("tag"),
    excerpt: document.getElementById("excerpt"),
    content: document.getElementById("content"),
    original: document.getElementById("original-slug"),
    preview: document.getElementById("preview"),
    status: document.getElementById("save-status"),
    who: document.getElementById("who"),
    app: document.getElementById("app"),
    gate: document.getElementById("gate"),
    list: document.getElementById("post-list"),
    count: document.getElementById("post-count"),
    filter: document.getElementById("filter"),
    modeText: document.getElementById("mode-text"),
    cancelEdit: document.getElementById("cancel-edit"),
    deleteBtn: document.getElementById("delete-btn"),
    saveBtn: document.getElementById("save-btn")
  };

  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function sanitizeUrl(url) {
    var u = String(url || "").trim().replace(/[\s\u0000-\u001f]+/g, "");
    if (!u) return "#";
    var low = u.toLowerCase();
    if (low.indexOf("data:") === 0) {
      if (/^data:image\/(png|jpe?g|gif|webp);base64,[a-z0-9+/=]+$/i.test(u)) return u;
      return "#";
    }
    if (/^(javascript|vbscript|file|blob):/i.test(low)) return "#";
    if (/^[a-z][a-z0-9+.-]*:/i.test(low) && !/^(https?:|mailto:)/i.test(low)) return "#";
    return u;
  }

  function inline(s) {
    s = esc(s);
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, function (m, alt, src) {
      return '<img src="' + esc(sanitizeUrl(src)) + '" alt="' + alt + '">';
    });
    s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__(.+?)__/g, "<strong>$1</strong>");
    s = s.replace(/~~(.+?)~~/g, "<del>$1</del>");
    s = s.replace(/\*(.+?)\*/g, "<em>$1</em>");
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, label, href) {
      var safe = esc(sanitizeUrl(href));
      return '<a href="' + safe + '" rel="noopener noreferrer">' + label + "</a>";
    });
    return s;
  }

  function splitTableRow(line) {
    var s = line.trim();
    if (s.charAt(0) === "|") s = s.slice(1);
    if (s.charAt(s.length - 1) === "|") s = s.slice(0, -1);
    return s.split(/(?<!\\)\|/).map(function (c) {
      return c.replace(/\\\|/g, "|").trim();
    });
  }

  function isTableSep(line) {
    var s = (line || "").trim();
    if (!s || s.indexOf("|") === -1) return false;
    return /^\|?[\s:|-]+\|?$/.test(s) && s.indexOf("-") !== -1;
  }

  function mdPreview(md) {
    var lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
    var out = [], para = [], list = null, inCode = false, code = [], inQuote = false, inTable = false;
    var aligns = [];
    function flushPara() {
      if (para.length) { out.push("<p>" + inline(para.join(" ")) + "</p>"); para = []; }
    }
    function closeList() {
      if (list) { out.push(list === "ul" ? "</ul>" : "</ol>"); list = null; }
    }
    function closeQuote() {
      if (inQuote) { out.push("</blockquote>"); inQuote = false; }
    }
    function closeTable() {
      if (inTable) { out.push("</tbody></table></div>"); inTable = false; aligns = []; }
    }
    function closeAll() { flushPara(); closeList(); closeQuote(); closeTable(); }
    function li(content) {
      var m = /^\[([ xX])\]\s*(.*)$/.exec(content.trim());
      if (m) {
        var mark = m[1].toLowerCase() === "x" ? " checked" : "";
        return '<li class="task-item"><input type="checkbox" disabled' + mark + "> " + inline(m[2]) + "</li>";
      }
      return "<li>" + inline(content) + "</li>";
    }
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var t = line.trim();
      if (line.indexOf("```") === 0 || line.indexOf("~~~") === 0) {
        if (!inCode) { closeAll(); inCode = true; code = []; }
        else { out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>"); inCode = false; }
        continue;
      }
      if (inCode) { code.push(line); continue; }
      if (!t) { closeAll(); continue; }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) { closeAll(); out.push("<hr>"); continue; }
      if (t.indexOf("|") !== -1 && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        closeAll();
        var headers = splitTableRow(t);
        var sep = splitTableRow(lines[i + 1]);
        aligns = sep.map(function (c) {
          if (/^:.*:$/.test(c)) return "center";
          if (/:$/.test(c)) return "right";
          if (/^:/.test(c)) return "left";
          return "";
        });
        out.push('<div class="table-wrap"><table><thead><tr>');
        headers.forEach(function (h, idx) {
          var st = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : "";
          out.push("<th" + st + ">" + inline(h) + "</th>");
        });
        out.push("</tr></thead><tbody>");
        inTable = true;
        i++;
        continue;
      }
      if (inTable && t.indexOf("|") !== -1 && t.charAt(0) !== "#") {
        var cells = splitTableRow(t);
        out.push("<tr>");
        cells.forEach(function (c, idx) {
          var st = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : "";
          out.push("<td" + st + ">" + inline(c) + "</td>");
        });
        out.push("</tr>");
        continue;
      }
      if (inTable) closeTable();
      var mh = /^(#{1,6})\s+(.*)$/.exec(t);
      if (mh) {
        closeAll();
        var lv = Math.min(mh[1].length, 4);
        out.push("<h" + lv + ">" + inline(mh[2].trim()) + "</h" + lv + ">");
        continue;
      }
      if (t.charAt(0) === ">") {
        flushPara(); closeList(); closeTable();
        if (!inQuote) { out.push("<blockquote>"); inQuote = true; }
        out.push("<p>" + inline(t.replace(/^>\s?/, "")) + "</p>");
        continue;
      }
      var m1 = /^[-*+]\s+(.*)$/.exec(t);
      if (m1) {
        flushPara(); closeQuote(); closeTable();
        if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
        out.push(li(m1[1]));
        continue;
      }
      var m2 = /^\d+[.)]\s+(.*)$/.exec(t);
      if (m2) {
        flushPara(); closeQuote(); closeTable();
        if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
        out.push(li(m2[1]));
        continue;
      }
      closeQuote(); closeTable();
      para.push(t);
    }
    if (inCode) out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>");
    closeAll();
    return out.join("\n");
  }
  function renderPreview() {
    els.preview.innerHTML = mdPreview(els.content.value) ||
      '<p style="color:var(--haze)">开始输入，这里会实时预览…</p>';
  }
  function slugFromTitle(title) {
    var s = String(title || "").trim().toLowerCase();
    s = s.replace(/[^0-9a-z一-鿿]+/g, "-").replace(/-{2,}/g, "-").replace(/^-|-$/g, "");
    return s.slice(0, 48);
  }

  function stripFrontMatter(md) {
    var text = String(md || "");
    var lines = text.replace(/^\uFEFF/, "").split(/\r?\n/);
    if (lines[0] && lines[0].trim() === "---") {
      for (var i = 1; i < lines.length; i++) {
        if (lines[i].trim() === "---") return lines.slice(i + 1).join("\n");
      }
    }
    return text;
  }

  function isJunkToken(s) {
    var compact = String(s || "").replace(/\s+/g, "");
    if (compact.length >= 40 && /^[A-Za-z0-9+/=]+$/.test(compact)) return true;
    if (compact.length >= 50 && !/[一-鿿]/.test(compact)) {
      if (compact.slice(0, 30).indexOf(" ") === -1 && !/[。！？.!?]/.test(compact)) return true;
    }
    return false;
  }

  function stripMarkdown(md) {
    var text = stripFrontMatter(md);
    text = text.replace(/```[\s\S]*?```/g, "\n");
    text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, "\n");
    text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
    return text;
  }

  function generateExcerpt(md, limit) {
    limit = limit || 78;
    var text = stripMarkdown(md);
    var blocks = text.split(/\n\s*\n/);
    var paragraphs = [];
    for (var b = 0; b < blocks.length; b++) {
      var block = blocks[b].trim();
      if (!block) continue;
      var lines = block.split(/\r?\n/);
      var out = [];
      var skip = false;
      for (var i = 0; i < lines.length; i++) {
        var s = lines[i].trim();
        if (!s) continue;
        if (s.charAt(0) === "#" || s.indexOf("```") === 0) { skip = true; break; }
        if (s.charAt(0) === "|" && s.charAt(s.length - 1) === "|") { skip = true; break; }
        s = s.replace(/^\s*[-*+]\s+/, "");
        s = s.replace(/^\s*\d+[.、)]\s+/, "");
        s = s.replace(/^>\s*/, "");
        s = s.replace(/[*`_~]+/g, "").trim();
        if (!s || isJunkToken(s)) continue;
        out.push(s);
      }
      if (skip || !out.length) continue;
      var para = out.join(" ").replace(/\s+/g, " ").trim();
      if (para.length < 14) continue;
      paragraphs.push(para);
      if (paragraphs.length >= 3) break;
    }
    if (!paragraphs.length) return "";
    var chosen = paragraphs[0];
    if (chosen.length < Math.floor(limit / 2) && paragraphs[1]) chosen += " " + paragraphs[1];
    chosen = chosen.replace(/\s+/g, " ").trim();
    if (isJunkToken(chosen)) return "";
    if (chosen.length <= limit) return chosen;
    var window = chosen.slice(0, limit);
    var best = -1;
    ["。", "！", "？", "；", "…", ".", "!", "?", ";"].forEach(function (sep) {
      var pos = window.lastIndexOf(sep);
      if (pos > best) best = pos;
    });
    if (best >= 18) return window.slice(0, best + 1);
    ["，", ",", "、"].forEach(function (sep) {
      var pos = window.lastIndexOf(sep);
      if (best < 0 && pos >= Math.floor(limit * 0.5)) best = pos;
    });
    if (best >= 0) return window.slice(0, best).replace(/[，,、\s]+$/, "") + "…";
    return window.replace(/[，,、\s]+$/, "") + "…";
  }

  function fillTagOptions(tags) {
    var dl = document.getElementById("tag-options");
    if (!dl) return;
    var list = tags && tags.length ? tags : ["随笔", "技术", "安全", "读书", "生活"];
    dl.innerHTML = list.map(function (t) {
      return "<option>" + esc(t) + "</option>";
    }).join("");
  }

  function setStatus(msg, cls) {
    els.status.textContent = msg;
    els.status.className = "status" + (cls ? " " + cls : "");
  }

  function showGate() {
    els.app.classList.add("hidden");
    els.gate.classList.remove("hidden");
    els.who.textContent = "未登录";
  }

  function setMode(editing, slug) {
    if (editing) {
      els.original.value = slug || "";
      els.modeText.innerHTML = '正在编辑 <span class="editing">' + esc(slug) + "</span>";
      els.cancelEdit.classList.remove("hidden");
      els.deleteBtn.classList.remove("hidden");
      els.saveBtn.textContent = "保存修改";
    } else {
      els.original.value = "";
      els.modeText.textContent = "新建文章";
      els.cancelEdit.classList.add("hidden");
      els.deleteBtn.classList.add("hidden");
      els.saveBtn.textContent = "发布文章";
    }
  }

  function clearForm() {
    els.title.value = "";
    els.excerpt.value = "";
    els.content.value = "";
    els.tag.value = "随笔";
    setMode(false);
    renderPreview();
  }

  function renderList() {
    var q = (els.filter.value || "").trim().toLowerCase();
    var items = posts.filter(function (p) {
      if (!q) return true;
      return (p.title || "").toLowerCase().indexOf(q) >= 0 ||
        (p.slug || "").toLowerCase().indexOf(q) >= 0;
    });
    els.count.textContent = "(" + posts.length + ")";
    if (!items.length) {
      els.list.innerHTML = '<li class="empty">没有匹配的文章</li>';
      return;
    }
    els.list.innerHTML = items.map(function (p) {
      var pinLabel = p.pinned ? "取消置顶" : "置顶";
      return (
        "<li" + (p.pinned ? ' class="pinned"' : "") + ">" +
          '<div class="t">' + (p.pinned ? '<span class="pin-badge">置顶</span>' : "") + esc(p.title) + "</div>" +
          '<div class="s">' + esc(p.date || "") + " · " + esc(p.tag) + " · " + esc(p.slug) + "</div>" +
          '<div class="row-actions">' +
            '<button type="button" data-act="pin" data-slug="' + esc(p.slug) + '" data-pinned="' + (p.pinned ? "1" : "0") + '">' + pinLabel + "</button>" +
            '<button type="button" data-act="edit" data-slug="' + esc(p.slug) + '">编辑</button>' +
            '<a class="btn btn-ghost" href="' + esc(p.url) + '" target="_blank" rel="noopener">查看</a>' +
            '<button type="button" class="btn-danger" data-act="del" data-slug="' + esc(p.slug) + '">删除</button>' +
          "</div>" +
        "</li>"
      );
    }).join("");
  }

  function doPin(slug, pinned) {
    setStatus(pinned ? "置顶中…" : "取消置顶中…");
    fetch("/api/posts/pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ slug: slug, pinned: pinned, csrf: csrf })
    }).then(function (res) {
      return res.json().then(function (data) {
        if (res.status === 401) { showGate(); return; }
        if (res.ok && data.ok) {
          setStatus(pinned ? "已置顶：" + slug : "已取消置顶：" + slug, "ok");
          return loadPosts();
        }
        setStatus(data.error || "操作失败", "err");
      });
    }).catch(function () {
      setStatus("网络错误", "err");
    });
  }

  function loadPosts() {
    return fetch("/api/posts", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "加载失败");
        posts = data.posts || [];
        renderList();
      });
  }

  function startEdit(slug) {
    return fetch("/api/posts/" + encodeURIComponent(slug), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "载入失败");
        var p = data.post;
        els.title.value = p.title || "";
        els.date.value = p.date || "";
        els.tag.value = p.tag || "随笔";
        els.excerpt.value = p.excerpt || "";
        els.content.value = p.content || "";
        if (!p.has_md && !p.content) {
          setStatus("该篇缺少 Markdown 源稿，请粘贴正文后再保存", "err");
        } else {
          setStatus("已载入：" + p.title, "ok");
        }
        setMode(true, p.slug);
        renderPreview();
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
  }

  function doSave() {
    var title = els.title.value.trim();
    if (!title) {
      setStatus("请填写标题", "err");
      els.title.focus();
      return;
    }
    var isEdit = !!els.original.value;
    setStatus(isEdit ? "保存中…" : "发布中…");
    fetch("/api/posts/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        title: title,
        date: els.date.value,
        tag: els.tag.value,
        excerpt: els.excerpt.value.trim(),
        content: els.content.value,
        slug: slugFromTitle(title),
        original_slug: els.original.value,
        overwrite: true,
        csrf: csrf
      })
    }).then(function (res) {
      return res.json().then(function (data) {
        if (res.status === 401) { showGate(); return; }
        if (res.ok && data.ok) {
          els.status.innerHTML = (isEdit ? "已保存 · " : "已发布 · ") + esc(data.path) +
            ' · <a href="' + data.url + '" target="_blank" rel="noopener">打开文章</a>';
          els.status.className = "status ok";
          setMode(true, data.slug);
          localStorage.removeItem("jiejie-write-draft");
          loadPosts().catch(function () {});
          return;
        }
        setStatus(data.error || ("失败 HTTP " + res.status), "err");
      });
    }).catch(function () {
      setStatus("网络错误", "err");
    });
  }

  function doDelete(slug) {
    var title = slug;
    var found = posts.filter(function (p) { return p.slug === slug; })[0];
    if (found) title = found.title;
    if (!window.confirm("确定删除《" + title + "》吗？此操作不可恢复。")) return;
    setStatus("删除中…");
    fetch("/api/posts/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ slug: slug, csrf: csrf })
    }).then(function (res) {
      return res.json().then(function (data) {
        if (res.status === 401) { showGate(); return; }
        if (res.ok && data.ok) {
          if (els.original.value === slug) clearForm();
          setStatus("已删除：" + slug, "ok");
          loadPosts().catch(function () {});
          return;
        }
        setStatus(data.error || "删除失败", "err");
      });
    }).catch(function () {
      setStatus("网络错误", "err");
    });
  }

  els.content.addEventListener("input", renderPreview);

  var excerptGen = document.getElementById("excerpt-gen");
  if (excerptGen) {
    excerptGen.addEventListener("click", function () {
      var text = generateExcerpt(els.content.value, 72);
      if (!text) {
        setStatus("正文为空，无法生成摘要", "err");
        return;
      }
      els.excerpt.value = text;
      setStatus("已根据正文生成摘要", "ok");
    });
  }
  els.filter.addEventListener("input", renderList);

  els.list.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-act]");
    if (!btn) return;
    var slug = btn.getAttribute("data-slug");
    var act = btn.getAttribute("data-act");
    if (act === "edit") {
      startEdit(slug).catch(function (err) {
        setStatus(err.message || "载入失败", "err");
      });
    } else if (act === "del") {
      doDelete(slug);
    } else if (act === "pin") {
      var willPin = btn.getAttribute("data-pinned") !== "1";
      doPin(slug, willPin);
    }
  });

  document.getElementById("new-btn").addEventListener("click", function () {
    clearForm();
    setStatus("已切换到新建");
    els.title.focus();
  });
  document.getElementById("refresh-btn").addEventListener("click", function () {
    loadPosts().then(function () { setStatus("列表已刷新", "ok"); })
      .catch(function (err) { setStatus(err.message || "刷新失败", "err"); });
  });

  // —— 导入 ——
  var importFiles = [];
  var importBox = document.getElementById("import-box");
  var importList = document.getElementById("import-list");
  var importFileInput = document.getElementById("import-file");
  var importFolderInput = document.getElementById("import-folder");
  var importStatus = document.getElementById("import-status");
  var importOverwrite = document.getElementById("import-overwrite");
  var DOC_EXT = { md: 1, markdown: 1, txt: 1, html: 1, htm: 1, docx: 1, zip: 1 };
  var IMG_EXT = { png: 1, jpg: 1, jpeg: 1, gif: 1, webp: 1, svg: 1, bmp: 1 };

  function fmtOf(name) {
    var i = String(name).lastIndexOf(".");
    return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
  }

  function addImportFiles(list) {
    var allowed = {};
    Object.keys(DOC_EXT).forEach(function (k) { allowed[k] = 1; });
    Object.keys(IMG_EXT).forEach(function (k) { allowed[k] = 1; });
    var byKey = {};
    importFiles.forEach(function (item) {
      byKey[(item.path || item.name) + "|" + item.format] = item;
    });
    var added = 0;
    list.forEach(function (file) {
      var fmt = fmtOf(file.name);
      if (!allowed[fmt]) return;
      var rel = file.webkitRelativePath || file.name;
      var key = rel + "|" + fmt;
      if (byKey[key]) return;
      var item = {
        file: file,
        name: file.name,
        path: rel,
        format: fmt
      };
      byKey[key] = item;
      importFiles.push(item);
      added += 1;
    });
    return added;
  }

  function renderImportList() {
    if (!importFiles.length) {
      importList.innerHTML = '<li class="empty">尚未选择文件</li>';
      return;
    }
    var docs = importFiles.filter(function (f) { return DOC_EXT[f.format]; }).length;
    var imgs = importFiles.filter(function (f) { return IMG_EXT[f.format]; }).length;
    importList.innerHTML =
      '<li class="empty">正文 ' + docs + " 个 · 图片 " + imgs + " 个</li>" +
      importFiles.map(function (f, idx) {
        return (
          "<li>" +
            '<span class="fn" title="' + esc(f.path || f.name) + '">' + esc(f.path || f.name) + "</span>" +
            '<span class="fm">' + esc(f.format) + "</span>" +
            '<button type="button" class="rm" data-idx="' + idx + '">移除</button>' +
          "</li>"
        );
      }).join("");
  }

  function openImportBox() {
    importBox.classList.remove("hidden");
    importStatus.textContent = "";
    renderImportList();
  }

  function closeImportBox() {
    importBox.classList.add("hidden");
    importFiles = [];
    importFileInput.value = "";
    if (importFolderInput) importFolderInput.value = "";
    importStatus.textContent = "";
  }

  document.getElementById("import-btn").addEventListener("click", function () {
    openImportBox();
    importFileInput.click();
  });
  document.getElementById("import-folder-btn").addEventListener("click", function () {
    openImportBox();
    if (importFolderInput) importFolderInput.click();
  });
  document.getElementById("import-cancel").addEventListener("click", closeImportBox);

  importFileInput.addEventListener("change", function () {
    addImportFiles(Array.prototype.slice.call(importFileInput.files || []));
    importFileInput.value = "";
    openImportBox();
  });
  if (importFolderInput) {
    importFolderInput.addEventListener("change", function () {
      addImportFiles(Array.prototype.slice.call(importFolderInput.files || []));
      importFolderInput.value = "";
      openImportBox();
    });
  }

  // 拖拽文件/文件夹到左侧列表
  if (importBox) {
    importBox.addEventListener("dragover", function (e) {
      e.preventDefault();
      importBox.classList.add("dragover");
    });
    importBox.addEventListener("dragleave", function () {
      importBox.classList.remove("dragleave");
      importBox.classList.remove("dragover");
    });
    importBox.addEventListener("drop", function (e) {
      e.preventDefault();
      importBox.classList.remove("dragover");
      var dt = e.dataTransfer;
      if (!dt) return;
      var files = Array.prototype.slice.call(dt.files || []);
      addImportFiles(files);
      renderImportList();
    });
  }

  importList.addEventListener("click", function (e) {
    var btn = e.target.closest("button.rm");
    if (!btn) return;
    var idx = parseInt(btn.getAttribute("data-idx"), 10);
    importFiles.splice(idx, 1);
    renderImportList();
  });

  function readAsDataURL(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        var result = String(reader.result || "");
        var comma = result.indexOf(",");
        resolve(comma >= 0 ? result.slice(comma + 1) : "");
      };
      reader.onerror = function () { reject(new Error("读取失败")); };
      reader.readAsDataURL(file);
    });
  }

  function readAsText(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || "")); };
      reader.onerror = function () { reject(new Error("读取失败")); };
      reader.readAsText(file, "utf-8");
    });
  }

  document.getElementById("import-run").addEventListener("click", function () {
    if (!importFiles.length) {
      importStatus.textContent = "请先选择文件";
      importStatus.className = "status err";
      return;
    }
    importStatus.textContent = "解析中…（不会直接发布）";
    importStatus.className = "status";

    Promise.all(importFiles.map(function (item) {
      var isImg = /^(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(item.format);
      if (item.format === "docx" || item.format === "zip" || isImg) {
        return readAsDataURL(item.file).then(function (b64) {
          return {
            name: item.name,
            path: item.path || item.name,
            format: isImg ? "image" : item.format,
            base64: true,
            content: b64
          };
        });
      }
      return readAsText(item.file).then(function (text) {
        return {
          name: item.name,
          path: item.path || item.name,
          format: item.format,
          content: text
        };
      });
    })).then(function (payloadFiles) {
      return fetch("/api/posts/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          csrf: csrf,
          tag: els.tag.value,
          files: payloadFiles
        })
      });
    }).then(function (res) {
      return res.json().then(function (data) {
        if (res.status === 401) { showGate(); return; }
        if (!(res.ok && data.ok)) {
          importStatus.textContent = data.error || "解析失败";
          importStatus.className = "status err";
          return;
        }
        // 载入编辑器，不发布
        setMode(false);
        els.title.value = data.title || "";
        els.date.value = data.date || "";
        els.tag.value = data.tag || "随笔";
        els.excerpt.value = data.excerpt || "";
        els.content.value = data.content || "";
        renderPreview();
        importStatus.innerHTML =
          "已解析「" + esc(data.source || data.title) + "」到编辑器，<strong>尚未发布</strong>。" +
          (data.images ? " 图 " + data.images + " 张。" : "") +
          ((data.missing_images && data.missing_images.length)
            ? " 未找到：" + esc(data.missing_images.join(", "))
            : "") +
          ((data.other_docs && data.other_docs.length)
            ? " 其它正文未载入：" + esc(data.other_docs.join(", "))
            : "") +
          " 请检查右侧预览后点「发布文章」。";
        importStatus.className = "status ok";
        setStatus("已从「" + (data.source || "导入") + "」载入草稿，请预览后发布", "ok");
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    }).catch(function () {
      importStatus.textContent = "网络错误";
      importStatus.className = "status err";
    });
  });
  els.cancelEdit.addEventListener("click", function () {
    clearForm();
    setStatus("已退出编辑");
  });
  els.deleteBtn.addEventListener("click", function () {
    if (els.original.value) doDelete(els.original.value);
  });
  els.saveBtn.addEventListener("click", doSave);

  document.getElementById("draft-btn").addEventListener("click", function () {
    localStorage.setItem("jiejie-write-draft", JSON.stringify({
      title: els.title.value, date: els.date.value, tag: els.tag.value,
      excerpt: els.excerpt.value, content: els.content.value
    }));
    setStatus("草稿已存到浏览器本地", "ok");
  });
  document.getElementById("load-draft-btn").addEventListener("click", function () {
    var raw = localStorage.getItem("jiejie-write-draft");
    if (!raw) { setStatus("没有本地草稿", "err"); return; }
    try {
      var d = JSON.parse(raw);
      els.title.value = d.title || "";
      els.date.value = d.date || els.date.value;
      els.tag.value = d.tag || "随笔";
      els.excerpt.value = d.excerpt || "";
      els.content.value = d.content || "";
      setMode(false);
      renderPreview();
      setStatus("已读入本地草稿", "ok");
    } catch (e) {
      setStatus("草稿损坏", "err");
    }
  });

  document.getElementById("logout").addEventListener("click", function () {
    fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" })
      .then(function () { location.href = "/admin/login"; })
      .catch(function () { location.href = "/admin/login"; });
  });

  fetch("/api/auth/me", { credentials: "same-origin" })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      if (data && data.ok && data.authed) {
        els.gate.classList.add("hidden");
        els.app.classList.remove("hidden");
        csrf = data.csrf;
        els.who.innerHTML = "已登录：<b>" + esc(data.user) + "</b>";
        if (!els.date.value) els.date.value = data.today;
        fillTagOptions(data.tags);
        if (!els.tag.value) els.tag.value = "随笔";
        setMode(false);
        renderPreview();
        return loadPosts();
      }
      showGate();
    })
    .catch(function () { showGate(); });
})();
