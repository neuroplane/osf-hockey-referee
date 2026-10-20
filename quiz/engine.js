/* Движок викторины: карточка вопроса → 4 варианта → разбор.
   Наборы вопросов подключаются файлами quiz/data/*.js, которые делают
   window.QUIZ_SETS.push({id, title, daily, questions:[{n,q,table,media,opts,correct,rule}]}).
   Список файлов — в quiz/data/manifest.js. */
(function () {
  var Q = window.QUIZ = {};

  Q.API = '/referee/api';

  // Блок авторизации в шапке: «Войти через Яндекс» или имя + выход.
  // done(user|null) вызывается после загрузки.
  Q.initAuth = function (holder, done) {
    fetch(Q.API + '/me', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (m) {
        if (m.logged) {
          holder.innerHTML = '';
          var n = document.createElement('span');
          n.className = 'uname'; n.textContent = m.name;
          var out = document.createElement('a');
          out.className = 'back'; out.href = '#'; out.textContent = 'выйти';
          out.addEventListener('click', function (e) {
            e.preventDefault();
            fetch(Q.API + '/logout', { method: 'POST', credentials: 'same-origin' })
              .then(function () { location.reload(); });
          });
          holder.appendChild(n); holder.appendChild(out);
          if (done) done(m);
        } else {
          holder.innerHTML = '<a class="back" href="' + m.auth_url + '">Войти через Яндекс</a>';
          if (done) done(null);
        }
      })
      .catch(function () { if (done) done(null); });
  };

  // Заглушка для незалогиненного: прячет навигацию и контент, оставляет вход.
  Q.gate = function (msg) {
    var tb = document.getElementById('topbar');
    if (tb) {
      for (var i = 0; i < tb.children.length; i++) {
        var c = tb.children[i];
        if (c.id !== 'auth') c.style.display = 'none';
      }
    }
    var main = document.querySelector('main');
    main.innerHTML =
      '<div style="text-align:center;padding-top:60px">' +
      '<h2 style="font-size:20px;border:none">Вброс по борту</h2>' +
      '<p style="color:var(--fg-dim);font-size:14px">' + msg + '</p>' +
      '<a href="' + Q.API + '/auth/yandex" style="display:inline-block;margin-top:16px;' +
      'padding:12px 28px;font-size:15px;font-weight:600;color:#10131a;' +
      'background:var(--accent);border-radius:10px;text-decoration:none">' +
      'Войти через Яндекс</a></div>';
  };

  Q.sets = function () { return window.QUIZ_SETS || []; };

  Q.dailyPool = function () {
    var pool = [];
    Q.sets().forEach(function (s) { if (s.daily) pool = pool.concat(s.questions); });
    return pool;
  };

  Q.shuffle = function (arr) {
    var a = arr.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  };

  // детерминированный PRNG по seed-числу (для «вопроса дня» — одинаковые у всех)
  Q.seededShuffle = function (arr, seed) {
    var s = seed >>> 0;
    function rnd() { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }
    var a = arr.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(rnd() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  };

  // Рендер одной карточки в контейнер. onAnswer(ok) вызывается после выбора.
  Q.renderCard = function (box, q, onAnswer) {
    box.querySelector('.qnum').textContent = '№' + q.n + (q.rule ? ' · ' + q.rule : '');
    box.querySelector('.qtext').innerHTML = q.q + (q.table || '');
    var mb = box.querySelector('.qmedia');
    mb.innerHTML = '';
    (q.media || []).forEach(function (m) {
      var a = document.createElement('a');
      a.href = m.url; a.target = '_blank'; a.textContent = '[' + m.kind + ']';
      mb.appendChild(a);
    });
    var fb = box.querySelector('.feedback');
    fb.className = 'feedback'; fb.innerHTML = '';
    var opts = box.querySelector('.opts');
    opts.innerHTML = '';
    var btns = [];
    q.opts.forEach(function (o, i) {
      var b = document.createElement('button');
      b.className = 'opt'; b.textContent = o;
      b.addEventListener('click', function () { pick(i); });
      opts.appendChild(b); btns.push(b);
    });
    function pick(i) {
      var ok = i === q.correct;
      btns.forEach(function (b, j) {
        b.disabled = true;
        if (j === q.correct) b.classList.add('correct');
        else if (j === i) b.classList.add('wrong');
        else b.classList.add('dim');
      });
      fb.className = 'feedback show';
      fb.innerHTML = (ok ? '<b style="color:#8fe3ae">Верно.</b> ' : '<b style="color:#f0a49e">Неверно.</b> ') +
        'Правильный ответ: <b>' + q.opts[q.correct] + '</b>' +
        (q.rule ? '<div class="rule">' + q.rule + '</div>' : '');
      onAnswer(ok, i);
    }
    return { pick: pick };
  };

  // Список ошибок в конце прогона
  Q.renderMistakes = function (box, mistakes) {
    box.innerHTML = '';
    mistakes.forEach(function (q) {
      var d = document.createElement('details');
      var s = document.createElement('summary');
      s.textContent = '№' + q.n + ' — ' + q.q.replace(/<[^>]+>/g, '').slice(0, 90);
      var a = document.createElement('div');
      a.className = 'ans'; a.textContent = '✔ ' + q.opts[q.correct];
      var r = document.createElement('div');
      r.className = 'rule'; r.textContent = q.rule || '';
      d.appendChild(s); d.appendChild(a); d.appendChild(r);
      box.appendChild(d);
    });
  };

  // Загрузка наборов по manifest.js: колбэк получает готовый реестр
  Q.load = function (baseUrl, done) {
    var s = document.createElement('script');
    s.src = baseUrl + 'data/manifest.js';
    s.onload = function () {
      var files = window.QUIZ_MANIFEST || [];
      var left = files.length;
      if (!left) return done();
      files.forEach(function (f) {
        var sc = document.createElement('script');
        sc.src = baseUrl + 'data/' + f;
        sc.onload = sc.onerror = function () { if (!--left) done(); };
        document.head.appendChild(sc);
      });
    };
    document.head.appendChild(s);
  };
})();
