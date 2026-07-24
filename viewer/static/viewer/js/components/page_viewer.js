// Alpine CSP components for the pipeline walkthrough.
// This file must load BEFORE alpine-csp.min.js so factories register.

const LS_LAYERS = "extraction.viewer.layers";
const LS_ZOOM = "extraction.viewer.zoom";

document.addEventListener("alpine:init", () => {
  Alpine.data("pageViewer", () => ({
    showContainers: true,
    showYoloRaw: false,
    showDots: true,
    showSupp: false,
    zoom: 100,

    init() {
      try {
        const saved = JSON.parse(localStorage.getItem(LS_LAYERS) || "{}");
        if (typeof saved.containers === "boolean") {
          this.showContainers = saved.containers;
        }
        if (typeof saved.yoloRaw === "boolean") {
          this.showYoloRaw = saved.yoloRaw;
        }
        if (typeof saved.dots === "boolean") this.showDots = saved.dots;
        if (typeof saved.supp === "boolean") this.showSupp = saved.supp;
        const z = parseInt(localStorage.getItem(LS_ZOOM) || "100", 10);
        if (z >= 50 && z <= 400) this.zoom = z;
      } catch (e) {
        /* first visit or corrupted state: keep defaults */
      }
    },

    persist() {
      localStorage.setItem(
        LS_LAYERS,
        JSON.stringify({
          containers: this.showContainers,
          yoloRaw: this.showYoloRaw,
          dots: this.showDots,
          supp: this.showSupp,
        })
      );
      localStorage.setItem(LS_ZOOM, String(this.zoom));
    },

    toggleContainers() {
      this.showContainers = !this.showContainers;
      this.persist();
    },
    toggleYoloRaw() {
      this.showYoloRaw = !this.showYoloRaw;
      this.persist();
    },
    toggleDots() {
      this.showDots = !this.showDots;
      this.persist();
    },
    toggleSupp() {
      this.showSupp = !this.showSupp;
      this.persist();
    },

    zoomIn() {
      this.zoom = Math.min(400, this.zoom + 25);
      this.persist();
    },
    zoomOut() {
      this.zoom = Math.max(50, this.zoom - 25);
      this.persist();
    },
    zoomReset() {
      this.zoom = 100;
      this.persist();
    },

    get wrapStyle() {
      return "width:" + this.zoom + "%";
    },
  }));

  Alpine.data("pageNav", () => ({
    go(event) {
      window.location.href = event.target.value;
    },
  }));
});
