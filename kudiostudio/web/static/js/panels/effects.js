// Effects: one kudio function at a time, on the selection or the whole clip.
//
// The controls come from the parameter list the Python side serves, and the
// call underneath is worked out there too, so what this shows is the call
// Apply makes and records.

export async function mount(root, ctx) {
  const { ui, t } = ctx;
  const info = await ctx.call("describe");
  const memory = ctx.memory;
  memory.values ??= {};                       // effect -> its controls' values
  memory.effect ??= info.effects[0];

  const box = ui.section("kudio.effects");
  const top = ui.form();
  top.row("Effect", ui.select(info.effects, { value: memory.effect, onChange: (v) => choose(v) }));
  const paramsHolder = document.createElement("div");
  const extra = document.createElement("div");
  extra.className = "section-body";
  const call = ui.callText();

  const preview = ui.button("♪  Preview", {
    toggle: true,
    tip: "Hear the effect on the selection without applying it.\nLoops, and re-renders as you change the parameters.",
    onClick: (_, on) => (on ? startPreview() : stopPreview()),
  });
  const apply = ui.button("Apply", { primary: true, onClick: () => run("apply") });
  const undo = ui.button("Undo", { onClick: () => ctx.window("undo") });
  const revert = ui.button("Revert to original", { onClick: () => ctx.window("revert") });

  box.body.append(top.el, paramsHolder, extra, call, ui.buttons(preview, apply, undo, revert));
  root.append(box.el, ui.hint("Applies to the selected range when there is a selection, otherwise to the whole clip. Every Apply is undoable — Preview does not change anything at all."));

  // what sits under the parameters of the effect it belongs to: the
  // measured room's readout, and the split's result with its save button
  const irReadout = ui.readout();
  const segments = ui.readout();
  const saveSegments = ui.button("Save segments…", {
    tip: "Write each non-silent segment as its own .wav.",
    onClick: async () => {
      const folder = await ctx.dialogs.choosePath("folder", { title: t("Save segments to") });
      if (folder) show(await ctx.call("save_segments", [folder]));
    },
  });

  let params = null;
  let current = memory.effect;
  let nyquist = null;
  let previewing = false;
  let loadedIr = info.ir?.path ?? null;       // the response the Python side holds
  let timer = 0;

  const values = () => (params ? params.values() : {});

  function choose(name) {
    current = memory.effect = name;
    params = ui.params(info.params[name], { onChange: changed });
    params.set(memory.values[name]);
    if (nyquist) params.setNyquist(nyquist);
    if (name === "measured_room" && loadedIr) params.set({ ir: loadedIr });
    paramsHolder.replaceChildren(params.el);
    extra.replaceChildren();
    if (name === "measured_room") extra.append(irReadout);
    if (name === "split_on_silence") extra.append(ui.buttons(saveSegments), segments);
    preview.disabled = info.not_previewable.includes(name);
    if (previewing && preview.disabled) stopPreview();
    changed();
  }

  async function changed() {
    const v = values();
    memory.values[current] = v;
    if (current === "measured_room") {
      // a response is read on the Python side as soon as it is picked:
      // Preview renders again on every change, and reading the file each
      // time would be slow and could resample it twice
      if (v.ir && v.ir !== loadedIr) show(await ctx.call("load_ir", [v.ir, v.align]));
      else show(await ctx.call("measure_ir", [v.align]));
    }
    refreshCall();
  }

  /** The caption again, a moment after the last change: the controls, or
   *  the clip under them, whose rate caps a cutoff and decides how a loaded
   *  response is read. */
  function refreshCall() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      call.text = await ctx.call("call_text", [current, values()]).catch(() => "");
      if (previewing) startPreview();
    }, 120);
  }

  async function startPreview() {
    const result = await ctx.call("preview", [current, values()]).catch(() => ({ on: false }));
    previewing = Boolean(result.on);
    preview.on = previewing;
  }

  function stopPreview() {
    previewing = false;
    preview.on = false;
    ctx.window("stop_preview");
  }

  async function run(name) {
    show(await ctx.call(name, [current, values()]));
  }

  function show(state) {
    if (!state) return;
    loadedIr = state.ir?.path ?? null;
    if (state.ir) irReadout.show(state.ir.measured, state.ir.doubtful);
    else irReadout.show("—");
    // a file that could not be used leaves the one already loaded in place
    const control = params?.controls.ir?.control;
    if (control && control.value !== (loadedIr ?? "")) control.value = loadedIr ?? "";
    segments.show(state.segments ? t(`${state.segments} segment(s)`) : "—");
    saveSegments.disabled = !state.segments;
  }

  show(info);
  choose(current);

  return {
    state(s) {
      const half = s.has_audio ? s.sr / 2 : null;
      if (half && half !== nyquist) {
        nyquist = half;
        params?.setNyquist(nyquist);
        refreshCall();
      }
      apply.disabled = !s.has_audio;
      undo.disabled = !s.can_undo;
      revert.disabled = !s.can_revert;
    },
    event(e) {
      if (e.info) {
        show(e.info);
        refreshCall();
      }
    },
    preview(on) {
      if (!on) {
        previewing = false;
        preview.on = false;
      }
    },
  };
}
