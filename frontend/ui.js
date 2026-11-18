// Page chrome that doesn't depend on the map: the hideable side panel and the About card.

const PANEL_KEY = "ozwatch.panelHidden";
const ABOUT_KEY = "ozwatch.aboutSeen";
const isPhone = () => window.matchMedia("(max-width: 640px)").matches;

export function initChrome() {
  initPanel();
  initAbout();
}

// ---------- side panel: hide / show ----------

function initPanel() {
  const panel = document.getElementById("panel");
  const show = document.getElementById("panel-show");
  const set = (hidden, remember = true) => {
    panel.hidden = hidden;
    show.hidden = !hidden;
    document.body.classList.toggle("panel-hidden", hidden);
    if (remember) store(PANEL_KEY, hidden ? "1" : "0");
  };
  document.getElementById("panel-hide").addEventListener("click", () => set(true));
  show.addEventListener("click", () => set(false));
  // Phones start with the map visible; desktops remember the last choice.
  const saved = load(PANEL_KEY);
  set(saved == null ? isPhone() : saved === "1", false);
  // After choosing a quick view on a phone, get the panel out of the way.
  window.addEventListener("ozwatch:view", () => isPhone() && set(true, false));
}

// ---------- About card ----------

async function initAbout() {
  const dialog = document.getElementById("about");
  document.getElementById("about-open").addEventListener("click", () => dialog.showModal());
  dialog.addEventListener("close", () => store(ABOUT_KEY, "1"));
  dialog.addEventListener("click", (e) => e.target === dialog && dialog.close()); // click outside closes

  try {
    const site = await (await fetch("/api/site")).json();
    if (site.author) {
      const by = dialog.querySelector(".by");
      by.textContent = `Built by ${site.author} as a data portfolio project.`;
      by.hidden = false;
    }
    const links = dialog.querySelector(".about-links");
    const add = (href, text) => {
      if (!href) return;
      const a = Object.assign(document.createElement("a"), { href, textContent: text, target: "_blank", rel: "noopener" });
      a.className = "link-button";
      links.append(a);
    };
    add(site.github, "View the code on GitHub");
    add(site.github && `${site.github}/blob/main/docs/decisions.md`, "How it works");
    add(site.linkedin, "LinkedIn");
  } catch {
    // the card still works without the extra links
  }

  // First visit: introduce the project. After closing it once, it stays closed.
  if (load(ABOUT_KEY) == null) dialog.showModal();
}

// localStorage can be unavailable (private mode, blocked storage): never let that break the page.
function load(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}
