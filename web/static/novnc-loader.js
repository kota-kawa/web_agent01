const globalObject = window;

(async () => {
  try {
    const module = await import('/static/vendor/novnc/core/rfb.js');
    const RFB = module && module.default ? module.default : module;

    if (!RFB) {
      throw new Error('RFB module did not provide a default export');
    }

    globalObject.__NOVNC_RFB__ = RFB;
    globalObject.__NOVNC_READY__ = true;
    document.dispatchEvent(new CustomEvent('novnc:ready'));
  } catch (error) {
    globalObject.__NOVNC_READY__ = false;
    globalObject.__NOVNC_LOAD_FAILED__ = true;
    const detail = error && error.message ? error.message : String(error);
    document.dispatchEvent(
      new CustomEvent('novnc:error', { detail }),
    );
  }
})();
