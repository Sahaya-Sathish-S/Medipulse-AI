/* MediPulse – one-click full-site language switcher (uses Google Website Translator) */
(function () {
  const LANGS = [
    { code: "en", label: "English", speech: "en-IN" },
    { code: "ta", label: "தமிழ்",   speech: "ta-IN" },
    { code: "hi", label: "हिन्दी",   speech: "hi-IN" },
    { code: "te", label: "తెలుగు",  speech: "te-IN" },
    { code: "ml", label: "മലയാളം", speech: "ml-IN" },
    { code: "kn", label: "ಕನ್ನಡ",   speech: "kn-IN" }
  ];

  function setCookie(name, value) {
    const host = location.hostname;
    document.cookie = `${name}=${value};path=/`;
    document.cookie = `${name}=${value};path=/;domain=${host}`;
  }

  window.MediPulseLang = {
    LANGS,
    get() { return localStorage.getItem("mp_lang") || "en"; },
    speechCode() { return (LANGS.find(l => l.code === this.get()) || LANGS[0]).speech; },
    set(code) {
      localStorage.setItem("mp_lang", code);
      setCookie("googtrans", code === "en" ? "/en/en" : `/en/${code}`);
      location.reload();               // Google Translate applies on load
    }
  };

  // Apply saved language on every page
  const saved = MediPulseLang.get();
  setCookie("googtrans", saved === "en" ? "/en/en" : `/en/${saved}`);

  // Hidden Google Translate host
  const holder = document.createElement("div");
  holder.id = "google_translate_element";
  holder.style.display = "none";
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(holder));

  window.googleTranslateElementInit = function () {
    new google.translate.TranslateElement(
      { pageLanguage: "en", includedLanguages: LANGS.map(l => l.code).join(","), autoDisplay: false },
      "google_translate_element"
    );
  };
  const s = document.createElement("script");
  s.src = "https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit";
  document.head.appendChild(s);

  // Language button (top-right)
  document.addEventListener("DOMContentLoaded", () => {
    const wrap = document.createElement("div");
    wrap.className = "mp-lang notranslate";
    wrap.innerHTML = `<button class="mp-lang-btn" aria-label="Language">🌐 <span>${
      (LANGS.find(l => l.code === saved) || LANGS[0]).label}</span></button>
      <div class="mp-lang-menu">${LANGS.map(l =>
        `<button data-code="${l.code}">${l.label}</button>`).join("")}</div>`;
    document.body.appendChild(wrap);
    wrap.querySelector(".mp-lang-btn").onclick = () => wrap.classList.toggle("open");
    wrap.querySelectorAll(".mp-lang-menu button").forEach(b =>
      b.onclick = () => MediPulseLang.set(b.dataset.code));
  });
})();
