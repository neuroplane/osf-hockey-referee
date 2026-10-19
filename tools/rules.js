// Поиск по документу: подсветка всех совпадений + переход между ними.
// Срабатывание: ввод текста (>= 2 символов). Enter — следующее, Shift+Enter — предыдущее.
// Esc — снять подсветку.

(function () {
  var input = document.getElementById('search-input');
  var countEl = document.getElementById('search-count');
  var hits = [];
  var current = -1;
  var timer = null;

  function clearMarks() {
    document.querySelectorAll('mark.hit').forEach(function (m) {
      var p = m.parentNode;
      p.replaceChild(document.createTextNode(m.textContent), m);
      p.normalize();
    });
    hits = [];
    current = -1;
    countEl.textContent = '';
  }

  function highlight(query) {
    clearMarks();
    if (query.length < 2) return;
    var q = query.toLowerCase();
    var main = document.querySelector('main') || document.body;
    var walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var tag = node.parentNode && node.parentNode.nodeName;
        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'MARK') return NodeFilter.FILTER_REJECT;
        return node.nodeValue.toLowerCase().indexOf(q) !== -1
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      var text = node.nodeValue;
      var low = text.toLowerCase();
      var frag = document.createDocumentFragment();
      var pos = 0, i;
      while ((i = low.indexOf(q, pos)) !== -1) {
        if (i > pos) frag.appendChild(document.createTextNode(text.slice(pos, i)));
        var mark = document.createElement('mark');
        mark.className = 'hit';
        mark.textContent = text.slice(i, i + q.length);
        frag.appendChild(mark);
        hits.push(mark);
        pos = i + q.length;
      }
      frag.appendChild(document.createTextNode(text.slice(pos)));
      node.parentNode.replaceChild(frag, node);
    });
    if (hits.length) { jump(0); }
    else countEl.textContent = '0';
  }

  function jump(n) {
    if (!hits.length) return;
    if (current >= 0) hits[current].classList.remove('current');
    current = (n + hits.length) % hits.length;
    hits[current].classList.add('current');
    hits[current].scrollIntoView({ block: 'center', behavior: 'smooth' });
    countEl.textContent = (current + 1) + ' / ' + hits.length;
  }

  input.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(function () { highlight(input.value.trim()); }, 200);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); jump(current + (e.shiftKey ? -1 : 1)); }
    if (e.key === 'Escape') { input.value = ''; clearMarks(); input.blur(); }
  });
  document.getElementById('search-prev').addEventListener('click', function () { jump(current - 1); });
  document.getElementById('search-next').addEventListener('click', function () { jump(current + 1); });

  // '/' фокусирует поиск, как в большинстве документаций
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement !== input) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });

  // ---------- мобильное меню ----------
  var toc = document.getElementById('TOC');
  var backdrop = document.getElementById('toc-backdrop');
  var menuBtn = document.getElementById('menu-toggle');

  function closeMenu() {
    toc.classList.remove('open');
    backdrop.classList.remove('open');
  }
  menuBtn.addEventListener('click', function () {
    var open = toc.classList.toggle('open');
    backdrop.classList.toggle('open', open);
  });
  backdrop.addEventListener('click', closeMenu);
  toc.addEventListener('click', function (e) {
    if (e.target.closest('a')) closeMenu(); // закрыть после перехода по пункту
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeMenu();
  });

  // ---------- компактные метки правил: «ПРАВИЛО 10. …» → «П10. …» ----------
  document.querySelectorAll('#TOC a, main ul a[href^="#"]').forEach(function (a) {
    a.textContent = a.textContent.replace(/^ПРАВИЛО\s+(\d+)/, 'П$1');
  });

  // ---------- заголовки разделов в меню — без ссылок, визуально отделены от правил ----------
  var firstLi = toc.querySelector('li');
  var firstH1 = document.querySelector('main h1');
  if (firstLi && firstH1 && firstLi.textContent.trim() === firstH1.textContent.trim()) firstLi.remove();
  // правила кликабельны («П10»), остальные пункты — заголовки-разделители;
  // если пунктов-правил нет вообще (страница фактов) — все пункты остаются ссылками
  var hasRuleLinks = Array.prototype.some.call(toc.querySelectorAll('a'), function (a) {
    return /^П\d/.test(a.textContent.trim());
  });
  if (hasRuleLinks) {
    toc.querySelectorAll('a').forEach(function (a) {
      if (/^П\d/.test(a.textContent.trim())) return;
      var s = document.createElement('span');
      s.className = 'toc-section';
      s.textContent = a.textContent;
      a.parentNode.replaceChild(s, a);
    });
  }

  // ---------- широкие таблицы: скролл внутри, без горизонтального скролла страницы ----------
  document.querySelectorAll('main table').forEach(function (t) {
    var w = document.createElement('div');
    w.className = 'table-scroll';
    t.parentNode.insertBefore(w, t);
    w.appendChild(t);
  });

  // ---------- «к содержанию» в конце каждого правила ----------
  var heads = document.querySelectorAll('main h1, main h2');
  heads.forEach(function (h, i) {
    if (i === 0) return;
    var a = document.createElement('a');
    a.className = 'to-top';
    a.href = '#';
    a.textContent = '↑ к содержанию';
    a.addEventListener('click', function (e) {
      e.preventDefault();
      window.scrollTo({ top: 0, behavior: 'smooth' });
      if (window.matchMedia('(max-width: 900px)').matches) {
        toc.classList.add('open');
        backdrop.classList.add('open');
      }
    });
    var wrap = document.createElement('div');
    wrap.className = 'to-top-wrap';
    wrap.appendChild(a);
    // вставить перед началом следующего раздела (или в конец документа)
    h.parentNode.insertBefore(wrap, h);
  });

  // Подсветка текущего пункта в оглавлении при прокрутке
  var tocLinks = Array.prototype.slice.call(document.querySelectorAll('#TOC a'));
  var map = new Map();
  tocLinks.forEach(function (a) {
    var id = decodeURIComponent(a.getAttribute('href').slice(1));
    var h = document.getElementById(id);
    if (h) map.set(h, a);
  });
  var spy = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (en.isIntersecting) {
        tocLinks.forEach(function (a) { a.classList.remove('active'); });
        var a = map.get(en.target);
        if (a) a.classList.add('active');
      }
    });
  }, { rootMargin: '-10% 0px -80% 0px' });
  map.forEach(function (_, h) { spy.observe(h); });
})();
