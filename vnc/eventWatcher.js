(function(){
  if (window.__ag_ev_registry) return;
  window.__ag_ev_registry = new WeakMap();
  function record(el, type){
    if(!el) return;
    let set = window.__ag_ev_registry.get(el);
    if(!set){ set = new Set(); window.__ag_ev_registry.set(el,set); }
    set.add(type);
  }
  const origAdd = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function(type, listener, opts){
    record(this, type);
    return origAdd.call(this, type, listener, opts);
  };
  const props = [
    'onclick','ondblclick','onmousedown','onmouseup','onmouseover','onmouseout',
    'onmousemove','onmouseenter','onmouseleave','onchange','oninput','onfocus',
    'onblur','onkeydown','onkeyup','onsubmit','onreset','onpointerdown',
    'onpointerup','onpointermove','onpointerover','onpointerout'];
  props.forEach(prop => {
    const desc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop) || {};
    Object.defineProperty(HTMLElement.prototype, prop, {
      get(){ return desc.get ? desc.get.call(this) : this['__'+prop]; },
      set(v){
        if(v != null) record(this, prop.slice(2));
        if(desc.set) desc.set.call(this,v);
        else { this['__'+prop] = v; }
      },
      configurable: true,
      enumerable: desc.enumerable || false
    });
  });
  window.__ag_get_events = el => {
    const set = window.__ag_ev_registry.get(el);
    return set ? Array.from(set) : [];
  };
})();
