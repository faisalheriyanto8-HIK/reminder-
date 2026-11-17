(function () {
  var btn = document.getElementById('copy');
  var msg = document.getElementById('msg');
  btn.addEventListener('click', function () {
    var text = msg.innerText;
    var done = function () { btn.textContent = 'Tersalin ✓'; setTimeout(function () { btn.textContent = 'Salin pesan'; }, 1800); };
    var fallback = function () {
      var r = document.createRange(); r.selectNodeContents(msg);
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      btn.textContent = 'Tekan Ctrl/⌘ + C';
    };
    try { navigator.clipboard.writeText(text).then(done, fallback); } catch (e) { fallback(); }
  });

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var words = document.querySelectorAll('#cap span');
  var i = 0;
  setInterval(function () {
    words[i].classList.remove('on');
    i = (i + 1) % words.length;
    words[i].classList.add('on');
  }, 450);
})();
