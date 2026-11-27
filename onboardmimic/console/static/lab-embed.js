/* Lab owns its UI and recording lifecycle. Never forward controller tokens. */
(async () => {
  const region = document.getElementById('lab-embed-region');
  if (!region || document.getElementById('collection-panel')) return;
  const placeholder = document.getElementById('lab-embed-placeholder');
  const title = document.getElementById('lab-placeholder-title');
  const detail = document.getElementById('lab-placeholder-detail');
  const retry = document.getElementById('lab-embed-retry');
  const open = document.getElementById('lab-embed-open');
  const container = document.getElementById('lab-embed-container');
  let busy = false, frame = null, timer = null, languageObserver = null, messageHandler = null;
  region.hidden = false;
  const show = (heading, text) => {
    placeholder.hidden = false; title.textContent = heading; detail.textContent = text;
  };
  async function connect() {
    if (busy) return;
    busy = true; retry.disabled = true; clearInterval(timer);
    if (messageHandler) window.removeEventListener('message', messageHandler);
    frame?.remove(); frame = null; languageObserver?.disconnect(); open.hidden = true;
    show('正在连接 Lab', '加载数采页面，请稍候。机器人控制可正常使用。');
    try {
      const response = await fetch('/api/console-config', {cache:'no-store', signal:AbortSignal.timeout(4000)});
      if (!response.ok) throw Error('config');
      const config = await response.json();
      if (config.schema_version !== 1 || !config.lab_embed?.enabled) {
        show('Lab 尚未配置', '配置 Lab 页面后，这里将显示数采界面。当前不影响机器人控制。');
        return;
      }
      const url = new URL(config.lab_embed.url);
      if (url.origin === location.origin || (location.protocol === 'https:' && url.protocol !== 'https:')) {
        show('Lab 地址暂不可用', 'Lab 需要独立地址；HTTPS 控制台需要使用 HTTPS Lab。');
        return;
      }
      open.href = url.href; open.hidden = false;
      const current = document.createElement('iframe'); frame = current;
      current.className = 'lab-collection-frame'; current.title = 'OnboardMimic Lab';
      current.setAttribute('aria-label', 'Lab 数采'); current.referrerPolicy = 'no-referrer';
      current.hidden = true;
      const sendLanguage = () => current.contentWindow?.postMessage({
        type:'onboardmimic.lab.language', version:1,
        language:document.documentElement.lang === 'en' ? 'en' : 'zh-CN'
      }, url.origin);
      const nonce = crypto.randomUUID ? crypto.randomUUID() : String(Date.now())+Math.random();
      let lastReady = 0, started = Date.now();
      const probe = () => current.contentWindow?.postMessage({
        type:'onboardmimic.lab.probe', version:1, nonce
      }, url.origin);
      messageHandler = event => {
        const m = event.data;
        if (frame !== current || event.source !== current.contentWindow || event.origin !== url.origin ||
            !m || m.type !== 'onboardmimic.lab.ready' || m.version !== 1 || m.nonce !== nonce) return;
        lastReady = Date.now(); placeholder.hidden = true; current.hidden = false;
      };
      window.addEventListener('message', messageHandler);
      current.addEventListener('load', () => {
        if (frame !== current) return;
        lastReady = 0; started = Date.now(); current.hidden = true;
        show('正在连接 Lab', '等待 Lab 页面确认就绪。机器人控制可正常使用。');
        sendLanguage(); probe();
      });
      current.addEventListener('error', () => {
        if (frame !== current) return;
        lastReady = 0; current.hidden = true;
        show('Lab 未能嵌入', '页面可能尚未启动、被浏览器拦截或加载失败。可重新连接或单独打开 Lab。');
      });
      languageObserver = new MutationObserver(sendLanguage);
      languageObserver.observe(document.documentElement, {attributes:true, attributeFilter:['lang']});
      current.src = url.href; container.appendChild(current);
      timer = setInterval(() => {
        if (frame !== current) return;
        probe();
        if (Date.now()-(lastReady || started)>8000) {
          current.hidden = true;
          show('Lab 未能嵌入', '尚未收到页面就绪确认。可重新连接或单独打开 Lab，机器人控制不受影响。');
        }
      }, 2000);
    } catch (_) {
      show('Lab 暂时无法连接', 'Lab 可能尚未启动或连接中断。启动后点击“重新连接”，机器人控制不受影响。');
    } finally { busy = false; retry.disabled = false; }
  }
  retry.onclick = connect;
  await connect();
})();
