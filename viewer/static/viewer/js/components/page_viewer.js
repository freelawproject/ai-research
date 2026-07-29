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
    showInImage: true,
    showDisputes: true,
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
        if (typeof saved.inImage === "boolean") {
          this.showInImage = saved.inImage;
        }
        if (typeof saved.disputes === "boolean") {
          this.showDisputes = saved.disputes;
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
          inImage: this.showInImage,
          disputes: this.showDisputes,
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
    toggleInImage() {
      this.showInImage = !this.showInImage;
      this.persist();
    },
    toggleDisputes() {
      this.showDisputes = !this.showDisputes;
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

  // The route-compare selects: picks persist via localStorage. A select
  // rendered with data-restore="true" (its query param was absent, so the
  // server filled a default) restores the last-used combination — and the
  // form resubmits once when that changes what is shown.
  const LS_COMPARE = "extraction.viewer.compareRoutes";
  Alpine.data("comparePicker", () => ({
    init() {
      let saved = null;
      try {
        saved = JSON.parse(localStorage.getItem(LS_COMPARE) || "null");
      } catch (e) {
        /* first visit or corrupted state: keep defaults */
      }
      if (!saved) return;
      let changed = false;
      this.$root
        .querySelectorAll('select[data-restore="true"]')
        .forEach((el) => {
          const value = saved[el.name];
          if (!value || el.value === value) return;
          if ([...el.options].some((o) => o.value === value)) {
            el.value = value;
            changed = true;
          }
        });
      if (changed) this.$root.submit();
    },

    pick() {
      const data = {};
      this.$root.querySelectorAll("select[name]").forEach((el) => {
        data[el.name] = el.value;
      });
      localStorage.setItem(LS_COMPARE, JSON.stringify(data));
      this.$root.submit();
    },
  }));

  // The home page's dataset selector: one sample set shows at a time;
  // the pick persists. A select rendered with data-restore="true" (no
  // ?dataset= param) restores the last-viewed set — and resubmits once
  // when that changes what is shown.
  const LS_DATASET = "extraction.viewer.homeDataset";
  Alpine.data("datasetPicker", () => ({
    init() {
      const el = this.$root.querySelector('select[data-restore="true"]');
      if (!el) return;
      const saved = localStorage.getItem(LS_DATASET);
      if (!saved || el.value === saved) return;
      if ([...el.options].some((o) => o.value === saved)) {
        el.value = saved;
        this.$root.submit();
      }
    },

    pick(event) {
      localStorage.setItem(LS_DATASET, event.target.value);
      this.$root.submit();
    },
  }));

  // The route-compare page image: zoom only (no overlay layers here —
  // those live on the walkthrough).
  const LS_COMPARE_ZOOM = "extraction.viewer.compareZoom";
  Alpine.data("imgZoom", () => ({
    zoom: 100,

    init() {
      const z = parseInt(localStorage.getItem(LS_COMPARE_ZOOM) || "100", 10);
      if (z >= 50 && z <= 400) this.zoom = z;
    },

    persist() {
      localStorage.setItem(LS_COMPARE_ZOOM, String(this.zoom));
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

  // The route-compare highlight layers: plain-text diffs, styling
  // diffs, and low-confidence marks toggle independently; the panel
  // wrapper gets one hide-* class per layer turned off.
  const LS_COMPARE_LAYERS = "extraction.viewer.compareLayers";
  Alpine.data("compareView", () => ({
    showText: true,
    showStyle: true,
    showLowConf: true,

    init() {
      try {
        const saved = JSON.parse(
          localStorage.getItem(LS_COMPARE_LAYERS) || "{}"
        );
        if (typeof saved.text === "boolean") this.showText = saved.text;
        if (typeof saved.style === "boolean") this.showStyle = saved.style;
        if (typeof saved.lowConf === "boolean") {
          this.showLowConf = saved.lowConf;
        }
      } catch (e) {
        /* first visit or corrupted state: keep defaults */
      }
    },

    persist() {
      localStorage.setItem(
        LS_COMPARE_LAYERS,
        JSON.stringify({
          text: this.showText,
          style: this.showStyle,
          lowConf: this.showLowConf,
        })
      );
    },

    toggleText() {
      this.showText = !this.showText;
      this.persist();
    },
    toggleStyle() {
      this.showStyle = !this.showStyle;
      this.persist();
    },
    toggleLowConf() {
      this.showLowConf = !this.showLowConf;
      this.persist();
    },

    get layerClasses() {
      const cls = [];
      if (!this.showText) cls.push("hide-text-diff");
      if (!this.showStyle) cls.push("hide-style-diff");
      if (!this.showLowConf) cls.push("hide-low-conf");
      return cls.join(" ");
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
