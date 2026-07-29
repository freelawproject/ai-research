// Alpine CSP components for the alignment viewer.
// This file must load BEFORE alpine-csp.min.js so factories register.

const LS_ALIGN_LAYERS = "extraction.viewer.alignLayers";
const LS_ALIGN_ZOOM = "extraction.viewer.alignZoom";
const LS_ALIGN_MODE = "extraction.viewer.alignMode";

document.addEventListener("alpine:init", () => {
  Alpine.data("alignViewer", () => ({
    // Container-YOLO is informational here, so it starts hidden: the
    // aligned groups are what the page is actually built from.
    showContainers: false,
    showYoloRaw: false,
    showGroups: true,
    showWeak: true,
    showBoundary: true,
    showDots: false,
    showMistral: false,
    showSurya: false,
    zoom: 100,
    mode: "groups",

    init() {
      try {
        const saved = JSON.parse(
          localStorage.getItem(LS_ALIGN_LAYERS) || "{}",
        );
        for (const [key, prop] of Object.entries({
          containers: "showContainers",
          yoloRaw: "showYoloRaw",
          groups: "showGroups",
          weak: "showWeak",
          boundary: "showBoundary",
          dots: "showDots",
          mistral: "showMistral",
          surya: "showSurya",
        })) {
          if (typeof saved[key] === "boolean") this[prop] = saved[key];
        }
        const z = parseInt(
          localStorage.getItem(LS_ALIGN_ZOOM) || "100",
          10,
        );
        if (z >= 50 && z <= 400) this.zoom = z;
        const m = localStorage.getItem(LS_ALIGN_MODE);
        if (m === "groups" || m === "reading") this.mode = m;
      } catch (e) {
        /* first visit or corrupted state: keep the defaults */
      }
    },

    persist() {
      localStorage.setItem(
        LS_ALIGN_LAYERS,
        JSON.stringify({
          containers: this.showContainers,
          yoloRaw: this.showYoloRaw,
          groups: this.showGroups,
          weak: this.showWeak,
          boundary: this.showBoundary,
          dots: this.showDots,
          mistral: this.showMistral,
          surya: this.showSurya,
        }),
      );
    },

    toggleContainers() {
      this.showContainers = !this.showContainers;
      this.persist();
    },
    toggleYoloRaw() {
      this.showYoloRaw = !this.showYoloRaw;
      this.persist();
    },
    toggleGroups() {
      this.showGroups = !this.showGroups;
      this.persist();
    },
    toggleWeak() {
      this.showWeak = !this.showWeak;
      this.persist();
    },
    toggleBoundary() {
      this.showBoundary = !this.showBoundary;
      this.persist();
    },
    toggleDots() {
      this.showDots = !this.showDots;
      this.persist();
    },
    toggleMistral() {
      this.showMistral = !this.showMistral;
      this.persist();
    },
    toggleSurya() {
      this.showSurya = !this.showSurya;
      this.persist();
    },

    showGroupList() {
      this.mode = "groups";
      localStorage.setItem(LS_ALIGN_MODE, this.mode);
    },
    showReading() {
      this.mode = "reading";
      localStorage.setItem(LS_ALIGN_MODE, this.mode);
    },

    // CSP Alpine evaluates property and method references only, never
    // expressions, so the comparisons live here.
    get isGroups() {
      return this.mode === "groups";
    },
    get isReading() {
      return this.mode === "reading";
    },
    get groupsTab() {
      return this.mode === "groups" ? "tab-on" : "";
    },
    get readingTab() {
      return this.mode === "reading" ? "tab-on" : "";
    },

    get wrapStyle() {
      return "width:" + this.zoom + "%";
    },
    zoomIn() {
      this.zoom = Math.min(400, this.zoom + 25);
      localStorage.setItem(LS_ALIGN_ZOOM, String(this.zoom));
    },
    zoomOut() {
      this.zoom = Math.max(50, this.zoom - 25);
      localStorage.setItem(LS_ALIGN_ZOOM, String(this.zoom));
    },
    zoomReset() {
      this.zoom = 100;
      localStorage.setItem(LS_ALIGN_ZOOM, String(this.zoom));
    },
  }));

  // Volume <select> in the header: navigate on pick.
  Alpine.data("volumeNav", () => ({
    go(event) {
      if (event.target.value) window.location.href = event.target.value;
    },
  }));
});
