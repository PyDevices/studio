// Stand-ins for the browser's file pickers, for tests: a script can't click
// a native dialog. What the app writes lands in window.__files[name] (an
// array of the chunks written); Open returns window.__openPayload.
(() => {
  window.__files = {};
  window.__openPayload = null;
  window.__pickerCalls = 0;
  window.showSaveFilePicker = async (opts) => (window.__pickerCalls++, {
    name: opts.suggestedName,
    createWritable: async () => {
      const parts = [];
      return { write: async (d) => { parts.push(d); }, close: async () => { window.__files[opts.suggestedName] = parts; } };
    },
  });
  window.showOpenFilePicker = async () => [{ getFile: async () => new File([window.__openPayload], "project.ostinato.json") }];
})();
