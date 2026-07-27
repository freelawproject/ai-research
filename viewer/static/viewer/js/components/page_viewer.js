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
    showSupp2: false,
    showDropped: true,
    showInImage: true,
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
        if (typeof saved.supp2 === "boolean") this.showSupp2 = saved.supp2;
        if (typeof saved.dropped === "boolean") {
          this.showDropped = saved.dropped;
        }
        if (typeof saved.inImage === "boolean") {
          this.showInImage = saved.inImage;
        }
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
          supp2: this.showSupp2,
          dropped: this.showDropped,
          inImage: this.showInImage,
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
    toggleSupp2() {
      this.showSupp2 = !this.showSupp2;
      this.persist();
    },
    toggleDropped() {
      this.showDropped = !this.showDropped;
      this.persist();
    },
    toggleInImage() {
      this.showInImage = !this.showInImage;
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

  // The three model dropdowns: selections persist across pages via
  // localStorage. Forms with data-restore="true" (the home button's
  // hidden fields) restore the last-used combination; page forms are
  // prefilled from the URL and only save.
  const LS_MODELS = "extraction.viewer.models";
  Alpine.data("modelPicker", () => ({
    init() {
      if (this.$root.dataset.restore !== "true") return;
      try {
        const saved = JSON.parse(localStorage.getItem(LS_MODELS) || "null");
        if (!saved) return;
        for (const [name, value] of Object.entries(saved)) {
          const el = this.$root.querySelector(`[name="${name}"]`);
          if (!el) continue;
          if (el.tagName === "SELECT") {
            if ([...el.options].some((o) => o.value === value)) {
              el.value = value;
            }
          } else {
            el.value = value;
          }
        }
      } catch (e) {
        /* first visit or corrupted state: keep defaults */
      }
    },

    save() {
      const data = {};
      this.$root.querySelectorAll("select[name]").forEach((el) => {
        data[el.name] = el.value;
      });
      localStorage.setItem(LS_MODELS, JSON.stringify(data));
    },
  }));

  // Synced scrolling for the reconstruct card: scrolling any panel scrolls
  // all panels proportionally (top meets top, bottom meets bottom, even
  // when the texts differ in length).
  Alpine.data("reconSync", () => ({
    locked: false,

    sync(event) {
      if (this.locked) return;
      this.locked = true;
      const src = event.target;
      const range = src.scrollHeight - src.clientHeight;
      const ratio = range > 0 ? src.scrollTop / range : 0;
      this.$root.querySelectorAll(".recon-scroll").forEach((el) => {
        if (el === src) return;
        el.scrollTop = ratio * (el.scrollHeight - el.clientHeight);
      });
      requestAnimationFrame(() => {
        this.locked = false;
      });
    },
  }));
});
