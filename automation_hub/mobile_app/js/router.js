/* 思忆集 PWA — Hash-based SPA router */
class MobileRouter {
  constructor() {
    this.routes = {};
    this.currentView = null;
    this.currentParams = {};
    this.prevView = null;

    window.addEventListener('hashchange', () => this._handleRoute());
  }

  register(name, viewFactory) {
    this.routes[name] = viewFactory;
  }

  navigate(name, params = {}) {
    const hash = '#' + name + (Object.keys(params).length ? '?' + new URLSearchParams(params).toString() : '');
    if (window.location.hash !== hash) {
      window.location.hash = hash;
    } else {
      this._handleRoute();
    }
  }

  getParams() {
    const idx = window.location.hash.indexOf('?');
    if (idx === -1) return {};
    return Object.fromEntries(new URLSearchParams(window.location.hash.slice(idx + 1)));
  }

  async _handleRoute() {
    const hash = window.location.hash.slice(1).replace(/\?.*$/, '') || 'pairing';
    const params = this.getParams();
    const factory = this.routes[hash];

    if (!factory) {
      this.navigate('dashboard');
      return;
    }

    this.prevView = this.currentView;
    this.currentParams = params;

    const container = document.getElementById('view-container');
    if (!container) return;

    // Destroy previous view
    if (this.currentView && this.currentView.destroy) {
      this.currentView.destroy();
    }

    // Hide old active view pages
    container.querySelectorAll('.view-page').forEach(el => el.classList.remove('active'));

    this.currentView = factory();
    container.innerHTML = '';

    if (this.currentView && this.currentView.render) {
      const el = await this.currentView.render(params);
      if (el) container.appendChild(el);
    }

    // Update nav
    document.querySelectorAll('.nav-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.route === hash);
    });
  }

  start() {
    this._handleRoute();
  }
}

window.router = new MobileRouter();
