/* MediPulse – futuristic voice assistant (all pages) */
(function () {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let rec, active = false, busy = false;

  const orb = document.createElement("button");
  orb.className = "mp-orb notranslate"; orb.innerHTML = "🎙️"; orb.title = "MediPulse Voice";
  const cap = document.createElement("div");
  cap.className = "mp-caption notranslate";
  document.addEventListener("DOMContentLoaded", () => {
    document.body.append(orb, cap);
    if (!SR) { caption("Voice needs Chrome or Edge."); return; }
    // Chrome remembers mic permission on HTTPS, so this auto-starts on later visits.
    if (localStorage.getItem("mp_voice") !== "off") start();
    else state("");
  });

  function caption(t, ms = 6000) {
    cap.textContent = t; cap.classList.add("show");
    clearTimeout(caption.t); caption.t = setTimeout(() => cap.classList.remove("show"), ms);
  }
  function state(s) { orb.className = "mp-orb notranslate " + s; }

  function speak(text) {
    return new Promise(res => {
      if (!synth) return res();
      synth.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = MediPulseLang.speechCode();
      u.onend = u.onerror = () => { state(active ? "listening" : ""); res(); };
      state("speaking"); synth.speak(u);
    });
  }

  function start() {
    if (!SR) return;
    rec = new SR();
    rec.lang = MediPulseLang.speechCode();
    rec.continuous = false; rec.interimResults = false;
    rec.onstart = () => { active = true; state("listening"); };
    rec.onresult = e => handle(e.results[0][0].transcript);
    rec.onerror = e => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        active = false; state(""); caption("Tap the orb and allow the microphone.");
      }
    };
    rec.onend = () => { if (active && !busy && !synth.speaking) try { rec.start(); } catch (_) {} };
    try { rec.start(); localStorage.setItem("mp_voice", "on"); } catch (_) {}
  }
  function stop() { active = false; try { rec.stop(); } catch (_) {} state(""); localStorage.setItem("mp_voice", "off"); }
  orb.onclick = () => (active ? stop() : start());

  async function handle(text) {
    busy = true; state("thinking"); caption("🗣 " + text);
    try {
      const r = await fetch("/api/voice_intent", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, lang: MediPulseLang.get(), page: location.pathname })
      });
      const d = await r.json();
      caption(d.reply || "", 9000);
      await speak(d.reply || "");
      if (d.route && d.route !== location.pathname) {
        sessionStorage.setItem("mp_pending", JSON.stringify(d));
        location.href = d.route;        // next page picks up the action
        return;
      }
      runAction(d);
    } catch (e) { caption("Connection problem. Try again."); }
    busy = false;
    if (active) try { rec.start(); } catch (_) {}
  }

  // Actions the current page can run (called on arrival, too)
  function runAction(d) {
    if (d.intent === "blood_search" && d.blood_group && typeof window.searchDonors === "function") {
      const sel = document.getElementById("blood_group");
      if (sel) { sel.value = d.blood_group; window.searchDonors(); }
    }
    if (d.intent === "chat" && d.message) {
      const inp = document.getElementById("userInput");
      if (inp && typeof window.sendMessage === "function") { inp.value = d.message; window.sendMessage(); }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const p = sessionStorage.getItem("mp_pending");
    if (p) { sessionStorage.removeItem("mp_pending"); setTimeout(() => runAction(JSON.parse(p)), 800); }
  });
})();
